import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from launcher.core import LauncherError, Task
from launcher.service import Application


class ControlledProcess:
    def __init__(self,pid,command):
        self.pid,self.args = pid,command
        self.code = None
        self.gate = threading.Event()
        self.stdout = self
        self.first = True
        self.terminated = False

    def readline(self):
        if self.first:
            self.first = False
            return ("process-marker-"+str(self.pid)+"\n").encode()
        self.gate.wait(10)
        return b""

    def poll(self): return self.code
    def close(self): pass
    def wait(self):
        self.gate.wait(10)
        return self.code
    def finish(self,code=0):
        self.code = code
        self.gate.set()
    def terminate(self):
        self.terminated = True
        self.finish()


class MultiGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name)/"data")
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Multi","1.20.1")
        self.app.store.update_instance(self.instance["id"],{"ready":True})
        self.account = self.app.accounts.offline("MultiTester")
        self.children = []
        def spawn(command,**kwargs):
            child = ControlledProcess(4200+len(self.children),command)
            self.children.append(child)
            return child
        def command(instance,account,java,runtime=None):
            return [java,"-Djava.library.path="+str(runtime/"natives"),"Main"]
        self.patches = [patch.object(self.app.game,"ensure_java",return_value="java.exe"),
                        patch.object(self.app.game,"profile",return_value={"javaVersion":{"majorVersion":17}}),
                        patch.object(self.app.game,"libraries",return_value=[]),
                        patch.object(self.app.game.skins,"prepare",return_value=None),
                        patch.object(self.app.game,"command",side_effect=command),
                        patch("launcher.game.subprocess.Popen",side_effect=spawn)]
        for item in self.patches: item.start()

    def tearDown(self):
        for child in self.children: child.finish()
        self.wait(lambda:all(p["finalized"] for p in self.app.game.process_status()))
        self.wait(lambda:all(t["status"] not in ("queued","running") for t in self.app.tasks.list()))
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def wait(self,predicate):
        deadline = time.time()+5
        while time.time()<deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail("Multi-game condition timed out")

    def launch(self,instance=None):
        return self.app.game.launch((instance or self.instance)["id"],self.account["id"],Task("launch",None))

    def test_same_instance_two_runs_have_independent_native_paths_logs_stop_and_dismiss(self):
        first,second = self.launch(),self.launch()
        self.assertNotEqual(first["process_id"],second["process_id"])
        self.assertEqual(len([p for p in self.app.game.process_status() if p["running"]]),2)
        self.assertNotEqual(self.children[0].args[1],self.children[1].args[1])
        for result,pid,other in ((first,4200,4201),(second,4201,4200)):
            self.wait(lambda: "process-marker-"+str(pid) in self.app.game.log(self.instance["id"],process_id=result["process_id"]))
            text = self.app.game.log(self.instance["id"],process_id=result["process_id"])
            self.assertNotIn("process-marker-"+str(other),text)
            self.assertTrue(self.app.game.run_info(self.instance["id"],result["process_id"])["shared_directory"])
        self.app.route("POST","/api/processes/stop",{},{"id":first["process_id"]})
        self.wait(lambda:next(p for p in self.app.game.process_status() if p["id"]==first["process_id"])["finalized"])
        self.assertTrue(self.children[0].terminated)
        self.assertFalse(self.children[1].terminated)
        self.assertIsNone(self.children[1].poll())
        self.app.game.dismiss_process(first["process_id"])
        self.assertEqual([p["id"] for p in self.app.game.process_status()],[second["process_id"]])
        with self.assertRaises(LauncherError): self.app.running_check(self.instance["id"])
        with self.assertRaises(LauncherError): self.app.game.dismiss_process(second["process_id"])

    def test_different_instances_can_run_together_and_legacy_stop_targets_only_its_instance(self):
        another = self.app.game.create("Another","1.20.1")
        self.app.store.update_instance(another["id"],{"ready":True})
        first,second = self.launch(),self.launch(another)
        self.assertEqual({p["instance_id"] for p in self.app.game.process_status()},{self.instance["id"],another["id"]})
        self.app.game.stop(self.instance["id"])
        self.assertTrue(self.children[0].terminated)
        self.assertFalse(self.children[1].terminated)
        self.assertFalse(self.app.game.run_info(another["id"],second["process_id"])["shared_directory"])
        self.assertNotIn("process-marker-4201",self.app.game.log(self.instance["id"],process_id=second["process_id"]))
        with self.assertRaises(LauncherError): self.app.game.log(self.instance["id"],process_id="../../config")

    def test_latest_run_log_persists_after_restart_and_shared_logs_are_not_mixed(self):
        first,second = self.launch(),self.launch()
        self.wait(lambda:"process-marker-4201" in self.app.game.log(self.instance["id"]))
        folder = self.app.store.game_dir(self.instance["id"])
        (folder/"logs").mkdir(parents=True)
        (folder/"logs/latest.log").write_text("SHARED-GAME-LOG-MUST-NOT-BE-SENT",encoding="utf-8")
        text = self.app.diagnostics._logs(self.instance["id"],process_id=first["process_id"])
        self.assertIn("process-marker-4200",text)
        self.assertNotIn("process-marker-4201",text)
        self.assertNotIn("SHARED-GAME-LOG",text)
        restarted = Application(self.app.store.root)
        self.assertIn("process-marker-4201",restarted.game.log(self.instance["id"]))
        self.assertNotIn("process-marker-4200",restarted.game.log(self.instance["id"]))

    def test_two_crashes_schedule_distinct_analysis_with_their_own_logs(self):
        first,second = self.launch(),self.launch()
        self.wait(lambda:all(Path(r["log_path"]).exists() for r in (first,second)))
        def analyze(instance_id,kind,*args,**kwargs):
            process_id = kwargs["process_id"]
            return {"status":"risk","kind":kind,"instance_id":instance_id,"process_id":process_id,
                    "cause":self.app.game.log(instance_id,process_id=process_id),"suggestion":"fixture"}
        with patch.object(self.app.diagnostics,"configured",return_value=True),patch.object(self.app.diagnostics,"analyze",side_effect=analyze) as check:
            self.children[0].finish(1)
            self.children[1].finish(2)
            self.wait(lambda:all(p["finalized"] for p in self.app.game.process_status()))
            self.wait(lambda:len(self.app.tasks.list())==2 and all(t["status"]=="completed" for t in self.app.tasks.list()))
            self.assertEqual(check.call_count,2)
            results = [t["result"]["diagnostic_report"] for t in self.app.tasks.list()]
            self.assertEqual({r["process_id"] for r in results},{first["process_id"],second["process_id"]})
            for result in results:
                marker = "process-marker-4200" if result["process_id"]==first["process_id"] else "process-marker-4201"
                self.assertIn(marker,result["cause"])

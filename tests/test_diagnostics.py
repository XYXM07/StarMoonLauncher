import io
import json
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".build-env"))
from launcher.core import Cancelled, LauncherError, Task
from launcher.diagnostics import endpoint, satisfies, parse_answer
from launcher.service import Application


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root/"data")
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Test","1.20.1","fabric","0.16.0")
        self.mods = self.app.store.game_dir(self.instance["id"])/"mods"
        self.mods.mkdir()
        self.requests = []
        self.mode = "normal"
        self.final_response = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path,dict(self.headers),body))
                if owner.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location",owner.url+"/stolen")
                    self.end_headers()
                    return
                if owner.mode == "http_error":
                    self.send_response(401); self.end_headers(); return
                if owner.mode in ("fallback","combined_fallback") and "max_tokens" in body:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b'max_tokens unsupported; use max_completion_tokens')
                    return
                if owner.mode in ("json_unsupported","combined_fallback") and "response_format" in body:
                    self.send_response(422); self.end_headers()
                    self.wfile.write(b'response_format json_object is not supported')
                    return
                content = "not JSON" if owner.mode == "malformed" else json.dumps({"status":"ok","cause":"未发现明确问题","suggestion":"建议保留日志并进行实际启动验证。"},ensure_ascii=False)
                if owner.mode == "long":
                    content = json.dumps({"status":"risk","cause":"原因"*200,"suggestion":"建议"*300})
                response = {"choices":[{"message":{"content":content,"reasoning_content":"NEVER DISPLAY THIS"}}]}
                budget = body.get("max_tokens",body.get("max_completion_tokens",0))
                if owner.mode == "always_truncated" or (owner.mode == "truncated_once" and budget<4096):
                    response = {"choices":[{"finish_reason":"length","message":{"content":None,"reasoning_content":"PRIVATE THOUGHTS"}}]}
                if owner.mode == "empty_final":
                    response = {"choices":[{"finish_reason":"stop","message":{"content":None,"reasoning_content":"PRIVATE THOUGHTS"}}]}
                payload = json.dumps(owner.final_response or response).encode()
                self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(payload)
        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.url = "http://127.0.0.1:"+str(self.server.server_port)+"/v1"
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.app.config_update({"ai_base_url":self.url,"ai_model":"fixture-model"})

    def tearDown(self):
        deadline = time.time()+5
        while any(t["status"] in ("queued","running") for t in self.app.tasks.list()) and time.time()<deadline:
            time.sleep(.02)
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def jar(self,name,data,extra=None):
        with zipfile.ZipFile(self.mods/name,"w") as z:
            z.writestr("fabric.mod.json",json.dumps(data))
            for path,content in (extra or {}).items(): z.writestr(path,content)

    def wait_task(self,identifier):
        deadline = time.time()+5
        while time.time()<deadline:
            item = self.app.tasks.tasks[identifier].public()
            if item["status"] not in ("queued","running"): return item
            time.sleep(.02)
        self.fail("Task timeout")

    def test_mod_missing_duplicate_breaks_disabled_and_version_constraints(self):
        self.jar("a.jar",{"id":"mod_a","version":"1.0","depends":{"absent":">=2","minecraft":">=1.21"},"breaks":{"mod_b":"*"}})
        self.jar("b.jar",{"id":"mod_b","version":"1.0"})
        self.jar("b2.jar",{"id":"mod_b","version":"2.0"})
        self.jar("disabled.jar.disabled",{"id":"absent","version":"2.0"})
        data,_ = self.app.diagnostics.snapshot(self.instance["id"])
        text = "\n".join(data["local_findings"])
        self.assertIn("缺失前置 absent",text)
        self.assertIn("重复模组 ID",text)
        self.assertIn("不兼容",text)
        self.assertIn("当前为 1.20.1",text)
        self.assertEqual(data["mod_count"],3)

    def test_nested_provider_and_alias_are_not_false_missing_or_duplicate(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload,"w") as z:
            z.writestr("fabric.mod.json",json.dumps({"id":"library","version":"2.0","provides":["alias"]}))
        child = payload.getvalue()
        self.jar("a.jar",{"id":"a","version":"1","depends":{"alias":">=1"},"jars":[{"file":"META-INF/jars/library.jar"}]},{"META-INF/jars/library.jar":child})
        self.jar("b.jar",{"id":"b","version":"1","jars":[{"file":"META-INF/jars/library.jar"}]},{"META-INF/jars/library.jar":child})
        data,_ = self.app.diagnostics.snapshot(self.instance["id"])
        self.assertFalse(data["local_findings"],data["local_findings"])
        self.assertEqual(len(data["mods"]),4)

    def test_forge_neoforge_and_quilt_metadata(self):
        for filename,metadata in (("forge.jar","META-INF/mods.toml"),("neo.jar","META-INF/neoforge.mods.toml")):
            identifier = filename.split(".")[0]
            text = '[[mods]]\nmodId="'+identifier+'"\nversion="1.0"\n[[dependencies.'+identifier+']]\nmodId="missing_'+identifier+'"\nmandatory=true\nversionRange="[2.0,)"\nside="BOTH"\n'
            with zipfile.ZipFile(self.mods/filename,"w") as z: z.writestr(metadata,text)
        with zipfile.ZipFile(self.mods/"quilt.jar","w") as z:
            z.writestr("quilt.mod.json",json.dumps({"quilt_loader":{"id":"quilt","version":"1","depends":[{"id":"missing_quilt","versions":">=1"},{"id":"optional","optional":True}]}}))
        for loader, identifier in (("forge", "forge"), ("neoforge", "neo"), ("quilt", "quilt")):
            with self.subTest(loader=loader):
                self.app.store.update_instance(self.instance["id"], {"loader": loader, "game_version": "1.21"})
                data,_ = self.app.diagnostics.snapshot(self.instance["id"])
                text = " ".join(data["local_findings"])
                self.assertIn("missing_" + identifier, text)
                for other in {"forge", "neo", "quilt"} - {identifier}:
                    self.assertNotIn("missing_" + other, text)
                self.assertNotIn("optional",text)

    def test_http_contract_redaction_bounded_response_and_no_reasoning(self):
        self.app.accounts.offline("PrivacyTester")
        key = "unit-private-api-key-123456"
        self.app.config_update({"ai_api_key":key})
        game = self.app.store.game_dir(self.instance["id"])
        (game/"logs").mkdir()
        log = 'PrivacyTester test@example.com 192.168.1.23 C:\\Users\\PrivatePerson\\game accessToken=TOPSECRET Authorization: Bearer SECRET2 '+key+'\n[CHAT] private conversation\njava.lang.RuntimeException: fixture\n'
        (game/"logs/latest.log").write_text(log,encoding="utf-8")
        report = self.app.diagnostics.analyze(self.instance["id"],"crash")
        sent = json.dumps(self.requests[-1],ensure_ascii=False)
        prompt = self.requests[-1][2]["messages"][-1]["content"]
        for secret in ("PrivacyTester","test@example.com","192.168.1.23","PrivatePerson","TOPSECRET","SECRET2",key,"private conversation"):
            self.assertNotIn(secret,prompt)
        self.assertIn("RuntimeException",prompt)
        self.assertEqual(self.requests[-1][0],"/v1/chat/completions")
        self.assertEqual(self.requests[-1][2]["model"],"fixture-model")
        self.assertEqual(self.requests[-1][1]["Authorization"],"Bearer "+key)
        self.assertNotIn("reasoning",json.dumps(report))
        self.assertEqual(self.app.diagnostics.latest(self.instance["id"],"crash")["cause"],report["cause"])
        self.mode = "long"
        report = self.app.diagnostics.analyze(self.instance["id"])
        self.assertLessEqual(len(report["cause"]),120)
        self.assertLessEqual(len(report["suggestion"]),220)

    def test_secret_encrypted_not_public_preserved_on_blank_and_clearable(self):
        key = "private-key-for-dpapi-test"
        self.app.config_update({"ai_api_key":key})
        self.assertNotIn(key,(self.app.store.root/"ai-credentials.json").read_text())
        self.assertNotIn(key,json.dumps(self.app.state()))
        self.assertNotIn("ai_api_key",self.app.store.config())
        self.assertTrue(self.app.state()["config"]["ai_key_configured"])
        self.app.config_update({"ai_api_key":""})
        self.assertEqual(self.app.diagnostics.key(),key)
        restarted = Application(self.app.store.root)
        self.assertEqual(restarted.diagnostics.key(),key)
        self.app.config_update({"ai_clear_key":True})
        self.assertEqual(self.app.diagnostics.key(),"")
        self.assertFalse(self.app.state()["config"]["ai_key_configured"])

    def test_local_issue_forces_risk_and_cache_invalidates_when_mods_change(self):
        self.jar("a.jar",{"id":"a","version":"1","depends":{"missing":"*"}})
        report = self.app.diagnostics.analyze(self.instance["id"])
        self.assertEqual(report["status"],"risk")
        self.assertIn("missing",report["cause"])
        reused = self.app.diagnostics.analyze(self.instance["id"],reuse=True)
        self.assertTrue(reused["cached"])
        self.assertEqual(len(self.requests),1)
        self.jar("b.jar",{"id":"missing","version":"1"})
        report = self.app.diagnostics.analyze(self.instance["id"],reuse=True)
        self.assertEqual(len(self.requests),2)
        self.assertEqual(report["status"],"ok")

    def test_preflight_blocks_risk_failure_and_cancel_but_explicit_continue_launches(self):
        self.app.config_update({"ai_auto_preflight":True})
        with patch.object(self.app.game,"launch",return_value={"pid":123}) as launch:
            with patch.object(self.app.diagnostics,"analyze",return_value={"status":"risk","cause":"Missing","suggestion":"Install"}):
                result = self.app.start_game(self.instance["id"],"account",Task("Launch",None))
                self.assertTrue(result["launch_blocked"]); launch.assert_not_called()
                self.app.start_game(self.instance["id"],"account",Task("Launch",None),True)
                launch.assert_called_once()
            launch.reset_mock()
            with patch.object(self.app.diagnostics,"analyze",side_effect=LauncherError("fixture offline")):
                result = self.app.start_game(self.instance["id"],"account",Task("Launch",None))
                self.assertEqual(result["diagnostic_report"]["source"],"error"); launch.assert_not_called()
            with patch.object(self.app.diagnostics,"analyze",side_effect=Cancelled("cancel")):
                with self.assertRaises(Cancelled): self.app.start_game(self.instance["id"],"account",Task("Launch",None))
            with patch.object(self.app.diagnostics,"analyze",return_value={"status":"ok"}):
                self.app.start_game(self.instance["id"],"account",Task("Launch",None)); launch.assert_called_once()

    def test_crash_auto_trigger_deduplicates_and_skips_normal_stop_and_disabled(self):
        self.app.on_game_exit(self.instance["id"],10,0)
        self.app.on_game_exit(self.instance["id"],11,1,True)
        self.app.config_update({"ai_crash_analysis":False})
        self.app.on_game_exit(self.instance["id"],12,1)
        self.assertFalse(self.app.tasks.list())
        self.app.config_update({"ai_crash_analysis":True})
        self.app.on_game_exit(self.instance["id"],13,1)
        self.app.on_game_exit(self.instance["id"],13,1)
        self.assertEqual(len(self.app.tasks.list()),1)
        job = self.app.tasks.list()[0]["id"]
        result = self.wait_task(job)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["result"]["diagnostic_report"]["kind"],"crash")

    def test_network_errors_redirects_malformed_output_and_token_parameter_fallback(self):
        for mode in ("redirect","http_error","malformed"):
            self.mode = mode
            with self.assertRaises(LauncherError): self.app.diagnostics.analyze(self.instance["id"])
        self.assertNotIn("/stolen",[req[0] for req in self.requests])
        self.mode = "fallback"
        report = self.app.diagnostics.analyze(self.instance["id"])
        self.assertEqual(report["status"],"ok")
        self.assertIn("max_completion_tokens",self.requests[-1][2])

    def test_start_failure_triggers_analysis_and_cancellation_does_not(self):
        with patch.object(self.app.game,"launch",side_effect=LauncherError("startup fixture failure")):
            with self.assertRaises(LauncherError):
                self.app.start_game(self.instance["id"],"account",Task("launch",None))
        self.assertEqual(len(self.app.tasks.list()),1)
        job = self.wait_task(self.app.tasks.list()[0]["id"])
        self.assertEqual(job["status"],"completed")
        prompt = self.requests[-1][2]["messages"][-1]["content"]
        self.assertIn("startup fixture failure",prompt)
        with patch.object(self.app.game,"launch",side_effect=Cancelled("cancel")):
            with self.assertRaises(Cancelled):
                self.app.start_game(self.instance["id"],"account",Task("launch",None))
        self.assertEqual(len(self.app.tasks.list()),1)

    def test_truncated_large_mod_payload_is_valid_json_and_never_auto_ok(self):
        data = {"kind":"preflight","mods":[{"id":"large","depends":{"entry":"x"*40000}}],"local_findings":[]}
        report = self.app.diagnostics._request(data)
        sent = json.loads(self.requests[-1][2]["messages"][-1]["content"])
        self.assertTrue(sent["scan_truncated"])
        self.assertEqual(report["status"],"unknown")
        self.assertLessEqual(len(report["cause"]),80)

    def test_corrupt_optional_key_file_does_not_break_launcher_state(self):
        self.app.diagnostics.key_path.write_text("not-json",encoding="utf-8")
        self.assertFalse(self.app.state()["config"]["ai_key_configured"])
        with self.assertRaises(LauncherError): self.app.diagnostics.key()
        self.app.config_update({"ai_clear_key":True})
        self.assertEqual(self.app.diagnostics.key(),"")

    def test_real_process_monitor_callback_schedules_crash_diagnostics(self):
        self.app.store.update_instance(self.instance["id"],{"ready":True})
        account = self.app.accounts.offline("MonitorTest")
        process = Mock()
        process.pid = 1234
        process.stdout = io.BytesIO(b"java.lang.RuntimeException: fixture-fatal\n")
        process.poll.return_value = 1
        process.wait.return_value = 1
        with patch.object(self.app.game,"ensure_java",return_value="java.exe"), patch.object(self.app.game,"profile",return_value={"javaVersion":{"majorVersion":17}}), patch.object(self.app.game,"command",return_value=["java.exe","Main"]), patch("launcher.launch_options.java_architecture", return_value="x64"), patch("launcher.game.subprocess.Popen",return_value=process):
            self.app.game.launch(self.instance["id"],account["id"],Task("launch",None))
            deadline = time.time()+5
            while not self.app.tasks.list() and time.time()<deadline: time.sleep(.02)
            self.assertTrue(self.app.tasks.list())
            job = self.wait_task(self.app.tasks.list()[0]["id"])
            self.assertEqual(job["status"],"completed")
        prompt = self.requests[-1][2]["messages"][-1]["content"]
        self.assertIn("fixture-fatal",prompt)
        self.assertEqual(job["result"]["diagnostic_report"]["kind"],"crash")

    def test_answer_formats_json_fences_chinese_headings_aliases_and_text_blocks(self):
        cases = [
            ({"content":'说明：\n```json\n{"status":"risk","cause":"Missing library","suggestion":"Install library"}\n```\n以上为建议。'},"Missing library","risk"),
            ({"content":json.dumps({"诊断结果":{"原因":"缺少前置","解决方案":["补齐前置","匹配加载器版本"]}})},"缺少前置","unknown"),
            ({"content":"**原因**：缺少前置\n\n**建议解决方法**：安装匹配的前置模组。"},"缺少前置","unknown"),
            ({"content":"Cause: Missing library\nSuggestion: Install library"},"Missing library","unknown"),
            ({"content":[{"type":"reasoning","text":"PRIVATE"},{"type":"text","text":'{"reason":"Missing library","solution":"Install library"}'}]},"Missing library","unknown"),
            ({"content":"<think>PRIVATE</think>\n{\"status\":\"ok\",\"cause\":\"No known issue\",\"suggestion\":\"Keep logs\"}"},"No known issue","ok"),
            ({"content":"PRIVATE</think>\n{\"cause\":\"Missing library\",\"suggestion\":\"Install library\"}"},"Missing library","unknown"),
            ({"content":{"cause":"Missing library","suggestion":"Install library"}},"Missing library","unknown"),
            ({"parsed":{"status":"ok","cause":"No known issue","suggestion":"Keep logs"}},"No known issue","ok"),
            ({"content":"Missing library\n\nInstall the required library."},"Missing library","unknown"),
        ]
        for message,cause,status in cases:
            with self.subTest(message=message):
                self.final_response = {"choices":[{"message":dict(message,reasoning_content="NEVER DISPLAY THIS")}]}
                report = self.app.diagnostics.analyze(self.instance["id"])
                self.assertEqual(report["cause"],cause)
                self.assertEqual(report["status"],status)
                self.assertNotIn("PRIVATE",json.dumps(report))
                self.assertTrue(report["suggestion"])
                self.assertLessEqual(len(report["cause"]),80)
                self.assertLessEqual(len(report["suggestion"]),140)

    def test_json_mode_and_token_parameter_fallback_are_bounded(self):
        for mode,count in (("json_unsupported",2),("combined_fallback",3)):
            self.mode = mode
            self.requests.clear()
            report = self.app.diagnostics.analyze(self.instance["id"])
            self.assertEqual(report["status"],"ok")
            self.assertEqual(len(self.requests),count)
            self.assertIn("response_format",self.requests[0][2])
            self.assertNotIn("response_format",self.requests[-1][2])

    def test_truncated_answer_retries_once_and_empty_final_never_uses_reasoning(self):
        self.mode = "truncated_once"
        report = self.app.diagnostics.analyze(self.instance["id"])
        self.assertEqual(report["status"],"ok")
        self.assertEqual([r[2]["max_tokens"] for r in self.requests],[2048,4096])
        self.mode = "always_truncated"
        self.requests.clear()
        with self.assertRaisesRegex(LauncherError,"截断"):
            self.app.diagnostics.analyze(self.instance["id"])
        self.assertEqual(len(self.requests),2)
        self.mode = "empty_final"
        self.requests.clear()
        with self.assertRaisesRegex(LauncherError,"最终回答"):
            self.app.diagnostics.analyze(self.instance["id"])
        self.assertEqual(len(self.requests),1)
        for message in ({"content":None,"reasoning_content":'{"cause":"PRIVATE","suggestion":"PRIVATE"}'},
                        {"content":"<think>PRIVATE 原因：secret\n建议：secret"}):
            with self.assertRaises(ValueError): parse_answer(message)

    def test_refusal_invalid_response_and_ambiguous_text_never_auto_approve(self):
        for choice in ({"message":{"refusal":"PRIVATE","content":None}},
                       {"message":{"content":"incomplete"},"finish_reason":"content_filter"},
                       {"message":[]}, {"message":{"content":"incomplete"}}):
            self.final_response = {"choices":[choice]}
            with self.assertRaises(LauncherError) as caught:
                self.app.diagnostics.analyze(self.instance["id"])
            self.assertNotIn("PRIVATE",str(caught.exception))
        self.final_response = {"choices":[{"message":{"content":"原因：可能存在冲突\n建议：先检查日志和前置。"}}]}
        self.app.config_update({"ai_auto_preflight":True})
        with patch.object(self.app.game,"launch") as launch:
            result = self.app.start_game(self.instance["id"],"account",Task("launch",None))
            self.assertTrue(result["launch_blocked"])
            launch.assert_not_called()

    def test_url_and_version_constraints_are_conservative(self):
        for invalid in ("http://example.com/v1","https://a:secret@example.com/v1","https://example.com/v1?key=secret"):
            with self.assertRaises(LauncherError): endpoint(invalid)
        self.assertEqual(endpoint(self.url),self.url+"/chat/completions")
        for version,rule,expected in (("1.2","[1.0,2)",True),("2.0","[1.0,2)",False),("1.20.1",">=1.21",False),("1.2.4","1.2.*",True),("1.2.3-beta","*",True),("1.2","^1.0",None)):
            self.assertIs(satisfies(version,rule),expected)


if __name__ == "__main__": unittest.main()

"""Task prompts are cancellable, nonce-bound and do not hold the UI thread."""
import tempfile
import time
import unittest

from launcher.core import Cancelled, LauncherError, Task, TaskManager
from launcher.service import Application
from unittest.mock import patch


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Task prompt timed out")
        time.sleep(.01)


class RecoveryInteractionTests(unittest.TestCase):
    def setUp(self):
        self.manager = TaskManager()

    def tearDown(self):
        for task in self.manager.tasks.values():
            task.cancel.set()
        until(lambda: all(task.finished for task in self.manager.tasks.values()))

    def start(self, operation=None):
        result = self.manager.submit("Pack", operation or (lambda task: task.request_choice("ai_recovery_confirm",
            {"filename": "example.jar"}, [{"id": "search"}])))
        task = self.manager.tasks[result["task_id"]]
        until(lambda: task.interaction is not None)
        return task

    def test_task_waits_for_matching_explicit_response(self):
        task = self.start()
        self.assertIsNone(task.result)
        self.assertEqual(task.public()["message"], "等待确认 AI 补下载")
        self.manager.respond(task.id, task.interaction["id"], "search")
        until(lambda: task.finished)
        self.assertEqual(task.result, "search")
        self.assertEqual(task.status, "completed")
        self.assertIsNone(task.public()["interaction"])

    def test_unknown_stale_and_invalid_choices_cannot_resume_task(self):
        task = self.start()
        request = task.interaction["id"]
        for identifier, nonce, choice in (("unknown", request, "search"), (task.id, "old", "search"), (task.id, request, "made-up")):
            with self.assertRaises(LauncherError):
                self.manager.respond(identifier, nonce, choice)
        self.assertFalse(task.finished)
        self.manager.respond(task.id, request, "search")
        until(lambda: task.finished)
        with self.assertRaises(LauncherError):
            self.manager.respond(task.id, request, "search")

    def test_cancel_response_cancels_installation(self):
        task = self.start()
        self.manager.respond(task.id, task.interaction["id"], "cancel")
        until(lambda: task.finished)
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(task.public()["interaction"])

    def test_activity_cancel_unblocks_pending_prompt(self):
        task = self.start()
        self.manager.cancel_task(task.id)
        until(lambda: task.finished)
        self.assertEqual(task.status, "cancelled")

    def test_public_snapshot_does_not_mutate_pending_options(self):
        task = self.start()
        public = task.public()
        public["interaction"]["options"][0]["id"] = "injected"
        self.assertEqual(task.interaction["options"][0]["id"], "search")

    def test_next_file_requires_a_new_prompt_id(self):
        def operation(task):
            task.request_choice("ai_recovery_confirm", {}, [{"id": "search"}])
            return task.request_choice("ai_recovery_select", {}, [{"id": "file-one"}])
        task = self.start(operation)
        first = task.interaction["id"]
        self.manager.respond(task.id, first, "search")
        until(lambda: task.interaction and task.interaction["id"] != first)
        with self.assertRaises(LauncherError):
            self.manager.respond(task.id, first, "search")
        self.manager.respond(task.id, task.interaction["id"], "file-one")
        until(lambda: task.finished)
        self.assertEqual(task.result, "file-one")

    def test_sibling_download_failure_aborts_pending_prompt_without_deadlock(self):
        with tempfile.TemporaryDirectory() as root:
            app = Application(root)
            jobs = [{"path": "mods/example.jar", "url": "https://original.invalid/mod.jar", "target": app.store.root / "mod.jar", "sha1": "a" * 40, "size": 1},
                    {"path": "config/fatal.txt", "url": "https://original.invalid/fatal.txt", "target": app.store.root / "fatal.txt"}]
            def download(url, *args, **kwargs):
                if url.endswith("fatal.txt"):
                    until(lambda: any(task.interaction for task in self.manager.tasks.values()))
                    raise LauncherError("sibling failed")
                raise LauncherError("mod failed")
            with patch.object(app.net, "download", side_effect=download), patch.object(app.content.ai_downloads, "candidates") as search:
                result = self.manager.submit("Pack", lambda task: app.game.download_batch(jobs, task, "Pack",
                    fallback=lambda job, transfer: app.content.ai_downloads.recover(job, {}, transfer)))
                task = self.manager.tasks[result["task_id"]]
                until(lambda: task.finished)
            self.assertEqual(task.status, "failed")
            self.assertIn("sibling failed", task.error)
            self.assertIsNone(task.public()["interaction"])
            search.assert_not_called()


if __name__ == "__main__":
    unittest.main()

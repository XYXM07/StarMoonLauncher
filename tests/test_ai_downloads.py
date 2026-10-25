"""Grounded model search, exact-file recovery, cancellation and pack routing."""
import copy
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".build-env"))
from launcher.ai_downloads import MR
from launcher.core import Cancelled, LauncherError, Task
from launcher.github_source import API
from launcher.progress import DownloadProgress
from launcher.service import Application


def mod_jar():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps({"id": "example", "name": "Example", "version": "1.0", "depends": {"minecraft": "1.21.1"}}))
    return stream.getvalue()


class AIDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root / "data")
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("AI fixture", "1.21.1", "fabric", "0.16.5")
        self.ai = self.app.content.ai_downloads
        self.mcmod_patch = patch.object(self.ai.mcmod, "search", return_value=[])
        self.mcmod_patch.start()
        self.task = Task("AI fixture", lambda t: None)
        self.choice_patch = patch.object(self.task, "request_choice", side_effect=lambda kind, data, options:
            "search" if kind == "ai_recovery_confirm" else options[0]["id"])
        self.choice_patch.start()
        self.payload = mod_jar()
        self.url = "https://cdn.modrinth.com/data/Example/versions/one/example.jar"
        self.job = {"path": "mods/example.jar", "target": self.root / "game/mods/example.jar", "size": len(self.payload),
                    "sha1": hashlib.sha1(self.payload).hexdigest(), "sha512": hashlib.sha512(self.payload).hexdigest()}
        self.repo = {"full_name": "Author/Example", "name": "Example", "description": "Minecraft Example mod", "owner": {"login": "Author"}}
        self.candidate = {"id": "fixture", "url": self.url, "filename": "example.jar", "version": "1.0", "provider": "Modrinth"}

    def tearDown(self):
        self.choice_patch.stop()
        self.mcmod_patch.stop()
        self.temp.cleanup()

    def enable(self):
        self.app.config_update({"ai_github_search": True, "ai_pack_download_fallback": True})

    def fake_download(self, url, target, task=None, **kwargs):
        self.assertEqual(url, self.url)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payload)
        task.reset_download()
        task.add_bytes(len(self.payload))
        task.complete_download(len(self.payload))

    def test_recovery_defaults_on_and_explicit_opt_out_persists(self):
        self.assertFalse(self.app.store.config()["ai_github_search"])
        self.assertTrue(self.app.store.config()["ai_pack_download_fallback"])
        self.app.config_update({"ai_github_search": True, "ai_pack_download_fallback": False})
        reopened = Application(self.root / "data")
        self.assertTrue(reopened.store.config()["ai_github_search"])
        self.assertFalse(reopened.store.config()["ai_pack_download_fallback"])

    def test_name_search_off_does_not_call_model_and_direct_repository_still_works(self):
        with patch.object(self.ai, "ask") as ask, patch.object(self.app.content.github, "search", return_value={"hits": []}) as direct:
            with self.assertRaisesRegex(LauncherError, "开启 AI"):
                self.app.content.search("示例模组", provider="github")
            self.app.content.search("Author/Example", provider="github")
            direct.assert_called_once_with("Author/Example", 0)
            ask.assert_not_called()

    def test_invalid_urls_never_become_prompts(self):
        self.enable()
        with patch.object(self.ai, "ask") as ask:
            with self.assertRaises(LauncherError):
                self.app.content.search("https://evil.invalid/mod", provider="github")
            ask.assert_not_called()

    def test_model_can_select_only_a_real_repository_candidate(self):
        self.enable()
        def get(url):
            if "/search/repositories?" in url:
                return {"items": [self.repo]}
            if url == API + "Author/Example":
                return self.repo
            raise AssertionError(url)
        with patch.object(self.ai, "ask", side_effect=[{"query": "Example", "repositories": []},
                          {"repositories": ["Attacker/Fake", "Author/Example"]}]), patch.object(self.app.content.providers, "get", side_effect=get):
            result = self.app.content.search("示例模组", provider="github")
        self.assertEqual([item["project_id"] for item in result["hits"]], ["Author/Example"])

    def test_model_without_matching_real_candidates_fails(self):
        with patch.object(self.ai, "ask", side_effect=[{"query": "Example"}, {"repositories": ["Made/Up"]}]), \
             patch.object(self.app.content.providers, "get", return_value={"items": [self.repo]}):
            with self.assertRaisesRegex(LauncherError, "未找到可核实"):
                self.ai.discover("示例")

    def test_recovery_off_and_nonmod_files_do_not_call_ai(self):
        self.app.config_update({"ai_pack_download_fallback": False})
        with patch.object(self.ai, "candidates") as search:
            self.assertFalse(self.ai.recover(self.job, {}, self.task))
            self.enable()
            self.assertFalse(self.ai.recover(dict(self.job, path="config/test.jar"), {}, self.task))
            self.assertFalse(self.ai.recover(dict(self.job, path="mods/config.json"), {}, self.task))
            search.assert_not_called()

    def test_recovery_rejects_missing_hash_before_search(self):
        self.enable()
        with patch.object(self.ai, "candidates") as search:
            with self.assertRaisesRegex(LauncherError, "校验哈希"):
                self.ai.recover({"path": "mods/example.jar", "target": self.job["target"]}, {}, self.task)
            search.assert_not_called()

    def test_exact_modrinth_hash_candidate_and_model_cannot_inject_url(self):
        data = {"files": [{"url": self.url, "filename": "example.jar", "hashes": {"sha512": self.job["sha512"]}}]}
        with patch.object(self.app.content.providers, "get", return_value=data) as get, \
             patch.object(self.ai, "ask", return_value={"urls": ["https://evil.invalid/fake.jar", self.url, self.url]}):
            urls = self.ai.candidates(self.job, {}, self.task)
        self.assertEqual(urls, [self.url])
        self.assertTrue(get.call_args.args[0].startswith(MR + "/version_file/"))

    def test_github_filename_candidate_is_verified_against_release_metadata(self):
        url = "https://github.com/Author/Example/releases/download/v1/example.jar"
        asset = {"name": "example.jar", "id": 1, "size": len(self.payload), "browser_download_url": url}
        with patch.object(self.ai, "discover", return_value=["Author/Example"]), \
             patch.object(self.ai, "ask", return_value={"urls": [url, "https://github.com/Other/Repo/releases/download/v1/fake.jar"]}), \
             patch.object(self.app.content.providers, "get", side_effect=[LauncherError("404"), LauncherError("404"), [{"assets": [asset]}]]):
            self.assertEqual(self.ai.candidates(self.job, {}, self.task), [url])

    def test_successful_recovery_commits_only_after_verification(self):
        self.enable()
        transfer = DownloadProgress(self.task, [self.job], "Pack", 20, 90).file(0)
        with patch.object(self.ai, "candidates", return_value=[self.candidate]), \
             patch.object(self.app.net, "download", side_effect=self.fake_download):
            self.assertTrue(self.ai.recover(self.job, {"game_version": "1.21.1"}, transfer))
        self.assertEqual(self.job["target"].read_bytes(), self.payload)
        self.assertEqual(self.task.download["files"], 1)
        self.assertFalse(list((self.app.store.root / "cache/ai-fallback").iterdir()))

    def test_hash_mismatch_does_not_replace_existing_file_or_complete_progress(self):
        self.enable()
        self.job["target"].parent.mkdir(parents=True)
        self.job["target"].write_bytes(b"keep old mod")
        bad = dict(self.job, sha1="0" * 40)
        with patch.object(self.ai, "candidates", return_value=[self.candidate]), \
             patch.object(self.app.net, "download", side_effect=self.fake_download):
            with self.assertRaises(LauncherError):
                self.ai.recover(bad, {}, self.task)
        self.assertEqual(self.job["target"].read_bytes(), b"keep old mod")
        self.assertEqual(self.task.download["files"], 0)

    def test_nonmod_jar_rejected_even_with_matching_hashes(self):
        self.enable()
        self.payload = b"not a Minecraft mod"
        job = dict(self.job, size=len(self.payload), sha1=hashlib.sha1(self.payload).hexdigest(), sha512=hashlib.sha512(self.payload).hexdigest())
        with patch.object(self.ai, "candidates", return_value=[self.candidate]), patch.object(self.app.net, "download", side_effect=self.fake_download):
            with self.assertRaises(LauncherError):
                self.ai.recover(job, {}, self.task)
        self.assertFalse(job["target"].exists())

    def test_cancelled_search_never_downloads_and_releases_lock(self):
        self.enable()
        with patch.object(self.ai, "candidates", side_effect=Cancelled("cancel")), patch.object(self.app.net, "download") as download:
            with self.assertRaises(Cancelled):
                self.ai.recover(self.job, {}, self.task)
            download.assert_not_called()
        self.assertFalse(self.ai.lock.locked())

    def test_batch_tries_all_original_mirrors_before_ai(self):
        job = dict(self.job, url="https://original.invalid/mod.jar", urls=["https://one.invalid/mod.jar", "https://two.invalid/mod.jar"])
        events = []
        def download(url, *args, **kwargs):
            events.append(url)
            raise LauncherError("404")
        def fallback(item, task):
            events.append("AI")
            return True
        with patch.object(self.app.net, "download", side_effect=download):
            self.app.game.download_batch([job], self.task, "Pack", fallback=fallback)
        self.assertEqual(events, job["urls"] + ["AI"])

    def test_batch_cancellation_never_triggers_fallback(self):
        with patch.object(self.app.net, "download", side_effect=Cancelled("cancel")), \
             patch.object(self.ai, "recover") as fallback:
            with self.assertRaises(Cancelled):
                self.app.game.download_batch([dict(self.job, url=self.url)], self.task, "Pack", fallback=fallback)
            fallback.assert_not_called()

    def test_curseforge_author_download_restriction_is_not_bypassed(self):
        self.enable()
        with patch.object(self.ai, "recover") as recover:
            with self.assertRaisesRegex(LauncherError, "作者禁止"):
                self.app.content._cf_download({"fileName": "example.jar"}, self.job["target"], self.task, self.instance["id"])
            recover.assert_not_called()

    def test_curseforge_pack_only_failure_uses_recovery(self):
        file = {"downloadUrl": self.url, "fileName": "example.jar", "fileLength": len(self.payload),
                "hashes": [{"algo": 1, "value": self.job["sha1"]}]}
        with patch.object(self.app.net, "download", side_effect=LauncherError("404")), patch.object(self.ai, "recover", return_value=True) as recover:
            with self.assertRaises(LauncherError):
                self.app.content._cf_download(file, self.job["target"], self.task)
            recover.assert_not_called()
            self.app.content._cf_download(file, self.job["target"], self.task, self.instance["id"])
            self.assertEqual(recover.call_args.args[0]["sha1"], self.job["sha1"])
            self.assertEqual(recover.call_args.args[1]["loader"], "fabric")

    def test_http_model_transport_fallback_redaction_and_redirect_rejection(self):
        owner, requests = self, []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(data)
                if owner.http_mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://evil.invalid/steal")
                    self.end_headers()
                    return
                if "response_format" in data:
                    self.send_response(422)
                    self.end_headers()
                    self.wfile.write(b"response_format unsupported")
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"query":"Example"}\n```'}}]}).encode())
        self.http_mode = "ok"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.app.store.save({"ai_base_url": "http://127.0.0.1:" + str(server.server_port), "ai_model": "fixture"})
        try:
            with patch.object(self.ai.diagnostics, "key", return_value="SENSITIVE_TEST_KEY"):
                result = self.ai.ask("Return JSON", {"name": "SENSITIVE_TEST_KEY"})
                self.assertEqual(result["query"], "Example")
                self.assertNotIn("SENSITIVE_TEST_KEY", json.dumps(requests))
                self.http_mode = "redirect"
                with self.assertRaisesRegex(LauncherError, "重定向"):
                    self.ai.ask("Return JSON", {})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_missing_ai_configuration_fails_without_network_request(self):
        with patch("urllib.request.build_opener") as request:
            with self.assertRaisesRegex(LauncherError, "API Key"):
                self.ai.ask("JSON", {})
            request.assert_not_called()

    def test_previous_default_migrates_once_then_disabled_setting_is_preserved(self):
        from launcher.core import atomic_json
        legacy = self.root / "legacy"
        legacy.mkdir()
        atomic_json(legacy / "config.json", {"ai_pack_download_fallback": False})
        upgraded = Application(legacy)
        self.assertTrue(upgraded.store.config()["ai_pack_download_fallback"])
        upgraded.config_update({"ai_pack_download_fallback": False})
        self.assertFalse(Application(legacy).store.config()["ai_pack_download_fallback"])

    def test_mcmod_repository_is_used_before_global_github_search(self):
        self.ai.mcmod.search.return_value = [{"id": "2361", "title": "Example", "url": "https://www.mcmod.cn/class/2361.html"}]
        with patch.object(self.ai, "ask", side_effect=[{"query": "Example", "mcmod_query": "Example"}, {"articles": ["2361", "999"]},
                          {"repositories": ["Author/Example"]}]), \
             patch.object(self.ai.mcmod, "detail", return_value=[{"provider": "github", "project": "Author/Example", "source": "https://www.mcmod.cn/class/2361.html"}]) as detail, \
             patch.object(self.app.content.providers, "get", return_value=self.repo) as get:
            self.assertEqual(self.ai.discover("Example"), ["Author/Example"])
            detail.assert_called_once_with("2361", None)
            get.assert_called_once_with(API + "Author/Example")

    def test_recovery_looks_up_mcmod_release_before_hash_fallback(self):
        self.ai.mcmod.search.return_value = [{"id": "2361", "title": "Example", "url": "https://www.mcmod.cn/class/2361.html"}]
        url = "https://github.com/Author/Example/releases/download/v1/example.jar"
        asset = {"name": "example.jar", "id": 1, "size": len(self.payload), "browser_download_url": url}
        events = []
        def get(endpoint):
            events.append(endpoint)
            if endpoint.endswith("/releases?per_page=100"):
                return [{"assets": [asset]}]
            raise LauncherError("404")
        with patch.object(self.ai, "ask", side_effect=[{"query": "Example"}, {"articles": ["2361"]}, {"urls": [url]}]) as ask, \
             patch.object(self.ai.mcmod, "detail", return_value=[{"provider": "github", "project": "Author/Example", "source": "https://www.mcmod.cn/class/2361.html"}]), \
             patch.object(self.app.content.providers, "get", side_effect=get):
            self.assertEqual(self.ai.candidates(self.job, {}, self.task), [url])
            self.assertIn("/releases?", events[0])
            self.assertEqual(ask.call_args.args[1]["candidates"][0]["source"], "https://www.mcmod.cn/class/2361.html")

    def test_mcmod_modrinth_project_link_is_resolved_before_global_hash_lookup(self):
        link = {"provider": "modrinth", "project": "example", "url": "https://modrinth.com/mod/example", "source": "https://www.mcmod.cn/class/2361.html"}
        events = []
        def get(url):
            events.append(url)
            if "/project/example/version?" in url:
                return [{"files": [{"filename": "example.jar", "url": self.url, "hashes": {"sha1": self.job["sha1"]}}]}]
            raise LauncherError("404")
        with patch.object(self.ai, "plan", return_value={"query": "Example"}), \
             patch.object(self.ai, "encyclopedia", return_value=[link]), \
             patch.object(self.ai, "ask", return_value={"urls": [self.url]}), \
             patch.object(self.app.content.providers, "get", side_effect=get):
            self.assertEqual(self.ai.candidates(self.job, {"game_version": "1.21.1", "loader": "fabric"}, self.task), [self.url])
        self.assertIn("/project/example/version?", events[0])

    def test_mrpack_full_import_recovers_and_marks_ready_after_verified_mod(self):
        self.enable()
        index = {"formatVersion": 1, "game": "minecraft", "name": "AI recovered pack",
                 "dependencies": {"minecraft": "1.21.1", "fabric-loader": "0.16.5"},
                 "files": [{"path": "mods/example.jar", "downloads": ["https://original.invalid/mod.jar"],
                            "hashes": {"sha1": self.job["sha1"], "sha512": self.job["sha512"]}, "fileSize": len(self.payload)}]}
        archive = self.root / "fixture.mrpack"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("modrinth.index.json", json.dumps(index))
            output.writestr("overrides/config/keep.txt", "pack settings")
        def download(url, *args, **kwargs):
            if url.startswith("https://original.invalid"):
                raise LauncherError("404")
            return self.fake_download(url, *args, **kwargs)
        with patch.object(self.app.game, "install"), patch.object(self.ai, "candidates", return_value=[self.candidate]), \
             patch.object(self.app.net, "download", side_effect=download):
            result = self.app.content.import_pack(archive, self.task)
        instance = self.app.store.instance(result["instance_id"])
        self.assertTrue(instance["ready"])
        self.assertFalse(instance["pack_install_pending"])
        directory = self.app.store.game_dir(instance["id"])
        self.assertEqual((directory / "mods/example.jar").read_bytes(), self.payload)
        self.assertEqual((directory / "config/keep.txt").read_text(), "pack settings")

    def test_external_pack_provider_recovery_uses_original_hashes_and_staging(self):
        self.enable()
        plan = {"name": "External pack", "game_version": "1.21.1", "loader": "fabric", "loader_version": "0.16.5",
                "archives": [], "files": [dict(self.job, urls=["https://original.invalid/mod.jar"])]}
        def download(url, *args, **kwargs):
            if url.startswith("https://original.invalid"):
                raise LauncherError("404")
            return self.fake_download(url, *args, **kwargs)
        for provider in ("atlauncher", "ftb"):
            with self.subTest(provider=provider), patch.object(self.app.content.providers, "manifest", return_value=plan), \
                 patch.object(self.app.game, "install"), patch.object(self.ai, "candidates", return_value=[self.candidate]), \
                 patch.object(self.app.net, "download", side_effect=download):
                result = self.app.content.install_project("1", "1", "", "modpack", provider, self.task)
                instance = self.app.store.instance(result["instance_id"])
                self.assertTrue(instance["ready"])
                self.assertEqual((self.app.store.game_dir(instance["id"]) / "mods/example.jar").read_bytes(), self.payload)
        self.assertFalse(self.job["target"].exists())


if __name__ == "__main__":
    unittest.main()

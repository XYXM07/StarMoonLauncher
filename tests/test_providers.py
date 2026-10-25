"""Small public-format fixtures; never read player games or call live providers."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher.core import Cancelled, LauncherError, Network, Task
from launcher.providers import ATL_API, ATL_CDN, FTB_API, MCMOD_API, ProviderHub, file_record, sources
from launcher.service import Application


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root)
        self.app.store.create_minecraft()
        self.hub = self.app.content.providers
        self.mod = b"synthetic mod bytes"
        self.config = b"keep_config=true"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("config/options.txt", self.config)
        self.archive = stream.getvalue()
        self.atl_pack = {"name": "Example Pack", "safeName": "ExamplePack", "type": "public", "description": "<b>Example</b>",
                         "versions": [{"version": "2.0", "minecraft": "1.21.1", "published": 2},
                                      {"version": "1.0", "minecraft": "1.20.1", "published": 1}]}
        self.atl = {"version": "2.0", "minecraft": "1.21.1", "loader": {"type": "neoforge", "metadata": {"version": "21.1.1"}},
                    "configs": {"sha1": hashlib.sha1(self.archive).hexdigest(), "filesize": len(self.archive)},
                    "mods": [{"name": "Example Mod", "file": "example mod.jar", "type": "mods", "download": "server",
                              "url": "packs/ExamplePack/files/example mod.jar", "md5": hashlib.md5(self.mod).hexdigest(),
                              "filesize": len(self.mod), "client": True},
                             {"name": "Server", "file": "server.jar", "client": False},
                             {"name": "Optional", "file": "optional.jar", "client": True, "optional": True}]}
        self.ftb_pack = {"id": 1, "name": "FTB Example", "synopsis": "Example pack", "installs": 123,
                         "authors": [{"name": "Pack Author"}], "art": [{"type": "square", "url": "https://cdn.feed-the-beast.com/logo.png"}],
                         "versions": [{"id": 10, "name": "1.0", "type": "release", "released": 10,
                                       "targets": [{"name": "minecraft", "version": "1.21.1"}]},
                                      {"id": 9, "name": "old", "type": "archived"}, {"id": 8, "name": "private", "private": True}]}
        self.ftb = {"id": 10, "parent": 1, "name": "1.0", "targets": [
            {"name": "minecraft", "type": "game", "version": "1.21.1"},
            {"name": "neoforge", "type": "modloader", "version": "21.1.1"}],
            "files": [{"path": "./mods/", "name": "example.jar", "url": "https://edge.forgecdn.net/example.jar",
                       "mirrors": ["https://files.feed-the-beast.com/example.jar"], "hashes": {"sha512": hashlib.sha512(self.mod).hexdigest()},
                       "size": len(self.mod)},
                      {"path": "./", "name": "options.txt", "url": "https://files.feed-the-beast.com/options.txt",
                       "sha1": hashlib.sha1(self.config).hexdigest(), "size": len(self.config)},
                      {"path": "./mods/", "name": "server.jar", "serveronly": True},
                      {"path": "./mods/", "name": "optional.jar", "optional": True}]}
        self.responses = {ATL_API + "/packs/full/public": {"data": [self.atl_pack]},
            ATL_CDN + "/packs/ExamplePack/versions/2.0/Configs.json": self.atl,
            FTB_API + "/all": {"packs": [1]}, FTB_API + "/1": self.ftb_pack, FTB_API + "/1/10": self.ftb}
        self.fetch = patch.object(self.hub, "fetch_json", side_effect=lambda url: copy.deepcopy(self.responses[url]))
        self.fetch_mock = self.fetch.start()

    def tearDown(self):
        self.fetch.stop()
        self.temp.cleanup()

    def test_provider_types_and_invalid_provider_never_fall_back_to_modrinth(self):
        self.assertEqual(set(dict(sources("modpack"))), {"modrinth", "curseforge", "atlauncher", "ftb", "mcmod"})
        self.assertNotIn("ftb", dict(sources("mod")))
        self.assertNotIn("mcmod", dict(sources("shader")))
        with patch.object(self.app.net, "json") as remote:
            for provider, kind in (("unknown", "mod"), ("ftb", "mod"), ("atlauncher", "shader")):
                with self.assertRaises(LauncherError):
                    self.app.content.search(provider=provider, kind=kind)
            with self.assertRaises(LauncherError):
                self.app.content.versions("1", kind="mod", provider="mcmod")
            remote.assert_not_called()

    def test_atlauncher_catalog_pagination_filter_and_exact_versions(self):
        for i in range(20):
            self.responses[ATL_API + "/packs/full/public"]["data"].append(dict(self.atl_pack, safeName="Pack"+str(i), name="Other "+str(i), description="Another pack"))
        result = self.app.content.search("Other", "modpack", provider="atlauncher", offset=18)
        self.assertEqual(result["total_hits"], 20)
        self.assertEqual(len(result["hits"]), 2)
        self.assertIsNone(result["hits"][0]["downloads"])
        self.assertEqual(self.app.content.search("Example", "modpack", version="1.21.1", provider="atlauncher")["total_hits"], 1)
        self.assertEqual([v["id"] for v in self.hub.versions("atlauncher", "ExamplePack")], ["2.0", "1.0"])
        self.assertEqual(self.fetch_mock.call_count, 1)  # Catalog is reused briefly in memory.
        with self.assertRaises(LauncherError):
            self.hub.manifest("atlauncher", "ExamplePack", "not-published")

    def test_ftb_search_details_and_public_versions(self):
        data = self.app.content.search(kind="modpack", provider="ftb")
        self.assertEqual(data["hits"][0]["title"], "FTB Example")
        self.assertEqual(data["hits"][0]["downloads"], 123)
        self.responses[FTB_API + "/search/1000?term=Example"] = {"packs": [1], "curseforge": [999]}
        self.assertEqual(self.app.content.search("Example", "modpack", provider="ftb")["total_hits"], 1)
        versions = self.hub.versions("ftb", "1")
        self.assertEqual([v["id"] for v in versions], ["10"])
        self.assertEqual(versions[0]["game_versions"], ["1.21.1"])

    def test_ftb_manifest_mirrors_paths_client_side_and_loader(self):
        manifest = self.hub.manifest("ftb", "1", "10")
        self.assertEqual(manifest["loader"], "neoforge")
        self.assertEqual(manifest["loader_version"], "21.1.1")
        self.assertEqual([f["path"] for f in manifest["files"]], ["mods/example.jar", "options.txt"])
        self.assertEqual(manifest["files"][0]["urls"][0], "https://files.feed-the-beast.com/example.jar")
        self.assertEqual(len(manifest["files"][0]["sha512"]), 128)
        self.ftb["parent"] = 2
        self.hub.cache.clear()
        with self.assertRaisesRegex(LauncherError, "不匹配"):
            self.hub.manifest("ftb", "1", "10")

    def test_atlauncher_manifest_configs_md5_and_url_encoding(self):
        manifest = self.hub.manifest("atlauncher", "ExamplePack", "2.0")
        self.assertEqual(len(manifest["files"]), 1)
        self.assertEqual(manifest["files"][0]["path"], "mods/example mod.jar")
        self.assertTrue(manifest["files"][0]["urls"][0].endswith("example%20mod.jar"))
        self.assertEqual(manifest["files"][0]["md5"], hashlib.md5(self.mod).hexdigest())
        self.assertTrue(manifest["archives"][0]["urls"][0].endswith("/Configs.zip"))

    def test_atlauncher_recommended_option_dependencies_are_included(self):
        self.atl["mods"][0]["depends"] = ["Optional"]
        self.atl["mods"][2].update(type="mods", download="direct", url="https://example.com/optional.jar",
                                    md5=hashlib.md5(self.mod).hexdigest())
        plan = self.hub.manifest("atlauncher", "ExamplePack", "2.0")
        self.assertEqual(len(plan["files"]), 2)

    def test_identical_published_files_deduplicate_but_conflicting_targets_fail(self):
        self.atl["mods"].append(dict(self.atl["mods"][0]))
        self.assertEqual(len(self.hub.manifest("atlauncher", "ExamplePack", "2.0")["files"]), 1)
        self.atl["mods"][-1]["md5"] = "0" * 32
        self.hub.cache.clear()
        with self.assertRaisesRegex(LauncherError, "重复"):
            self.hub.manifest("atlauncher", "ExamplePack", "2.0")

    def test_manual_downloads_and_unsupported_steps_fail_before_creating_instance(self):
        original = copy.deepcopy(self.atl)
        for changes in ({"mods": [dict(original["mods"][0], download="browser")]},
                        {"mainClass": {"mainClass": "custom.Main"}},
                        {"loader": {"type": "liteloader"}}):
            self.responses[ATL_CDN + "/packs/ExamplePack/versions/2.0/Configs.json"] = dict(original, **changes)
            self.hub.cache.clear()
            with patch.object(self.app.game, "create") as create:
                with self.assertRaises(LauncherError):
                    self.app.content.install_project("ExamplePack", "2.0", "", "modpack", "atlauncher", Task("Test", None))
                create.assert_not_called()

    def test_untrusted_file_paths_hashes_and_urls_are_rejected(self):
        for path in ("../outside.jar", "C:/outside.jar", "/outside.jar", "mods/a.jar:token", "mods/CON.jar", "mods/a.jar.", "."):
            with self.subTest(path=path), self.assertRaises(LauncherError):
                file_record(path, ["https://example.com/a"], hashes={"sha1": "a"*40})
        for url in ("http://example.com/a", "file:///C:/a", "https://user:secret@example.com/a"):
            with self.assertRaises(LauncherError):
                file_record("mods/a.jar", [url], hashes={"sha1": "a"*40})
        for hashes in ({}, {"sha1": "not-a-hash"}):
            with self.assertRaises(LauncherError):
                file_record("mods/a.jar", ["https://example.com/a"], hashes=hashes)

    def test_mcmod_chinese_search_articles_and_malicious_links(self):
        query = "合成"
        url = MCMOD_API + "/search?q=%E5%90%88%E6%88%90&filter=1&offset=0"
        self.responses[url] = {"results": [
            {"id": 459, "name": "JEI", "description": "中文资料", "url": "https://www.mcmod.cn/class/459.html"},
            {"name": "Bad", "url": "https://example.com/class/2.html"},
            {"name": "Bad", "url": "javascript:alert(1)"}], "totalResults": 1}
        result = self.app.content.search(query, provider="mcmod")
        self.assertEqual(len(result["hits"]), 1)
        self.assertEqual(result["hits"][0]["action"], "article")
        self.assertEqual(result["hits"][0]["project_url"], "https://www.mcmod.cn/class/459.html")
        self.assertIn("%E5%90%88%E6%88%90", result["external_search_url"])
        with self.assertRaises(LauncherError):
            self.app.content.install_project("459", "1", "", "mod", "mcmod", Task("Test", None))

    def test_mcmod_outage_keeps_original_site_search_and_empty_query_is_offline(self):
        result = self.app.content.search(provider="mcmod")
        self.assertIn("输入", result["notice"])
        self.fetch_mock.assert_not_called()
        self.fetch_mock.side_effect = OSError("simulated upstream 500")
        result = self.app.content.search("JEI", provider="mcmod")
        self.assertIn("暂不可用", result["notice"])
        self.assertEqual(result["hits"], [])
        self.assertTrue(result["external_search_url"].startswith("https://search.mcmod.cn/"))

    def fake_download(self, url, target, task=None, **kwargs):
        payload = self.archive if url.endswith("Configs.zip") else self.config if url.endswith("options.txt") else self.mod
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        task.add_bytes(len(payload))
        task.complete_download(len(payload))

    def test_atlauncher_full_install_uses_new_instance_and_preserves_existing_game(self):
        existing = self.app.game.create("Keep", "1.20.1")
        world = self.app.store.game_dir(existing["id"]) / "saves/World/level.dat"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"keep-world")
        with patch.object(self.app.game, "install"), patch.object(self.app.content.net, "download", side_effect=self.fake_download):
            result = self.app.content.install_project("ExamplePack", "2.0", existing["id"], "modpack", "atlauncher", Task("Pack", None))
        self.assertNotEqual(existing["id"], result["instance_id"])
        instance = self.app.store.instance(result["instance_id"])
        game = self.app.store.game_dir(instance["id"])
        self.assertTrue(instance["ready"])
        self.assertFalse(instance["pack_install_pending"])
        self.assertEqual(instance["loader_version"], "21.1.1")
        self.assertEqual((game/"mods/example mod.jar").read_bytes(), self.mod)
        self.assertEqual((game/"config/options.txt").read_bytes(), self.config)
        self.assertEqual(world.read_bytes(), b"keep-world")
        self.assertEqual(list((self.root/"cache/pack-sessions").iterdir()), [])

    def test_ftb_install_falls_back_only_to_manifest_urls_and_uses_human_version_name(self):
        def download(url, *args, **kwargs):
            if url.startswith("https://files.feed-the-beast.com/example"):
                raise OSError("mirror unavailable")
            return self.fake_download(url, *args, **kwargs)
        with patch.object(self.app.game, "install"), patch.object(self.app.content.net, "download", side_effect=download) as fetch:
            result = self.app.content.install_project("1", "10", "", "modpack", "ftb", Task("Pack", None))
        instance = self.app.store.instance(result["instance_id"])
        self.assertEqual(instance["name"], "FTB Example 1.0")
        self.assertTrue(instance["ready"])
        self.assertTrue(any(call.args[0] == "https://edge.forgecdn.net/example.jar" for call in fetch.call_args_list))

    def test_download_failure_and_cancellation_never_mark_pack_ready(self):
        for error in (OSError("offline"), Cancelled("cancelled")):
            with patch.object(self.app.game, "install"), patch.object(self.app.content.net, "download", side_effect=error):
                with self.assertRaises(LauncherError):
                    self.app.content.install_project("1", "10", "", "modpack", "ftb", Task("Pack", None))
            instances = self.app.store.instances()
            self.assertEqual(instances, [])
        self.assertEqual(list((self.root/"cache/pack-sessions").iterdir()), [])

    def test_archive_traversal_never_writes_outside_the_new_game(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("../../escape.txt", "bad")
        self.archive = stream.getvalue()
        self.atl["configs"].update(sha1=hashlib.sha1(self.archive).hexdigest(), filesize=len(self.archive))
        with patch.object(self.app.game, "install"), patch.object(self.app.content.net, "download", side_effect=self.fake_download):
            with self.assertRaises(LauncherError):
                self.app.content.install_project("ExamplePack", "2.0", "", "modpack", "atlauncher", Task("Pack", None))
        self.assertFalse((self.app.store.minecraft_root/"escape.txt").exists())
        self.assertEqual(self.app.store.instances(), [])

    def test_sha256_and_md5_downloads_verify_cached_bytes_and_reject_corruption(self):
        network = Network(lambda: {"source": "official"})
        def fetch(url, target, *args, **kwargs):
            target.write_bytes(self.mod)
            return len(self.mod)
        for algorithm in ("sha256", "md5"):
            target = self.root/(algorithm+".jar")
            expected = hashlib.new(algorithm, self.mod).hexdigest()
            with patch.object(network.engine, "fetch", side_effect=fetch) as remote:
                network.download("https://example.com/mod.jar", target, **{algorithm: expected})
                network.download("https://example.com/mod.jar", target, **{algorithm: expected})
                self.assertEqual(remote.call_count, 1)
            with patch.object(network.engine, "fetch", side_effect=fetch), patch("launcher.core.time.sleep"):
                with self.assertRaises(LauncherError):
                    network.download("https://example.com/mod.jar", target, **{algorithm: "0"*len(expected)})
            self.assertEqual(target.read_bytes(), self.mod)


if __name__ == "__main__":
    unittest.main()

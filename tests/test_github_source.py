"""Offline release fixtures and transactional mod installs in test-owned folders."""
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/".build-env"))
from launcher.core import Cancelled, LauncherError, Task, atomic_json, read_json
from launcher.github_source import API, inspect_mod, repository, requirement_matches
from launcher.providers import sources
from launcher.service import Application


def jar(identifier="example", version="1.0", depends=None, loader="fabric", **extra):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        if loader in ("fabric", "quilt"):
            data = {"id": identifier, "version": version, "name": "Example Mod",
                    "depends": depends if depends is not None else {"minecraft": "~1.21.1", "fabricloader": ">=0.16.0"}}
            data.update(extra)
            if loader == "quilt":
                data["depends"] = [{"id": key, "versions": value} for key, value in data["depends"].items()]
                data = {"quilt_loader": data}
            archive.writestr("fabric.mod.json" if loader == "fabric" else "quilt.mod.json", json.dumps(data))
        else:
            name = "neoforge" if loader == "neoforge" else "forge"
            text = 'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="'+identifier+'"\nversion="'+version+'"\n'
            text += '[[dependencies.'+identifier+']]\nmodId="minecraft"\nmandatory=true\ntype="required"\nversionRange="[1.21,1.22)"\n'
            text += '[[dependencies.'+identifier+']]\nmodId="'+name+'"\nmandatory=true\ntype="required"\nversionRange="[1,)"\n'
            archive.writestr("META-INF/" + ("neoforge.mods.toml" if loader == "neoforge" else "mods.toml"), text)
    return stream.getvalue()


class GitHubTests(unittest.TestCase):
    def setUp(self):
        output = ROOT/"test-results"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="github-unit-", dir=str(output))
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root/"data")
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Fixture", "1.21.1", "fabric", "0.16.5")
        self.repo = "Example/Mods"
        self.source = self.app.content.github
        self.responses = {API+self.repo: {"full_name": self.repo, "name": "Mods", "description": "Public releases", "owner": {"login": "Example"}}}
        self.payloads = {}
        self.add_asset(11, "example-1.0.jar", jar())
        self.fetch_patch = patch.object(self.app.content.providers, "fetch_json", side_effect=lambda url: copy.deepcopy(self.responses[url]))
        self.fetch = self.fetch_patch.start()
        self.download_patch = patch.object(self.app.net, "download", side_effect=self.download)
        self.remote = self.download_patch.start()

    def tearDown(self):
        self.download_patch.stop()
        self.fetch_patch.stop()
        self.temp.cleanup()

    def add_asset(self, identifier, name, payload, digest=True):
        url = "https://github.com/"+self.repo+"/releases/download/v1/"+name
        asset = {"id": identifier, "name": name, "size": len(payload), "state": "uploaded", "browser_download_url": url}
        if digest:
            asset["digest"] = "sha256:"+hashlib.sha256(payload).hexdigest()
        self.responses[API+self.repo+"/releases/assets/"+str(identifier)] = asset
        self.payloads[url] = payload
        return asset

    def download(self, url, target, task=None, sha256=None, size=None):
        payload = self.payloads[url]
        if sha256 and hashlib.sha256(payload).hexdigest() != sha256:
            raise LauncherError("checksum mismatch")
        if size != len(payload):
            raise LauncherError("size mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def install(self, identifier=11, allow_unknown=False):
        return self.app.content.install_project(self.repo, str(identifier), self.instance["id"], "mod", "github", Task("GitHub", None), allow_unknown)

    def test_repository_urls_are_normalized_without_accepting_other_hosts_or_credentials(self):
        for value in ("Example/Mods", "https://github.com/Example/Mods", "https://github.com/Example/Mods.git", "github.com/Example/Mods/releases"):
            self.assertEqual(repository(value), self.repo)
        for value in ("Example", "../Mods", "https://github.com.evil.test/Example/Mods", "file:///Example/Mods", "https://u:p@github.com/Example/Mods", "https://github.com/Example/Mods?token=secret", "https://github.com/Example/Mods/blob/main/test.jar"):
            with self.subTest(value=value), self.assertRaises(LauncherError):
                repository(value)

    def test_github_is_a_mod_source_and_empty_search_does_not_call_the_network(self):
        self.assertIn("github", dict(sources("mod")))
        self.assertNotIn("github", dict(sources("modpack")))
        result = self.app.content.search(provider="github")
        self.assertTrue(result["waiting_query"])
        self.fetch.assert_not_called()
        result = self.app.content.search(self.repo, provider="github")
        self.assertEqual(result["hits"][0]["project_id"], self.repo)
        self.assertEqual(result["hits"][0]["project_url"], "https://github.com/Example/Mods")

    def test_release_assets_filter_sources_development_files_and_drafts(self):
        asset = self.responses[API+self.repo+"/releases/assets/11"]
        assets = [asset] + [dict(asset, name=name) for name in ("example-sources.jar", "example-javadoc.jar", "example-dev.jar", "Source code.zip", "launcher.exe")]
        self.responses[API+self.repo+"/releases?per_page=100"] = [
            {"tag_name": "v1", "assets": assets}, {"tag_name": "private", "draft": True, "assets": [asset]},
            {"tag_name": "beta", "prerelease": True, "assets": [dict(asset, id=12)]}]
        result = self.app.content.versions(self.repo, self.instance["id"], "mod", "github")
        self.assertEqual([v["id"] for v in result], ["11", "12"])
        self.assertEqual(result[1]["version_type"], "beta")

    def test_cross_repository_and_invalid_assets_are_rejected(self):
        asset = dict(self.responses[API+self.repo+"/releases/assets/11"])
        for changes in ({"browser_download_url": "https://example.com/example.jar"}, {"browser_download_url": "https://github.com/Other/Mods/releases/download/v1/example-1.0.jar"}, {"digest": "sha256:broken"}):
            with self.assertRaises(LauncherError):
                self.source.asset(self.repo, dict(asset, **changes))
        for changes in ({"name": "../escape.jar"}, {"name": "CON.jar"}, {"size": 1024**3}):
            self.assertIsNone(self.source.asset(self.repo, dict(asset, **changes)))

    def test_metadata_identifies_supported_loaders_and_rejects_non_mod_jars(self):
        for loader in ("fabric", "quilt", "forge", "neoforge"):
            path = self.root/(loader+".jar")
            path.write_bytes(jar(loader=loader))
            record = inspect_mod(path)[0]
            self.assertEqual(record["loader"], loader)
            self.assertEqual(record["id"], "example")
        path = self.root/"plain.jar"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Example.class", b"not a mod")
        with self.assertRaises(LauncherError):
            inspect_mod(path)
        path.write_bytes(jar(identifier="fabricloader"))
        with self.assertRaisesRegex(LauncherError, "加载器"):
            inspect_mod(path)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("fabric.mod.json", "x"*(1024*1024+1))
        with self.assertRaises(LauncherError):
            inspect_mod(path)

    def test_version_ranges_keep_unknown_separate_from_known_incompatibility(self):
        for requirement in ("~1.21.1", "^1.21.0", "[1.21, 1.22)", "[1.21.1]", ">=1.21 <1.22", "1.21.x"):
            self.assertIs(requirement_matches("1.21.1", requirement), True, requirement)
        self.assertIs(requirement_matches("1.20.1", "~1.21.1"), False)
        self.assertIsNone(requirement_matches("1.21.1", "${minecraft_version}"))

    def test_successful_install_records_identity_and_does_not_touch_worlds(self):
        game = self.app.store.game_dir(self.instance["id"])
        world = game/"saves/Keep/level.dat"
        world.parent.mkdir(parents=True)
        world.write_bytes(b"keep")
        self.install()
        self.assertEqual((game/"mods/example-1.0.jar").read_bytes(), self.payloads[next(iter(self.payloads))])
        record = read_json(self.app.store.instance_dir(self.instance["id"])/"content.json")[0]
        self.assertEqual(record["mod_ids"], ["example"])
        self.assertEqual(record["provider"], "github")
        self.assertEqual(world.read_bytes(), b"keep")

    def test_missing_dependencies_and_wrong_game_or_loader_never_write_installed_mods(self):
        payloads = [jar(depends={"minecraft": "1.20.1"}), jar(loader="forge"),
                    jar(depends={"minecraft": "1.21.1", "missing_api": "*"})]
        for identifier, payload in enumerate(payloads, 20):
            self.add_asset(identifier, str(identifier)+".jar", payload)
            with self.assertRaises(LauncherError):
                self.install(identifier, allow_unknown=True)
        self.assertFalse((self.app.store.game_dir(self.instance["id"])/"mods").exists())

    def test_unknown_compatibility_requires_explicit_opt_in(self):
        self.add_asset(21, "unknown.jar", jar(depends={"minecraft": "${minecraft_version}"}))
        with self.assertRaisesRegex(LauncherError, "无法确认"):
            self.install(21)
        result = self.install(21, allow_unknown=True)
        self.assertTrue(result["github_unverified"])

    def test_declared_incompatible_mods_are_blocked_even_with_opt_in(self):
        self.add_asset(22, "conflict.jar", jar(breaks={"fabricloader": "*"}))
        with self.assertRaisesRegex(LauncherError, "不兼容"):
            self.install(22, allow_unknown=True)

    def test_same_repo_can_have_distinct_mods_and_updates_keep_disabled_state(self):
        self.install()
        folder = self.app.store.game_dir(self.instance["id"])/"mods"
        old = folder/"example-1.0.jar"
        old.rename(folder/"example-1.0.jar.disabled")
        self.add_asset(12, "example-2.0.jar", jar(version="2.0"))
        self.install(12)
        self.assertTrue((folder/"example-2.0.jar.disabled").exists())
        self.assertFalse((folder/"example-1.0.jar.disabled").exists())
        self.add_asset(13, "other.jar", jar(identifier="other"))
        self.install(13)
        records = read_json(self.app.store.instance_dir(self.instance["id"])/"content.json")
        self.assertEqual(len(records), 2)
        self.assertTrue((folder/"example-2.0.jar.disabled").exists())
        self.assertTrue((folder/"other.jar").exists())

    def test_registry_write_failure_restores_old_binary_and_registry(self):
        self.install()
        folder = self.app.store.game_dir(self.instance["id"])/"mods"
        original = (folder/"example-1.0.jar").read_bytes()
        self.add_asset(12, "example-2.0.jar", jar(version="2.0"))
        def write(path, data):
            if Path(path).name == "content.json":
                raise OSError("simulated metadata write failure")
            atomic_json(path, data)
        with patch("launcher.github_source.atomic_json", side_effect=write), self.assertRaises(OSError):
            self.install(12)
        self.assertEqual((folder/"example-1.0.jar").read_bytes(), original)
        self.assertFalse((folder/"example-2.0.jar").exists())
        self.assertEqual(read_json(self.app.store.instance_dir(self.instance["id"])/"content.json")[0]["version_id"], "11")

    def test_other_providers_or_manual_mods_are_not_silently_replaced(self):
        folder = self.app.store.game_dir(self.instance["id"])/"mods"
        folder.mkdir()
        path = folder/"manual.jar"
        path.write_bytes(jar())
        original = path.read_bytes()
        with self.assertRaisesRegex(LauncherError, "相同模组"):
            self.install()
        self.assertEqual(path.read_bytes(), original)

    def test_no_remote_digest_uses_fresh_download_and_records_a_local_digest(self):
        self.add_asset(31, "legacy.jar", jar(), digest=False)
        task = Task("Download", None)
        asset, cached, digest = self.source.download(self.repo, "31", task)
        self.assertIn(".download-", self.remote.call_args.args[1].name)
        self.assertEqual(digest, hashlib.sha256(cached.read_bytes()).hexdigest())
        self.source.download(self.repo, "31", task)
        self.assertEqual(self.remote.call_args.kwargs["sha256"], digest)

    def test_corruption_and_cancellation_do_not_install_mods(self):
        self.payloads[next(iter(self.payloads))] = b"corrupted"
        with self.assertRaises(LauncherError):
            self.install()
        task = Task("Cancel", None)
        task.cancel.set()
        with self.assertRaises(Cancelled):
            self.source.install(self.repo, "11", self.instance["id"], task)
        self.assertFalse((self.app.store.game_dir(self.instance["id"])/"mods").exists())


if __name__ == "__main__":
    unittest.main()

"""Mod website metadata, recorded providers and MCMOD fallbacks without network calls."""
import json
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path

from launcher.mod_sites import metadata_sites, mod_websites, website_url
from launcher.service import Application


class ModSiteTests(unittest.TestCase):
    def test_unsafe_links_are_rejected_and_legacy_http_is_supported(self):
        for url in ("file:///C:/private.txt", "javascript:alert(1)", "mailto:user@example.com", "https://user:secret@example.com/", "https://example.com:bad/", "https://example.com/a\nb"):
            self.assertEqual(website_url(url), "")
        self.assertEqual(website_url("http://example.com/mod"), "http://example.com/mod")
        self.assertEqual(website_url("http://www.mcmod.cn:80/class/2361.html"), "https://www.mcmod.cn/class/2361.html")

    def test_metadata_supports_homepage_sources_and_modmenu_mcmod_link(self):
        links = metadata_sites({"contact": {"homepage": "https://example.com/mod", "sources": "https://github.com/Author/Mod"},
            "custom": {"modmenu": {"links": {"modmenu.mcmod": "https://www.mcmod.cn/class/2361.html"}}}})
        self.assertEqual([link["title"] for link in links], ["项目主页", "GitHub", "MC百科"])
        sites = mod_websites({"websites": links, "provider": "github", "project_id": "Author/Mod"})
        self.assertEqual(len(sites), 3)
        self.assertFalse(any("search.mcmod.cn" in link["url"] for link in sites))

    def test_recorded_platforms_have_direct_project_pages(self):
        cases = (("modrinth", "P7dR8mSH", "https://modrinth.com/mod/P7dR8mSH"),
                 ("curseforge", "238222", "https://www.curseforge.com/projects/238222"),
                 ("github", "Author/Mod", "https://github.com/Author/Mod"),
                 ("mcmod", "2361", "https://www.mcmod.cn/class/2361.html"))
        for provider, project, expected in cases:
            self.assertIn(expected, [link["url"] for link in mod_websites({"provider": provider, "project_id": project, "filename": "mod.jar"})])

    def test_unknown_mod_uses_name_id_or_cleaned_filename_for_mcmod_search(self):
        for item, name in (({"mod_name": "示例 模组"}, "示例 模组"), ({"mod_id": "example"}, "example"),
                           ({"filename": "example-1.21.1-fabric.jar.disabled"}, "example")):
            url = mod_websites(item)[-1]["url"]
            self.assertEqual(urllib.parse.urlsplit(url).hostname, "search.mcmod.cn")
            self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query), {"key": [name], "filter": ["1"]})

    def test_fabric_quilt_forge_and_legacy_metadata_survive_disabled_files(self):
        with tempfile.TemporaryDirectory() as root:
            app = Application(root)
            app.store.create_minecraft()
            instance = app.game.create("Sites", "1.21.1", "fabric", "0.16.0")
            folder = app.store.game_dir(instance["id"]) / "mods"
            folder.mkdir()
            entries = [("fabric.mod.json", {"id": "example", "contact": {"homepage": "https://example.com/mod"}}),
                       ("quilt.mod.json", {"quilt_loader": {"id": "example", "metadata": {"contact": {"homepage": "https://example.com/mod"}}}}),
                       ("META-INF/mods.toml", '[[mods]]\nmodId="example"\ndisplayURL="https://example.com/mod"'),
                       ("mcmod.info", [{"modid": "example", "url": "https://example.com/mod"}])]
            path = folder / "example.jar.disabled"
            for name, data in entries:
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(name, data if isinstance(data, str) else json.dumps(data))
                item = app.content.list_local(instance["id"])[0]
                self.assertFalse(item["enabled"])
                self.assertEqual(item["mod_id"], "example")
                self.assertIn("https://example.com/mod", [link["url"] for link in item["websites"]])
                path.unlink()


if __name__ == "__main__":
    unittest.main()

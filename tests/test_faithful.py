"""Faithful's published Java catalog and transactional resource-pack installs."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher.core import Cancelled, LauncherError, Task, read_json
from launcher.faithful import AUTHOR, LICENSE, PACKS, SITE, compatible, download_url
from launcher.providers import sources
from launcher.service import Application

PROJECT = "faithful_32x_java"


def catalog():
    def release(number, date, link=None):
        return {"file_type":"Release", "file_version":number, "file_name":"Release "+str(number), "date":date,
                "links":{"download":link or "https://database.faithfulpack.net/packs/32x-Java/Faithful-"+str(number)+".zip"}}
    return {"1.20.1":[release(2,"2026-06-01"), release(1,"2025-01-01")],
            "1.18.x":[release("old","2024-01-01")], "1.21.1":[release(3,"2026-07-01")]}


def pack_bytes(extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,"w") as archive:
        if not extra or extra[0] != "pack.mcmeta":
            archive.writestr("pack.mcmeta",json.dumps({"pack":{"pack_format":15,"description":"Faithful fixture"}}))
        archive.writestr("assets/minecraft/textures/fixture.png",b"texture fixture")
        if extra:
            archive.writestr(*extra)
    return stream.getvalue()


class FaithfulTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name)/"data")
        self.app.store.create_minecraft()
        self.instance = self.app.game.create("Faithful target","1.20.1","vanilla","")
        self.data = catalog()
        self.remote = patch.object(self.app.net,"json",side_effect=lambda url:copy.deepcopy(self.data)).start()
        self.downloader = patch.object(self.app.net,"download",side_effect=lambda url,path,**kwargs:Path(path).write_bytes(pack_bytes())).start()
        self.addCleanup(patch.stopall)
        self.folder = self.app.content._folder(self.instance["id"],"resourcepack")

    def tearDown(self):
        self.temp.cleanup()

    def choices(self):
        return self.app.content.versions(PROJECT,self.instance["id"],"resourcepack","faithful")

    def install(self, version):
        return self.app.content.install_project(PROJECT,version,self.instance["id"],"resourcepack","faithful",Task("test",None))

    def test_only_resourcepacks_offer_faithful_and_wrong_kind_never_downloads(self):
        self.assertIn("faithful",dict(sources("resourcepack")))
        for kind in ("mod","shader","modpack","datapack","map"):
            self.assertNotIn("faithful",dict(sources(kind)))
            with self.assertRaises(LauncherError):
                self.app.content.search(kind=kind,provider="faithful")
            with self.assertRaises(LauncherError):
                self.app.content.install_project(PROJECT,"x",self.instance["id"],kind,"faithful",Task("test",None))
        self.downloader.assert_not_called()

    def test_search_six_java_series_keywords_previews_and_pagination(self):
        result = self.app.content.search(kind="resourcepack",provider="faithful")
        self.assertEqual(len(result["hits"]),6)
        self.assertTrue(all(hit["author"]==AUTHOR and hit["icon_url"].endswith(".jpg") for hit in result["hits"]))
        self.assertEqual(len(self.app.content.search("经典",kind="resourcepack",provider="faithful")["hits"]),4)
        self.assertEqual(len(self.app.content.search("32x",kind="resourcepack",provider="faithful")["hits"]),3)
        self.assertEqual(self.app.content.search(kind="resourcepack",provider="faithful",offset=18)["hits"],[])
        self.assertTrue(all(key.endswith("_java") for key in PACKS))

    def test_version_filters_exact_and_explicit_wildcards_only(self):
        self.assertTrue(compatible("1.18.x","1.18.2"))
        self.assertTrue(compatible("1.18.x","1.18"))
        self.assertFalse(compatible("1.18.x","1.180.1"))
        self.assertFalse(compatible("1.18.x","1.18-pre1"))
        self.assertFalse(compatible("1.18.1","1.18.2"))
        choices = self.choices()
        self.assertEqual([item["name"] for item in choices],["Release 2 · 1.20.1","Release 1 · 1.20.1"])
        self.assertEqual(len(self.app.content.search(kind="resourcepack",provider="faithful",version="1.20.1")["hits"]),6)
        self.assertEqual(self.app.content.search(kind="resourcepack",provider="faithful",version="1.99")["hits"],[])

    def test_official_raw_catalog_fallback_cache_and_invalid_shape(self):
        self.remote.side_effect = [LauncherError("offline"),catalog()]
        self.assertTrue(self.choices())
        self.assertTrue(self.remote.call_args.args[0].startswith("https://raw.githubusercontent.com/Faithful-Resource-Pack/Website/"))
        self.choices()[0]["name"]="changed"
        self.assertNotEqual(self.choices()[0]["name"],"changed")
        self.assertEqual(self.remote.call_count,2)
        self.app.content.faithful.cache.clear()
        self.remote.side_effect = lambda url:[]
        with self.assertRaises(LauncherError):
            self.choices()

    def test_partial_catalog_failure_is_visible_and_unsupported_entries_are_omitted(self):
        def response(url):
            if PROJECT in url:
                raise LauncherError("offline")
            return catalog()
        self.remote.side_effect=response
        result=self.app.content.search(kind="resourcepack",provider="faithful",version="1.20.1")
        self.assertEqual(len(result["hits"]),5)
        self.assertIn("notice",result)
        self.app.content.faithful.cache.clear()
        data=catalog()
        data["1.20.1"] += [dict(data["1.20.1"][0],links={"curse":"https://www.curseforge.com/project"}),
                                dict(data["1.20.1"][0],links={"download":"https://example.com/pack.zip"})]
        self.remote.side_effect=lambda url:data
        self.assertEqual(len(self.choices()),2)

    def test_install_records_author_license_and_original_zip_in_resourcepacks(self):
        selected=self.choices()[0]
        self.install(selected["id"])
        record=read_json(self.app.store.instance_dir(self.instance["id"])/"content.json")[0]
        self.assertEqual(record["provider"],"faithful")
        self.assertEqual(record["author"],AUTHOR)
        self.assertEqual(record["license_url"],LICENSE)
        self.assertEqual((self.folder/record["filename"]).read_bytes(),pack_bytes())
        self.assertEqual(len(record["sha256"]),64)
        self.assertEqual(self.app.content.list_local(self.instance["id"])[0]["kind"],"resourcepack")
        self.assertEqual(list((self.app.store.root/"cache").glob("faithful-*")),[])

    def test_forged_version_and_incompatible_version_never_download(self):
        for version in ("../evil", self.app.content.faithful.versions(PROJECT,"1.21.1")[0]["id"]):
            with self.assertRaises(LauncherError):
                self.install(version)
        self.downloader.assert_not_called()

    def test_invalid_archives_preserve_existing_pack_and_metadata(self):
        old=self.choices()[1]
        self.install(old["id"])
        registry=self.app.store.instance_dir(self.instance["id"])/"content.json"
        original=registry.read_bytes()
        original_files={path.name:path.read_bytes() for path in self.folder.iterdir()}
        for payload in (b"<html>error</html>",pack_bytes(("../outside.txt",b"bad")),pack_bytes(("pack.mcmeta",b"{}"))):
            self.downloader.side_effect=lambda url,path,**kwargs:Path(path).write_bytes(payload)
            with self.assertRaises(LauncherError):
                self.install(self.choices()[0]["id"])
            self.assertEqual(registry.read_bytes(),original)
            self.assertEqual({path.name:path.read_bytes() for path in self.folder.iterdir()},original_files)
        self.assertFalse((self.app.store.root/"outside.txt").exists())

    def test_update_preserves_disabled_status_and_check_updates(self):
        self.install(self.choices()[1]["id"])
        old=self.app.content.list_local(self.instance["id"])[0]
        self.assertEqual(len(self.app.content.check_updates(self.instance["id"])),1)
        self.app.content.toggle(self.instance["id"],old["filename"],"resourcepack")
        self.install(self.choices()[0]["id"])
        items=self.app.content.list_local(self.instance["id"])
        self.assertEqual(len(items),1)
        self.assertFalse(items[0]["enabled"])
        self.assertTrue(list((self.app.store.instance_dir(self.instance["id"])/"trash").glob("*")))

    def test_record_failure_or_cancellation_rolls_back_update_and_cleans_staging(self):
        self.install(self.choices()[1]["id"])
        registry=self.app.store.instance_dir(self.instance["id"])/"content.json"
        original=registry.read_bytes()
        files={path.name:path.read_bytes() for path in self.folder.iterdir()}
        for failure in (OSError("disk fixture"),Cancelled("cancel fixture")):
            with patch("launcher.faithful.atomic_json",side_effect=failure), self.assertRaises(type(failure)):
                self.install(self.choices()[0]["id"])
            self.assertEqual(registry.read_bytes(),original)
            self.assertEqual({path.name:path.read_bytes() for path in self.folder.iterdir()},files)
            self.assertEqual(list((self.app.store.root/"cache").glob("faithful-*")),[])

    def test_unrelated_filename_collision_is_preserved(self):
        selected=self.choices()[0]
        filename=PROJECT+"-"+selected["id"]+".zip"
        self.folder.mkdir(parents=True,exist_ok=True)
        (self.folder/filename).write_bytes(b"player file")
        with self.assertRaises(LauncherError):
            self.install(selected["id"])
        self.assertEqual((self.folder/filename).read_bytes(),b"player file")

    def test_download_urls_must_be_published_https_zip_hosts(self):
        for url in ("http://database.faithfulpack.net/packs/a.zip","https://example.com/a.zip",
                    "https://user@database.faithfulpack.net/packs/a.zip","https://github.com/other/a/releases/download/1/a.zip"):
            self.assertEqual(download_url(url),"")
        self.assertTrue(download_url("https://github.com/Faithful-Resource-Pack/Faithful-32x-Java/releases/download/1/a.zip"))

    def test_published_modrinth_cdn_and_version_pages_resolve_exact_files_with_hashes(self):
        url="https://cdn.modrinth.com/data/AAAA/versions/VVV/Faithful.zip"
        checks={"sha512":hashlib.sha512(pack_bytes()).hexdigest()}
        version={"project_id":"AAAA","game_versions":["1.20.1"],"files":[{"primary":True,"url":url,"hashes":checks,"size":len(pack_bytes())}]}
        for reference in (url,"https://modrinth.com/resourcepack/classic-faithful-64x/version/2025q1"):
            self.app.content.faithful.cache.clear()
            data=catalog()
            data["1.20.1"]= [dict(data["1.20.1"][0],links={"modrinth":reference})]
            def response(endpoint):
                if endpoint.startswith("https://api.modrinth.com"):
                    return version if "/version/" in endpoint else {"id":"AAAA","project_type":"resourcepack"}
                return data
            self.remote.side_effect=response
            self.install(self.choices()[0]["id"])
            self.assertEqual(self.downloader.call_args.args[0],url)
            self.assertEqual(self.downloader.call_args.kwargs["sha512"],checks["sha512"])
        version["game_versions"]=["1.21.1"]
        self.downloader.reset_mock()
        with self.assertRaises(LauncherError):
            self.install(self.choices()[0]["id"])
        self.downloader.assert_not_called()


if __name__=="__main__":
    unittest.main()

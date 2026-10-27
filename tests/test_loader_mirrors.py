"""Loader source selection, fallback and unchanged integrity checks."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from launcher.core import LauncherError, MIRROR, mirror_url, read_json
from launcher.service import Application


FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader/1.20.1"
NEOFORGE_META = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
XML = b"<metadata><versioning><versions><version>21.1.1</version><version>21.10.1</version><version>21.1.2-beta</version></versions></versioning></metadata>"


class LoaderMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Application(self.root)
        self.net = self.service.net

    def tearDown(self):
        self.temp.cleanup()

    def test_routes_cover_metadata_profile_and_maven_but_leave_other_hosts_alone(self):
        cases = {
            FABRIC_META: MIRROR + "/fabric-meta/v2/versions/loader/1.20.1",
            FABRIC_META + "/0.16.9/profile/json": MIRROR + "/fabric-meta/v2/versions/loader/1.20.1/0.16.9/profile/json",
            "https://maven.fabricmc.net/net/fabricmc/fabric-loader/0.16.9/fabric-loader-0.16.9.jar": MIRROR + "/maven/net/fabricmc/fabric-loader/0.16.9/fabric-loader-0.16.9.jar",
            NEOFORGE_META: MIRROR + "/maven/net/neoforged/neoforge/maven-metadata.xml",
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/21.1.1/neoforge-21.1.1-installer.jar?x=1": MIRROR + "/maven/net/neoforged/neoforge/21.1.1/neoforge-21.1.1-installer.jar?x=1",
            "https://maven.neoforged.net/releases/net/neoforged/forge/1.20.1-47.1.1/forge-1.20.1-47.1.1.jar": MIRROR + "/maven/net/neoforged/forge/1.20.1-47.1.1/forge-1.20.1-47.1.1.jar",
        }
        for original, expected in cases.items():
            with self.subTest(url=original):
                self.assertEqual(mirror_url(original), expected)
                self.assertEqual(self.net.urls(original), [expected, original])
        for original in ("https://meta.fabricmc.net.example.com/v2/versions", "https://maven.neoforged.net/snapshots/example.jar"):
            self.assertEqual(self.net.urls(original), [original])
        self.service.config_update({"source":"official"})
        for original in cases:
            self.assertEqual(self.net.urls(original), [original])

    def test_fabric_catalogue_and_generated_profile_follow_mirror_and_official_fallback(self):
        requested = []
        profile = {"id":"fabric-loader-0.16.9-1.20.1", "inheritsFrom":"1.20.1", "libraries":[]}
        def response(url):
            requested.append(url)
            if url.startswith(MIRROR):
                raise OSError("fixture mirror unavailable")
            return profile if url.endswith("/profile/json") else [{"loader":{"version":"0.16.9","stable":True}}]
        with patch("launcher.core.request_json",side_effect=response):
            self.assertEqual(self.service.game.loader_versions("1.20.1","fabric"),[{"version":"0.16.9","stable":True}])
            identifier = self.service.game.install_loader({"loader":"fabric","game_version":"1.20.1","loader_version":"0.16.9"},Mock())
        profile_url = FABRIC_META + "/0.16.9/profile/json"
        self.assertEqual(requested,[mirror_url(FABRIC_META),FABRIC_META,mirror_url(profile_url),profile_url])
        self.assertEqual(read_json(self.service.game.profile_path(identifier)),profile)

    def test_neoforge_xml_uses_mirror_and_filters_the_exact_game_version(self):
        with patch("launcher.core.urllib.request.urlopen",return_value=io.BytesIO(XML)) as request:
            versions = self.service.game.loader_versions("1.21.1","neoforge")
        self.assertEqual(request.call_args.args[0].full_url,mirror_url(NEOFORGE_META))
        self.assertEqual(versions,[{"version":"21.1.2-beta","stable":False},{"version":"21.1.1","stable":True}])

    def test_neoforge_falls_back_for_network_or_bad_xml_and_respects_official_source(self):
        for failure in (OSError("fixture unavailable"), b"not xml"):
            requested = []
            def response(request, **kwargs):
                requested.append(request.full_url)
                if request.full_url.startswith(MIRROR):
                    if isinstance(failure,Exception):
                        raise failure
                    return io.BytesIO(failure)
                return io.BytesIO(XML)
            with self.subTest(failure=type(failure).__name__), patch("launcher.core.urllib.request.urlopen",side_effect=response):
                self.assertEqual(self.net.xml(NEOFORGE_META).tag,"metadata")
                self.assertEqual(requested,[mirror_url(NEOFORGE_META),NEOFORGE_META])
        self.service.config_update({"source":"official"})
        with patch("launcher.core.urllib.request.urlopen",return_value=io.BytesIO(XML)) as request:
            self.service.game.loader_versions("1.21.1","neoforge")
        self.assertEqual(request.call_args.args[0].full_url,NEOFORGE_META)
        self.assertEqual(request.call_count,1)

    def test_neoforge_rejects_oversized_metadata_and_reports_both_sources_failed(self):
        with patch("launcher.core.urllib.request.urlopen",side_effect=lambda *args,**kwargs:io.BytesIO(b"x"*(4*1024*1024+1))) as request:
            with self.assertRaises(LauncherError):
                self.net.xml(NEOFORGE_META)
        self.assertEqual(request.call_count,2)

    def test_loader_binary_keeps_hash_validation_before_fallback_to_official(self):
        url = "https://maven.fabricmc.net/net/fabricmc/fabric-loader/0.16.9/fabric-loader-0.16.9.jar"
        data = b"fixture verified loader"
        target = self.root / "loader.jar"
        requested = []
        def fetch(endpoint, partial, *args):
            requested.append(endpoint)
            payload = b"bad mirror file" if endpoint.startswith(MIRROR) else data
            Path(partial).write_bytes(payload)
            return len(payload)
        with patch.object(self.net.engine,"fetch",side_effect=fetch), patch("launcher.core.time.sleep"):
            self.net.download(url,target,sha1=hashlib.sha1(data).hexdigest())
        self.assertEqual(requested,[mirror_url(url)]*3+[url])
        self.assertEqual(target.read_bytes(),data)
        self.assertEqual(list(self.root.glob("*.part-*")),[])


if __name__ == "__main__":
    unittest.main()

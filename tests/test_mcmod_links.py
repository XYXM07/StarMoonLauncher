"""Public MCMOD pages and related-link discovery, using small offline fixtures."""
import base64
import unittest
from unittest.mock import Mock, patch

from launcher.core import Cancelled, LauncherError, Task
from launcher.mcmod_links import MCModLinks, PageLinks, project_link


class MCModLinkTests(unittest.TestCase):
    def setUp(self):
        self.hub = Mock()
        self.source = MCModLinks(self.hub)

    def test_outbound_base64_links_are_decoded_and_normalized_without_following_redirect(self):
        url = "https://github.com/gnembon/fabric-carpet/tree/master"
        encoded = base64.b64encode(url.encode()).decode()
        self.assertEqual(project_link("//link.mcmod.cn/target/" + encoded)["project"], "gnembon/fabric-carpet")
        self.assertEqual(project_link("https://modrinth.com/mod/carpet/versions")["project"], "carpet")

    def test_unsafe_or_unsupported_external_links_are_ignored(self):
        for url in ("javascript:alert(1)", "http://github.com/Author/Repo", "https://github.com.evil.invalid/Author/Repo",
                    "https://user:secret@github.com/Author/Repo", "https://github.com:8080/Author/Repo",
                    "https://github.com/Author/Repo?token=secret", "//link.mcmod.cn/target/invalid!!", "file:///C:/private"):
            with self.subTest(url=url):
                self.assertIsNone(project_link(url))

    def test_search_extracts_only_canonical_articles_and_deduplicates_images(self):
        html = '<a href="https://www.mcmod.cn/class/2361.html"><img src="x"></a><a href="/class/2361.html">Carpet</a>'
        html += '<a href="/class/2361.html">www.mcmod.cn/class/2361.html</a>'
        html += '<a href="https://evil.invalid/class/9.html">Fake</a><a href="/class/history/2361.html">History</a>'
        with patch.object(self.source, "fetch_page", return_value=html):
            result = self.source.search("Carpet")
        self.assertEqual(result, [{"id": "2361", "title": "Carpet", "url": "https://www.mcmod.cn/class/2361.html"}])
        self.hub.get.assert_not_called()

    def test_detail_only_reads_related_link_section_not_arbitrary_body_links(self):
        html = '<a href="https://github.com/Other/Conflict">incompatible addon</a>'
        html += '<ul class="common-link-icon-frame"><li><a href="https://github.com/Author/Mod" data-original-title="GitHub: Fabric"><svg><use></use></svg></a></li></ul>'
        with patch.object(self.source, "fetch_page", return_value=html):
            links = self.source.detail("2361")
        self.assertEqual([item["project"] for item in links], ["Author/Mod"])
        self.assertEqual(links[0]["label"], "GitHub: Fabric")
        self.assertEqual(links[0]["source"], "https://www.mcmod.cn/class/2361.html")

    def test_official_search_failure_falls_back_to_community_metadata(self):
        self.hub.get.return_value = {"results": [{"url": "https://www.mcmod.cn/class/2361.html", "name": "Carpet"}]}
        with patch.object(self.source, "fetch_page", side_effect=LauncherError("unavailable")):
            self.assertEqual(self.source.search("Carpet")[0]["id"], "2361")
        self.assertIn("/api/search?", self.hub.get.call_args.args[0])

    def test_detail_api_fallback_accepts_only_supported_related_projects(self):
        self.hub.get.return_value = {"links": [{"url": "https://modrinth.com/mod/carpet", "title": "Modrinth"}, {"url": "https://evil.invalid/fake.jar"}]}
        with patch.object(self.source, "fetch_page", side_effect=LauncherError("unavailable")):
            self.assertEqual([item["project"] for item in self.source.detail("2361")], ["carpet"])

    def test_unavailable_sources_return_empty_for_further_source_fallback(self):
        self.hub.get.side_effect = LauncherError("unavailable")
        with patch.object(self.source, "fetch_page", side_effect=LauncherError("unavailable")):
            self.assertEqual(self.source.search("Carpet"), [])
            self.assertEqual(self.source.detail("2361"), [])

    def test_cancelled_task_never_fetches_and_invalid_article_is_rejected(self):
        task = Task("test", None)
        task.cancel.set()
        with patch.object(self.source, "fetch_page") as fetch:
            with self.assertRaises(Cancelled):
                self.source.search("Carpet", task)
            fetch.assert_not_called()
            with self.assertRaises(LauncherError):
                self.source.detail("../private")

    def test_page_cache_and_destination_allowlist(self):
        with patch.object(self.source, "fetch_page", return_value="fixture") as fetch:
            self.source.page("https://www.mcmod.cn/class/2361.html")
            self.source.page("https://www.mcmod.cn/class/2361.html")
            fetch.assert_called_once()
        for url in ("https://evil.invalid/", "http://www.mcmod.cn/class/1.html", "https://user@www.mcmod.cn/class/1.html"):
            with self.assertRaises(LauncherError):
                self.source.fetch_page(url)

    def test_exact_name_on_later_search_page_ranks_before_addons(self):
        first = '<a href="?key=Carpet&amp;page=2">2</a>' + ''.join(
            '<a href="/class/' + str(i) + '.html">Carpet Addon ' + str(i) + '</a>' for i in range(1, 31))
        second = '<a href="/class/2361.html">Carpet</a>'
        with patch.object(self.source, "fetch_page", side_effect=[first, second]):
            results = self.source.search("Carpet")
        self.assertEqual(results[0]["id"], "2361")
        self.assertEqual(len(results), 30)


if __name__ == "__main__":
    unittest.main()

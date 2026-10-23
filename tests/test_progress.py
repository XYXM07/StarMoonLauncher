import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launcher.core import Network, Task
from launcher.progress import DownloadProgress


class ProgressTests(unittest.TestCase):
    def test_byte_weighting_inflight_updates_and_integrity_gate(self):
        task = Task("Download", None)
        task.status = "running"
        progress = DownloadProgress(task, [{"size": 100}, {"size": 900}], "Files")
        progress.file(0).complete_download(100)  # verified cache reuse
        self.assertEqual(task.public()["download"]["percent"], 10)
        progress.file(1).add_bytes(450)
        self.assertAlmostEqual(task.public()["download"]["percent"], 55)
        progress.file(1).add_bytes(450)
        self.assertLess(task.public()["download"]["percent"], 100)
        self.assertTrue(task.public()["download"]["active"])
        progress.file(1).complete_download(900)
        self.assertEqual(task.public()["download"]["percent"], 100)
        self.assertFalse(task.public()["download"]["active"])
        self.assertEqual(task.bytes, 900)  # cache credit is not network traffic

    def test_unknown_size_then_response_length_and_retry_reset(self):
        task = Task("Unknown", None)
        progress = DownloadProgress(task, [{}], "Unknown")
        file = progress.file(0)
        self.assertIsNone(task.public()["download"]["percent"])
        file.add_bytes(10)
        self.assertIsNone(task.public()["download"]["total"])
        file.set_download_size(100)
        self.assertEqual(task.public()["download"]["percent"], 10)
        file.reset_download()
        self.assertEqual(task.public()["download"]["bytes"], 0)
        file.add_bytes(100)
        file.complete_download(100)
        self.assertEqual(task.public()["download"]["bytes"], 100)
        self.assertEqual(task.bytes, 110)

    def test_network_hash_retry_does_not_count_discarded_bytes_as_completed(self):
        task = Task("Retry", None)
        payload = b"verified payload"
        attempts = []
        with tempfile.TemporaryDirectory() as directory:
            network = Network(lambda: {"source": "official"})
            target = Path(directory) / "file.jar"
            def transfer(endpoint, partial, file, size, has_hash):
                data = b"x" * len(payload) if not attempts else payload
                file.add_bytes(len(data))
                partial.write_bytes(data)
                attempts.append(task.public()["download"].copy())
                return len(data)
            with patch.object(network.engine, "fetch", side_effect=transfer), patch("launcher.core.time.sleep"):
                network.download("https://example.com/file", target, sha1=hashlib.sha1(payload).hexdigest(), size=len(payload), task=task)
            self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(s["bytes"] == len(payload) and s["percent"] < 100 for s in attempts))
        self.assertEqual(task.public()["download"]["total"], len(payload))
        self.assertEqual(task.public()["download"]["files"], 1)
        self.assertEqual(task.bytes, 2 * len(payload))

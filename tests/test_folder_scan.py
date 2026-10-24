"""Discovery uses temporary fixtures only, never the developer's disks."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from launcher.folder_scan import MinecraftFolderScanner, is_directory_link, local_scan_roots


class FolderScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def make_folder(self, name, versions=("1.20.1",)):
        root = self.root / name
        for version in versions:
            folder = root / "versions" / version
            folder.mkdir(parents=True)
            (folder / (version + ".json")).write_text('{"mainClass":"Main"}', encoding="utf-8")
        return root

    def test_nested_unicode_case_insensitive_and_empty_folders_are_read_only(self):
        first = self.make_folder("玩家/整合包/.Minecraft", ("1.20.1", "Fabric 中文"))
        nested = self.make_folder("玩家/整合包/.Minecraft/backup/.minecraft")
        empty = self.root / "空目录/.minecraft"
        empty.mkdir(parents=True)
        self.make_folder("unrelated/minecraft")
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        scanner = MinecraftFolderScanner([self.root])
        result = scanner.run()
        found = {folder["path"]: folder for folder in result["folders"]}
        self.assertEqual(set(found), {str(first), str(nested), str(empty)})
        self.assertEqual(found[str(first)]["versions"], 2)
        self.assertTrue(found[str(nested)]["available"])
        self.assertFalse(found[str(empty)]["available"])
        self.assertFalse((empty / "versions").exists())
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertTrue(scanner.snapshot()["complete"])
        self.assertFalse(result["cancelled"])

    def test_overlapping_roots_and_dot_segments_are_scanned_once(self):
        folder = self.make_folder("游戏/.minecraft")
        result = MinecraftFolderScanner([folder, self.root, self.root / "游戏/../游戏", self.root]).run()
        self.assertEqual([item["path"] for item in result["folders"]], [str(folder)])
        self.assertEqual(result["visited"], 5)

    def test_inaccessible_directory_does_not_abort_other_roots(self):
        blocked = self.root / "无权限"
        blocked.mkdir()
        good = self.make_folder("可读取/.minecraft")
        scandir = os.scandir

        def guarded(path):
            if Path(path) == blocked:
                raise PermissionError("denied")
            return scandir(path)

        with patch("launcher.folder_scan.os.scandir", side_effect=guarded):
            result = MinecraftFolderScanner([blocked, good]).run()
        self.assertEqual([item["path"] for item in result["folders"]], [str(good)])
        self.assertEqual(result["skipped"], 1)

    def test_cancel_during_traversal_retains_partial_results(self):
        folder = self.make_folder("游戏/.minecraft")
        scanner = MinecraftFolderScanner([folder])
        scandir = os.scandir

        def stop_after_discovery(path):
            if Path(path) == folder:
                scanner.cancel()
            return scandir(path)

        with patch("launcher.folder_scan.os.scandir", side_effect=stop_after_discovery):
            result = scanner.run()
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["visited"], 1)
        self.assertEqual([item["path"] for item in result["folders"]], [str(folder)])
        self.assertTrue(scanner.snapshot()["complete"])

    def test_cancel_before_start_and_empty_disks(self):
        scanner = MinecraftFolderScanner([self.root])
        scanner.cancel()
        self.assertEqual(scanner.run()["visited"], 0)
        with patch("launcher.folder_scan.local_scan_roots", return_value=[]):
            result = MinecraftFolderScanner().run()
        self.assertEqual(result["folders"], [])
        self.assertTrue(result["complete"])

    @unittest.skipUnless(os.name == "nt", "Windows drive discovery")
    def test_default_scope_includes_local_disks_and_excludes_network_and_optical_drives(self):
        kernel = SimpleNamespace(GetLogicalDrives=Mock(return_value=sum(1 << index for index in range(2, 7))),
                                 GetDriveTypeW=Mock(side_effect=lambda root: {"C": 3, "D": 2, "E": 4, "F": 5, "G": 6}[root[0]]))
        with patch("launcher.folder_scan.ctypes.windll.kernel32", kernel):
            self.assertEqual(local_scan_roots(), [Path("C:/"), Path("D:/"), Path("G:/")])

    def test_windows_junction_and_symlink_roots_are_not_followed(self):
        folder = self.make_folder("linked/.minecraft")
        metadata = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        self.assertTrue(is_directory_link(metadata))
        self.assertTrue(is_directory_link(SimpleNamespace(st_mode=stat.S_IFLNK)))
        with patch("launcher.folder_scan.Path.lstat", return_value=metadata), patch("launcher.folder_scan.os.scandir") as read:
            result = MinecraftFolderScanner([folder]).run()
        read.assert_not_called()
        self.assertEqual(result["folders"], [])

    def test_unreadable_versions_folder_still_appears_without_being_addable(self):
        folder = self.make_folder("游戏/.minecraft")
        scandir = os.scandir

        def guarded(path):
            if Path(path) == folder / "versions":
                raise PermissionError("denied")
            return scandir(path)

        with patch("launcher.folder_scan.os.scandir", side_effect=guarded):
            result = MinecraftFolderScanner([folder]).run()
        self.assertEqual(result["found"], 1)
        self.assertFalse(result["folders"][0]["available"])
        self.assertIn("无法读取", result["folders"][0]["issue"])


if __name__ == "__main__":
    unittest.main()

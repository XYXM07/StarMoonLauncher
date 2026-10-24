import ast
import json
import tempfile
import unittest
from pathlib import Path

from launcher.core import LauncherError
from launcher.i18n import language, set_language, tr
from launcher.service import Application
from launcher.translations import EN


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        set_language("zh_CN")

    def test_chinese_remains_the_default_and_round_trips(self):
        set_language("zh_CN")
        self.assertEqual(tr("设置"), "设置")
        set_language("en_US")
        self.assertEqual(tr("设置"), "Settings")
        set_language("zh_CN")
        self.assertEqual(tr("设置"), "设置")

    def test_language_is_validated_and_persisted_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "data")
            app.store.save({"offline_name": "Player", "jvm_args": "-Dworld=中文世界"})
            app.config_update({"language": "en_US"})
            self.assertEqual(Application(app.store.root).store.config()["language"], "en_US")
            self.assertEqual(app.store.config()["jvm_args"], "-Dworld=中文世界")
            with self.assertRaises(LauncherError):
                app.config_update({"language": "invalid"})
            self.assertEqual(app.store.config()["language"], "en_US")

    def test_templates_translate_formatted_backend_messages_and_preserve_codes(self):
        set_language("en_US")
        message = "Minecraft 登录失败（HTTP 403 · Invalid app registration）\nMinecraft 服务拒绝此应用注册，请检查启动器应用 ID 的 Minecraft API 访问审批。\n接口：https://api.minecraftservices.com/authentication/login_with_xbox"
        result = tr(message)
        self.assertIn("Minecraft sign-in failed", result)
        self.assertIn("HTTP 403", result)
        self.assertIn("Invalid app registration", result)
        self.assertIn("Endpoint: https://api.minecraftservices.com", result)
        self.assertNotRegex(result, r"[\u4e00-\u9fff]")
        self.assertEqual(tr("正在扫描 · 已检查 12 个目录 · 找到 3 个文件夹"), "Scanning · 12 directories checked · 3 folders found")

    def test_runtime_values_paths_and_identifiers_are_not_rewritten(self):
        set_language("en_US")
        path = "D:/游戏/设置/.minecraft/versions/中文世界"
        self.assertEqual(tr(path), path)
        self.assertEqual(tr("https://example.invalid/中文世界"), "https://example.invalid/中文世界")
        self.assertEqual(tr("下载失败：{} ({})").format("mods/中文.jar", "403"), "Download failed: mods/中文.jar (403)")

    def test_every_explicit_ui_literal_has_an_english_catalog_entry(self):
        root = Path(__file__).resolve().parents[1] / "launcher"
        missing = []
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in EN:
                        missing.append((path.name, node.lineno, arg.value))
        self.assertEqual(missing, [])

    def test_catalog_preserves_placeholder_fields(self):
        import string
        formatter = string.Formatter()
        for source, target in EN.items():
            if "{" in source:
                with self.subTest(source=source):
                    left = [(name, spec, conversion) for _, name, spec, conversion in formatter.parse(source) if name is not None]
                    right = [(name, spec, conversion) for _, name, spec, conversion in formatter.parse(target) if name is not None]
                    self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()

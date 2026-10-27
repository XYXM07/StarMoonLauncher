"""Faithful source selection and real native install routing without network."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import test_native as native
from test_faithful import catalog, pack_bytes
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from launcher.content import ContentManager
from launcher.core import read_json
from launcher.faithful import LICENSE, SITE
from launcher.i18n import set_language
from launcher.widgets import Button


class FaithfulUiTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.resize(900,600)
        self.instance=self.service.game.create("Faithful target","1.20.1","vanilla","")
        self.window.data=self.service.state()
        self.catalog_patch=patch.object(self.service.net,"json",side_effect=lambda url:copy.deepcopy(catalog()))
        self.catalog_patch.start()
        image=QImage(200,120,QImage.Format_RGB32)
        image.fill(QColor("#61a76c"))
        self.images_patch=patch.object(self.window.images,"load",side_effect=lambda url,preview:preview.set_image(image))
        self.images_patch.start()
        def search(*args,**kwargs):
            provider=kwargs.get("provider",args[5] if len(args)>5 else "modrinth")
            return ContentManager.search(self.service.content,*args,**kwargs) if provider=="faithful" else {"hits":[],"total_hits":0}
        self.real_search=patch.object(self.service.content,"search",side_effect=search)
        self.real_search.start()

    def tearDown(self):
        native.NativeTests.tearDown(self)
        self.real_search.stop()
        self.images_patch.stop()
        self.catalog_patch.stop()
        set_language("zh_CN")

    def open_faithful(self):
        self.window.explore("resourcepack",self.instance["id"])
        choices=self.window.content_provider
        self.assertGreaterEqual(choices.findData("faithful"),0)
        choices.setCurrentIndex(choices.findData("faithful"))
        native.wait_for(lambda:any(button.text() in ("选择版本","Choose version") and button.isVisible()
                                  for button in self.window.page.findChildren(Button)))
        return next(button for button in self.window.page.findChildren(Button) if button.text() in ("选择版本","Choose version") and button.isVisible())

    def test_native_resourcepack_search_links_target_and_install(self):
        choose=self.open_faithful()
        self.assertEqual(self.window.content_instance.currentData(),self.instance["id"])
        with patch("launcher.desktop.QDesktopServices.openUrl",return_value=True) as browser:
            self.window.page.findChild(Button,"faithful-website").click()
            self.assertEqual(browser.call_args.args[0].toString(),SITE)
            self.window.page.findChild(Button,"faithful-license").click()
            self.assertEqual(browser.call_args.args[0].toString(),LICENSE)
        QTest.mouseClick(choose,Qt.LeftButton)
        native.wait_for(lambda:bool(self.window.dialogs))
        dialog=self.window.dialogs[-1]
        native.wait_for(lambda:any(button.text()=="↓ 安装" and button.isVisible() for button in dialog.findChildren(Button)))
        self.assertEqual(dialog.target.currentData(),self.instance["id"])
        self.assertIsNotNone(dialog.findChild(Button,"faithful-license"))
        with patch.object(self.service.net,"download",side_effect=lambda url,path,**kwargs:Path(path).write_bytes(pack_bytes())):
            next(button for button in dialog.findChildren(Button) if button.text()=="↓ 安装" and button.isVisible()).click()
            registry=self.service.store.instance_dir(self.instance["id"])/"content.json"
            native.wait_for(lambda:registry.exists())
            native.wait_for(lambda:bool(self.service.tasks.list()) and all(item["status"]=="completed" for item in self.service.tasks.list()))
        record=read_json(registry)[0]
        self.assertEqual(record["provider"],"faithful")
        self.assertTrue((self.service.store.game_dir(self.instance["id"])/"resourcepacks"/record["filename"]).is_file())
        self.window.explore("shader")
        self.assertEqual(self.window.content_provider.findData("faithful"),-1)
        self.assertEqual(self.window.content_provider.currentData(),"modrinth")

    def test_english_source_and_license_controls(self):
        self.service.config_update({"language":"en_US"})
        self.window.apply_ui_language()
        choose=self.open_faithful()
        self.assertEqual(self.window.page.findChild(Button,"faithful-website").text(),"Faithful website")
        self.assertEqual(self.window.page.findChild(Button,"faithful-license").text(),"License")
        QTest.mouseClick(choose,Qt.LeftButton)
        native.wait_for(lambda:bool(self.window.dialogs))
        dialog=self.window.dialogs[-1]
        native.wait_for(lambda:dialog.findChild(Button,"faithful-license") is not None)
        self.assertEqual(dialog.findChild(Button,"faithful-license").text(),"License")


if __name__=="__main__":
    unittest.main()

"""Install actions stay visible at the compact size while lists scroll independently."""
import unittest
from unittest.mock import patch

import test_native as native
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from launcher.desktop import InstallDialog
from launcher.core import LauncherError
from launcher.i18n import set_language


class InstallLayoutTests(unittest.TestCase):
    def setUp(self):
        native.NativeTests.setUp(self)
        self.window.resize(900, 600)

    def tearDown(self):
        native.NativeTests.tearDown(self)
        set_language("zh_CN")

    def visible_inside(self, widget, viewport):
        rectangle = QRect(widget.mapTo(viewport, QPoint()), widget.size())
        self.assertTrue(viewport.rect().contains(rectangle), (rectangle, viewport.rect()))

    def test_version_list_fits_compact_view_and_continue_remains_at_bottom_when_resized(self):
        manifest = {"versions": [{"id":"1.21." + str(index), "type":"release"} for index in range(80)], "latest":{}}
        with patch.object(self.service.game,"manifest",return_value=manifest):
            self.window.navigate("install",animated=False)
            native.wait_for(lambda:self.window.install_list.count()==80)
        QTest.qWait(100)
        first_height = self.window.install_list.height()
        self.assertLess(first_height, 360)
        self.visible_inside(self.window.install_list,self.window.page.viewport())
        self.visible_inside(self.window.install_next,self.window.page.viewport())
        self.assertEqual(self.window.page.verticalScrollBar().maximum(),0)
        native.wheel(self.window.install_list.viewport())
        self.assertGreater(self.window.install_list.verticalScrollBar().value(),0)
        self.visible_inside(self.window.install_next,self.window.page.viewport())
        self.window.resize(1200,800)
        QTest.qWait(120)
        self.assertGreater(self.window.install_list.height(),first_height)
        self.visible_inside(self.window.install_next,self.window.page.viewport())

    def test_loader_install_button_is_beside_reselect_and_options_use_full_width(self):
        self.window.install_version = "1.20.1"
        self.window.navigate("install",animated=False)
        form = self.window.install_form
        native.wait_for(lambda:form.rows["optifine"]["versions"].count()>0)
        QTest.qWait(100)
        self.visible_inside(form.submit,self.window.page.viewport())
        before = form.submit.mapTo(self.window.page.viewport(),QPoint())
        reselect = self.window.install_reselect
        self.visible_inside(reselect,self.window.page.viewport())
        reselect_position = reselect.mapTo(self.window.page.viewport(),QPoint())
        self.assertEqual(before.y(),reselect_position.y())
        self.assertGreater(before.x(),reselect_position.x()+reselect.width())
        self.assertEqual(form.options_scroll.width(),form.width())
        self.assertGreater(form.options_scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(form.grid_columns,2)
        native.wheel(form.options_scroll.viewport(),-480)
        self.assertGreater(form.options_scroll.verticalScrollBar().value(),0)
        self.assertEqual(form.submit.mapTo(self.window.page.viewport(),QPoint()),before)
        form.rows["optifine"]["radio"].click()
        self.assertEqual(form.selected_loader,"optifine")
        self.window.resize(1200,800)
        QTest.qWait(100)
        self.assertEqual(form.selected_loader,"optifine")
        self.assertEqual(form.grid_columns,2)
        self.visible_inside(form.submit,self.window.page.viewport())
        self.assertEqual(self.window.page.verticalScrollBar().maximum(),0)

    def test_english_dialog_keeps_install_visible_and_uses_selected_loader(self):
        self.service.config_update({"language":"en_US"})
        self.window.apply_ui_language()
        self.window.install_version = "1.12.2"
        self.window.navigate("install",animated=False)
        QTest.qWait(100)
        self.visible_inside(self.window.install_form.submit,self.window.page.viewport())
        self.visible_inside(self.window.install_reselect,self.window.page.viewport())
        dialog = self.window.open_dialog(InstallDialog(self.window,"1.12.2"))
        dialog.resize(700,550)
        native.wait_for(lambda:dialog.form.rows["liteloader"]["versions"].count()>0)
        QTest.qWait(100)
        self.visible_inside(dialog.submit,dialog.scroll.viewport())
        dialog.form.options_scroll.verticalScrollBar().setValue(dialog.form.options_scroll.verticalScrollBar().maximum())
        QTest.qWait(60)
        self.visible_inside(dialog.submit,dialog.scroll.viewport())
        dialog.form.rows["liteloader"]["radio"].click()
        with patch.object(self.window,"request") as request:
            dialog.form.install()
            self.assertEqual(request.call_args.args[1]["loader"],"liteloader")
        dialog.resize(500,550)
        QTest.qWait(100)
        self.assertEqual(dialog.form.grid_columns,1)
        self.visible_inside(dialog.submit,dialog.scroll.viewport())

    def test_retry_keeps_card_geometry_and_scroll_position_during_request_and_result(self):
        self.service.config_update({"language":"en_US"})
        self.window.apply_ui_language()
        def catalogue(version, loader):
            if loader == "quilt":
                raise LauncherError("fixture unavailable")
            return [{"version":"stable-1","stable":True}]
        with patch.object(self.service.game,"loader_versions",side_effect=catalogue):
            self.window.install_version = "1.20.1"
            self.window.navigate("install",animated=False)
            form = self.window.install_form
            entry = form.rows["quilt"]
            native.wait_for(lambda:entry["retry"].isVisible())
            QTest.qWait(80)
        scroll = form.options_scroll
        scroll.ensureWidgetVisible(entry["retry"],0,0)
        QTest.qWait(80)
        for failure in (True, False):
            if not failure:
                entry["retry"].setFocus(Qt.TabFocusReason)
                QTest.qWait(80)
            before = scroll.verticalScrollBar().value()
            geometry = [QRect(frame.geometry()) for frame in form.loader_frames]
            with patch.object(self.window,"request") as request:
                if failure:
                    QTest.mouseClick(entry["retry"],Qt.LeftButton)
                else:
                    QTest.keyClick(entry["retry"],Qt.Key_Space)
                native.APP.processEvents()
                QTest.qWait(60)
                self.assertEqual(scroll.verticalScrollBar().value(),before)
                self.assertEqual([frame.geometry() for frame in form.loader_frames],geometry)
                # A user can still scroll while the remote catalogue is pending.
                scroll.verticalScrollBar().setValue(before+25)
                current = scroll.verticalScrollBar().value()
                callback = request.call_args.kwargs["error" if failure else "success"]
                callback("fixture unavailable" if failure else [{"version":"recovered-1","stable":True}])
                QTest.qWait(100)
                self.assertEqual(scroll.verticalScrollBar().value(),current)
                self.assertEqual([frame.geometry() for frame in form.loader_frames],geometry)
        self.assertEqual(entry["versions"].currentData(),"recovered-1")
        self.assertEqual(form.rows["forge"]["versions"].currentData(),"stable-1")


if __name__=="__main__":
    unittest.main()

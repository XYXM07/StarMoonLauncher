"""Native Qt interaction tests: real wheel events, page animations and dialogs."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".build-env"))
from PySide6.QtCore import QAbstractAnimation, QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QImageReader, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QWidget

from launcher.desktop import LauncherWindow, NativeDialog, MicrosoftDialog, InstallDialog, ProjectDialog, PAGES
from launcher.images import ProjectPreview
from launcher.widgets import Button, ElidedLabel, STYLE, VersionRow, WheelRouter
from launcher.service import Application


APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")
APP.setStyleSheet(STYLE)
ROUTER = WheelRouter(APP)
APP.installEventFilter(ROUTER)
APP.wheel_router = ROUTER


def wait_for(predicate, timeout=4000):
    started = time.time()
    while time.time() - started < timeout / 1000:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(15)
    raise AssertionError("Native UI condition timed out")


def wheel(widget, angle=-120, pixels=0):
    local = QPointF(15, 15)
    global_pos = QPointF(widget.mapToGlobal(QPoint(15, 15)))
    event = QWheelEvent(local, global_pos, QPoint(0, pixels), QPoint(0, angle), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    APP.sendEvent(widget, event)
    QTest.qWait(210)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.service = Application(self.root)
        self.service.store.create_minecraft()
        self.search_patch = patch.object(self.service.content, "search", return_value={"hits": [], "total_hits": 0})
        self.search_patch.start()
        self.manifest_patch = patch.object(self.service.game, "manifest", return_value={"versions": [{"id": "1.21.1", "type": "release"}], "latest": {"release": "1.21.1"}})
        self.manifest_patch.start()
        self.loaders_patch = patch.object(self.service.game, "loader_versions", return_value=[{"version": "1.0.0", "stable": True}])
        self.loaders_patch.start()
        self.window = LauncherWindow(self.root, service=self.service)
        self.window.timer.stop()
        self.window.resize(1050, 700)
        self.window.show()
        QTest.qWait(80)

    def tearDown(self):
        for dialog in list(self.window.dialogs):
            dialog.reject()
        self.window.pool.waitForDone(5000)
        self.window.images.pool.waitForDone(10000)
        APP.processEvents()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.search_patch.stop()
        self.manifest_patch.stop()
        self.loaders_patch.stop()
        self.temp.cleanup()

    def test_every_page_is_native_and_animated(self):
        for page_id, _, _ in PAGES:
            self.window.navigate(page_id)
            APP.processEvents()
            self.assertEqual(self.window.page_id, page_id)
            self.assertEqual(self.window.host.page.objectName(), "page-" + page_id)
            self.assertIsNotNone(self.window.host.animation)
            self.assertEqual(self.window.host.animation.state(), QAbstractAnimation.Running)
            QTest.qWait(430)
            self.assertAlmostEqual(self.window.host.page.graphicsEffect().opacity(), 1.0)

    def test_rapid_navigation_never_shows_detached_or_unbuilt_widgets(self):
        class ShowObserver(QObject):
            def __init__(self):
                super().__init__()
                self.detached, self.empty_pages = [], []

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Show and isinstance(watched, QWidget):
                    if watched.isWindow():
                        self.detached.append(type(watched).__name__)
                    if watched.objectName().startswith("page-") and not watched.layout.count():
                        self.empty_pages.append(watched.objectName())
                return False

        observer = ShowObserver()
        APP.installEventFilter(observer)
        try:
            for _ in range(6):
                for page_id, _, _ in PAGES:
                    self.window.navigate(page_id)
                    APP.processEvents()
                    self.assertIs(self.window.page.parentWidget(), self.window.host)
            QTest.qWait(430)
            self.assertEqual(observer.detached, [], "A page child briefly became a separate window")
            self.assertEqual(observer.empty_pages, [], "An incomplete page was made visible")
            self.assertAlmostEqual(self.window.page.graphicsEffect().opacity(), 1.0)
        finally:
            APP.removeEventFilter(observer)

    def test_mouse_wheel_touchpad_and_combo_hover_scroll_the_page(self):
        self.window.set_settings_category("download")
        self.window.page.body.setMinimumHeight(1500)
        QTest.qWait(300)
        bar = self.window.page.verticalScrollBar()
        self.assertGreater(bar.maximum(), 300)
        wheel(self.window.page.viewport())
        self.assertGreater(bar.value(), 0)
        previous = bar.value()
        source = self.window.settings_fields["source"]
        index = source.currentIndex()
        wheel(source)
        self.assertGreater(bar.value(), previous)
        self.assertEqual(source.currentIndex(), index)
        previous = bar.value()
        wheel(self.window.page.viewport(), angle=0, pixels=-37)
        self.assertEqual(bar.value(), previous + 37)
        wheel(self.window.page.viewport(), angle=120)
        self.assertLess(bar.value(), previous + 37)
        bar.setValue(bar.maximum())
        wheel(source)
        self.assertEqual(source.currentIndex(), index)

    def test_scroll_inside_dialog_and_native_version_list(self):
        manifest = {"versions": [{"id": "1.21." + str(i), "type": "release", "releaseTime": "2026-01-01"} for i in range(45)], "latest": {"release": "1.21.0"}}
        with patch.object(self.service.game, "manifest", return_value=manifest):
            self.window.navigate("install")
            wait_for(lambda: self.window.install_list.count() == 45)
            wheel(self.window.install_list.viewport())
            self.assertGreater(self.window.install_list.verticalScrollBar().value(), 0)
            dialog = self.window.open_dialog(InstallDialog(self.window, "1.21.0"))
            dialog.resize(650, 570)
            QTest.qWait(220)
            wheel(dialog.scroll.viewport())
            self.assertGreater(dialog.scroll.verticalScrollBar().value(), 0)
            self.assertIsNotNone(dialog.animation)

    def test_native_offline_account_and_settings_save(self):
        self.window.navigate("accounts")
        self.window.add_offline()
        dialog = self.window.dialogs[-1]
        name = dialog.findChild(QLineEdit, "offline-name")
        name.setText("NativeTester")
        QTest.mouseClick(dialog.findChild(Button, "offline-submit"), Qt.LeftButton)
        wait_for(lambda: bool(self.service.store.config()["accounts"]))
        self.assertEqual(self.service.store.config()["accounts"][0]["name"], "NativeTester")
        self.window.navigate("settings")
        self.window.settings_fields["width"].setValue(1600)
        self.window.flush_settings()
        wait_for(lambda: self.service.store.config()["width"] == 1600)
        self.assertEqual(self.service.store.config()["theme"], "glass")

    def test_wam_login_passes_native_window_and_cancels(self):
        begin = {"id": "mock-session", "method": "wam"}
        APP.clipboard().setText("preserved clipboard")
        with patch.object(self.service.accounts, "begin", return_value=begin) as start, patch.object(self.service.accounts, "status", return_value={"status": "pending"}) as status, patch("launcher.desktop.QDesktopServices.openUrl", return_value=True) as browser:
            dialog = self.window.open_dialog(MicrosoftDialog(self.window))
            wait_for(lambda: dialog.session_id == "mock-session")
            start.assert_called_once_with(int(dialog.winId()))
            self.assertEqual(APP.clipboard().text(), "preserved clipboard")
            browser.assert_not_called()
            self.assertIsNone(dialog.findChild(QLineEdit, "device-code"))
            dialog.reject()
            wait_for(lambda: any(call.args == ("mock-session", True) for call in status.call_args_list))

    def test_pcl_navigation_select_version_and_launch_from_left_panel(self):
        instance = self.service.game.create("Survival", "1.21.1", "vanilla")
        self.window.refresh()
        self.window.offline_name.setText("PanelTester")
        self.window.refresh()
        self.assertEqual(self.window.offline_name.text(), "PanelTester")
        choose = next(b for b in self.window.sidebar.findChildren(Button) if b.text() == "版本选择")
        QTest.mouseClick(choose, Qt.LeftButton)
        self.assertEqual(self.window.page_id, "library")
        version = next(w for w in self.window.page.findChildren(VersionRow) if w.property("instanceId") == instance["id"])
        QTest.mouseClick(version, Qt.LeftButton)
        self.assertEqual(self.window.page_id, "home")
        self.assertEqual(self.window.offline_name.text(), "PanelTester")
        with patch.object(self.window, "launch") as launch:
            QTest.mouseClick(self.window.launch_button, Qt.LeftButton)
            wait_for(lambda: launch.called)
            launch.assert_called_once_with(instance["id"])
        self.assertEqual(self.service.store.config()["accounts"][0]["name"], "PanelTester")
        QTest.mouseClick(self.window.top_buttons["download"], Qt.LeftButton)
        self.assertEqual(self.window.page_id, "install")
        wait_for(lambda: self.window.install_list.count() == 1)
        QTest.mouseClick(self.window.install_next, Qt.LeftButton)
        install = self.window.install_form
        wait_for(lambda: install.submit.isEnabled())
        self.assertEqual(install.version, "1.21.1")
        self.assertEqual(set(install.rows), set(("vanilla", "forge", "neoforge", "fabric", "quilt")))

    def test_settings_draft_survives_category_switch(self):
        self.window.navigate("settings")
        self.window.settings_fields["width"].setValue(1700)
        self.window.set_settings_category("download")
        self.assertEqual(self.window.settings_fields["width"].value(), 1700)
        self.assertFalse(self.window.settings_fields["width"].isVisible())
        self.window.settings_fields["source"].setCurrentIndex(1)
        self.window.set_settings_category("appearance")
        self.window.animations_check.setChecked(False)
        self.window.flush_settings()
        wait_for(lambda: self.service.store.config()["width"] == 1700)
        self.assertEqual(self.service.store.config()["source"], "official")
        self.assertFalse(self.service.store.config()["animations"])

    def test_frameless_titlebar_and_version_favorites(self):
        self.assertTrue(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(self.window.testAttribute(Qt.WA_TranslucentBackground))
        self.assertEqual(self.window.windowTitle(), "星月启动器")
        QTest.mouseClick(self.window.titlebar.maximize, Qt.LeftButton)
        QTest.qWait(60)
        self.assertTrue(self.window.isMaximized())
        QTest.mouseClick(self.window.titlebar.maximize, Qt.LeftButton)
        QTest.qWait(60)
        self.assertFalse(self.window.isMaximized())
        instance = self.service.game.create("Favorite", "1.21.1", "fabric")
        self.window.refresh()
        self.window.navigate("library")
        star = next(b for b in self.window.page.findChildren(Button) if b.text() == "☆")
        QTest.mouseClick(star, Qt.LeftButton)
        self.assertTrue(self.service.store.instance(instance["id"])["favorite"])
        row = self.window.page.findChildren(VersionRow)[0]
        row.setFocus()
        QTest.keyClick(row, Qt.Key_Return)
        self.assertEqual(self.window.page_id, "home")
        self.assertEqual(self.service.store.config()["selected_instance"], instance["id"])

    def test_secondary_window_close_control_is_centered_and_clear_of_content(self):
        for width, height in ((460, 300), (680, 540), (900, 650)):
            dialog = self.window.open_dialog(NativeDialog(self.window, "二级窗口位置检查", width=width))
            dialog.resize(width, height)
            QTest.qWait(30)
            close = dialog.findChild(Button, "window-close")
            header = dialog.titlebar
            # The painted glass excludes an asymmetric shadow, so the widget's
            # rectangle is not the visible title bar's center.
            visible_bar = QRectF(header.rect()).adjusted(4, 3, -4, -7)
            self.assertAlmostEqual(QRectF(close.geometry()).center().y(), visible_bar.center().y(), delta=.5)
            image = close.grab().toImage()
            ink = []
            for y in range(image.height()):
                for x in range(image.width()):
                    pixel = image.pixelColor(x, y)
                    if pixel.red() < 210 and pixel.red() > pixel.green() + 40 and pixel.red() > pixel.blue() + 20:
                        ink.append((x, y))
            self.assertTrue(ink, "The close symbol was not rendered")
            scale = image.height() / close.height()
            ink_y = close.y() + (min(y for _, y in ink) + max(y for _, y in ink) + 1) / (2 * scale)
            self.assertAlmostEqual(ink_y, visible_bar.center().y(), delta=.5)
            ink_x = (min(x for x, _ in ink) + max(x for x, _ in ink) + 1) / (2 * image.width() / close.width())
            self.assertAlmostEqual(ink_x, close.width() / 2, delta=.5)
            self.assertLessEqual(close.mapTo(dialog, QPoint()).y(), 12)
            self.assertLessEqual(header.geometry().bottom(), dialog.scroll.geometry().top())
            self.assertEqual(close.accessibleName(), "关闭")
            QTest.mouseClick(close, Qt.LeftButton)
            self.assertFalse(dialog.isVisible())

    def test_custom_background_is_copied_and_applied_immediately(self):
        source = self.root / "custom.png"
        image = QImage(360,240,QImage.Format_RGB32)
        image.fill(QColor("#5c8fe0"))
        image.save(str(source),"PNG")
        self.window.set_settings_category("appearance")
        with patch("launcher.desktop.QFileDialog.getOpenFileName",return_value=(str(source),"")):
            self.window.choose_background()
        wait_for(lambda: bool(self.service.store.config()["background_path"]))
        destination = Path(self.service.store.config()["background_path"])
        self.assertTrue(destination.is_file())
        self.assertNotEqual(source,destination)
        source.unlink()
        self.assertFalse(self.window.glass_surface.image.isNull())
        self.window.glass_opacity.setValue(55)
        self.window.glass_refraction.setValue(180)
        self.window.background_opacity.setValue(70)
        self.assertEqual(APP.property("glassRefraction"),180)
        wait_for(lambda:self.service.store.config()["glass_opacity"]==55)
        self.assertEqual(self.service.store.config()["glass_refraction"],180)
        self.window.reset_background()
        self.assertEqual(self.service.store.config()["background_path"],"")
        self.assertEqual(self.service.store.config()["background_mode"],"default")
        self.assertFalse(self.window.glass_surface.image.isNull())
        self.window.clear_background()
        self.assertEqual(self.service.store.config()["background_mode"],"none")
        self.assertTrue(self.window.glass_surface.image.isNull())

    def test_live_global_settings_persist_without_save_or_exit_prompt(self):
        self.window.navigate("settings",animated=False)
        self.assertFalse(any(b.text().startswith("\u4fdd\u5b58") for b in self.window.sidebar.findChildren(Button)))
        self.window.settings_fields["width"].setValue(1800)
        self.assertEqual(self.service.store.config()["width"],1800)
        self.window.settings_fields["java_path"].setText("fixture-java.exe")
        self.window.navigate("home",animated=False)
        self.assertEqual(self.window.page_id,"home")
        self.assertEqual(self.service.store.config()["java_path"],"fixture-java.exe")
        self.window.set_settings_category("advanced")
        self.window.settings_fields["jvm_args"].setPlainText("-Dfixture=true")
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.service.store.config()["jvm_args"],"-Dfixture=true")
        restarted = Application(self.service.store.root)
        self.assertEqual(restarted.store.config()["width"],1800)

    def test_live_setting_failure_isolated_and_retry_preserves_value(self):
        self.window.navigate("settings",animated=False)
        update = self.service.config_update
        def failing(changes):
            if "width" in changes: raise OSError("fixture disk failure")
            return update(changes)
        with patch.object(self.service,"config_update",side_effect=failing):
            self.window.settings_fields["width"].setValue(1800)
            self.window.java_auto.setChecked(False)
            self.assertFalse(self.service.store.config()["java_auto"])
            self.assertEqual(self.window.setting_changes["width"],1800)
        self.assertTrue(self.window.flush_settings())
        self.assertEqual(self.service.store.config()["width"],1800)
        self.window.set_settings_category("ai")
        self.window.settings_fields["ai_base_url"].setText("not-a-valid-url")
        self.assertFalse(self.window.flush_settings())
        self.window.ai_preflight_check.setChecked(True)
        self.assertTrue(self.service.store.config()["ai_auto_preflight"])
        self.window.settings_fields["ai_base_url"].setText("http://127.0.0.1:12345/v1")
        self.assertTrue(self.window.flush_settings())

    def test_top_navigation_is_exclusive_switch_with_subpages_and_keyboard(self):
        switch = self.window.navigation_switch
        self.assertTrue(self.window.top_buttons["launch"].isChecked())
        for key,page in (("download","install"),("settings","settings"),("more","accounts"),("launch","home")):
            button = self.window.top_buttons[key]
            self.assertTrue(button.isCheckable())
            QTest.mouseClick(button,Qt.LeftButton)
            self.assertEqual(self.window.page_id,page)
            self.assertEqual(sum(b.isChecked() for b in self.window.top_buttons.values()),1)
            self.assertTrue(button.isChecked())
            current_page = self.window.page
            QTest.mouseClick(button,Qt.LeftButton)
            self.assertIs(self.window.page,current_page)
        for page,key in (("library","launch"),("discover","download"),("downloads","more"),("about","more")):
            self.window.navigate(page,animated=False)
            self.assertTrue(self.window.top_buttons[key].isChecked())
        self.window.top_buttons["settings"].setFocus(Qt.TabFocusReason)
        QTest.keyClick(self.window.top_buttons["settings"],Qt.Key_Space)
        self.assertEqual(self.window.page_id,"settings")
        self.window.animations_check.setChecked(False)
        self.window.navigate("home")
        self.assertEqual(switch.motion.state(),QAbstractAnimation.Stopped)

    def test_instance_settings_apply_on_edit_tab_and_close_without_save_button(self):
        instance = self.service.game.create("Original","1.20.1","forge")
        self.window.open_instance(instance["id"])
        self.window.set_instance_category("settings")
        panel = self.window.instance_panel
        self.assertFalse(any(b.text().startswith("\u4fdd\u5b58") for b in panel.findChildren(Button)))
        panel.instance_fields["name"].setText("Edited")
        self.window.set_instance_category("mods")
        self.assertEqual(self.service.store.instance(instance["id"])["name"],"Edited")
        self.window.set_instance_category("settings")
        panel = self.window.instance_panel
        panel.instance_fields["memory_mode"].setCurrentIndex(panel.instance_fields["memory_mode"].findData("manual"))
        panel.instance_fields["memory"].setValue(6144)
        self.assertEqual(self.service.store.instance(instance["id"])["memory"],6144)
        panel.instance_fields["name"].setText("Saved on close")
        self.window.close()
        self.assertEqual(self.service.store.instance(instance["id"])["name"],"Saved on close")

    def test_background_reset_cancels_pending_image_and_navigation_keeps_choice(self):
        self.window.set_settings_category("appearance")
        source = self.root/"wallpaper.png"
        image = QImage(24,24,QImage.Format_RGB32)
        image.fill(QColor("blue")); image.save(str(source))
        with patch("launcher.desktop.QFileDialog.getOpenFileName",return_value=(str(source),"")):
            self.window.choose_background()
        self.window.reset_background()
        self.window.pool.waitForDone(5000)
        APP.processEvents()
        self.assertEqual(self.service.store.config()["background_path"],"")
        with patch("launcher.desktop.QFileDialog.getOpenFileName",return_value=(str(source),"")):
            self.window.choose_background()
        self.window.navigate("home",animated=False)
        wait_for(lambda:bool(self.service.store.config()["background_path"]))
        self.assertFalse(self.window.glass_surface.image.isNull())

    def test_abnormal_game_exit_has_notification_and_log_entry(self):
        instance = self.service.game.create("Broken Forge", "1.20.1", "forge")
        process = {"instance_id": instance["id"], "pid": 123, "started": 99,
                   "running": False, "exit_code": 1, "finalized": True}
        with patch.object(self.service.game, "process_status", return_value=[process]):
            self.window.refresh()
            self.assertIn("代码 1", self.window.toast_label.text())
            self.window.navigate("downloads", animated=False)
            self.assertIn("1", self.window.process_rows[instance["id"]][0].text())
            logs = next(b for b in self.window.page.findChildren(Button) if b.text() == "查看日志")
            QTest.mouseClick(logs, Qt.LeftButton)
            self.assertEqual(self.window.page_id, "instance")
            self.assertEqual(self.window.instance_category, "logs")

    def test_glass_blurs_background_and_reuses_cache_across_navigation(self):
        source = self.root / "checker.png"
        image = QImage(256, 256, QImage.Format_RGB32)
        for x in range(256):
            for y in range(256):
                image.setPixelColor(x, y, QColor("#ffffff" if (x//8+y//8) % 2 else "#24559a"))
        image.save(str(source), "PNG")
        surface = self.window.glass_surface
        self.service.config_update({"background_path": str(source), "background_opacity": 100})
        self.window.data = self.service.state()
        self.window.apply_appearance()
        sharp = surface.backdrop().toImage()
        blurred = surface.backdrop(True).toImage()
        y = sharp.height()//2
        def edge_energy(image):
            return sum((image.pixelColor(x,y).red()-image.pixelColor(x-1,y).red())**2 for x in range(1,image.width()))
        self.assertLess(edge_energy(blurred), edge_energy(sharp)*.5)
        key = surface.backdrop(True).cacheKey()
        self.window.navigate("home")
        APP.processEvents()
        self.assertEqual(surface.backdrop(True).cacheKey(), key)
        self.window.resize(1100, 720)
        APP.processEvents()
        self.assertNotEqual(surface.backdrop(True).cacheKey(), key)

    def test_wallpaper_all_main_pages_dialog_exclusion_and_transparent_corners(self):
        source = self.root / "home-only.png"
        image = QImage(120, 80, QImage.Format_RGB32)
        image.fill(QColor("#e86bb2"))
        image.save(str(source))
        self.service.config_update({"background_path": str(source)})
        self.window.data = self.service.state()
        self.window.navigate("home")
        self.assertFalse(self.window.glass_surface.image.isNull())
        for page, _, _ in PAGES:
            if page == "home":
                continue
            self.window.navigate(page, animated=False)
            self.assertFalse(self.window.glass_surface.image.isNull(), page)
        dialog = self.window.open_dialog(NativeDialog(self.window, "Glass"))
        self.assertTrue(dialog.glass_surface.image.isNull())
        for widget in (self.window, dialog):
            APP.processEvents()
            capture = widget.grab().toImage()
            for x, y in ((0,0), (capture.width()-1,0), (0,capture.height()-1), (capture.width()-1,capture.height()-1)):
                self.assertEqual(capture.pixelColor(x,y).alpha(), 0)
            self.assertFalse(widget.mask().contains(QPoint(0,0)))
        dialog.reject()
        self.window.navigate("home")
        self.assertFalse(self.window.glass_surface.image.isNull())
        surface = self.window.glass_surface
        surface.set_glass_opacity(35)
        low = surface.backdrop().toImage().pixelColor(10,10).alpha()
        surface.set_glass_opacity(95)
        self.assertGreater(surface.backdrop().toImage().pixelColor(10,10).alpha(), low)

    def test_refraction_changes_edge_sampling_without_distorting_center(self):
        source = self.root / "refraction.png"
        image = QImage(256, 256, QImage.Format_RGB32)
        for x in range(256):
            for y in range(256):
                image.setPixelColor(x,y,QColor("#effaff" if (x//6+y//6) % 2 else "#366fc9"))
        image.save(str(source))
        self.service.config_update({"background_path": str(source), "background_opacity": 100})
        self.window.data = self.service.state()
        self.window.navigate("home", animated=False)
        QApplication.instance().setProperty("glassRefraction", 0)
        plain = self.window.sidebar.grab().toImage()
        QApplication.instance().setProperty("glassRefraction", 100)
        refracted = self.window.sidebar.grab().toImage()
        self.assertNotEqual(plain, refracted)
        self.assertEqual(plain.pixelColor(plain.width()//2,plain.height()//2), refracted.pixelColor(plain.width()//2,plain.height()//2))
        QApplication.instance().setProperty("glassRefraction", 200)
        extended = self.window.sidebar.grab().toImage()
        self.assertNotEqual(refracted,extended)
        self.assertEqual(plain.pixelColor(plain.width()//2,plain.height()//2),extended.pixelColor(plain.width()//2,plain.height()//2))

    def test_slide_direction_category_motion_and_animation_opt_out(self):
        self.window.navigate("settings")
        self.assertGreater(self.window.page.x(), 0)
        QTest.qWait(430)
        self.assertEqual(self.window.page.pos(), QPoint())
        self.window.set_settings_category("appearance")
        self.assertGreater(self.window.page.y(), 0)
        self.window.set_settings_category("game")
        self.assertLess(self.window.page.y(), 0)
        QTest.qWait(430)
        self.window.navigate("home")
        self.assertLess(self.window.page.x(), 0)
        self.window.animations = False
        self.window.navigate("settings")
        self.assertIsNone(self.window.host.animation)
        self.assertIsNone(self.window.sidebar.graphicsEffect())
        self.assertEqual(self.window.page.pos(), QPoint())

    def test_download_speed_is_displayed_for_running_tasks(self):
        from launcher.core import Task
        task = Task("Download", lambda t: None)
        task.status = "running"
        task.add_bytes(1024*1024)
        self.service.tasks.tasks[task.id] = task
        self.window.refresh()
        self.window.navigate("downloads", animated=False)
        info = self.window.task_rows[task.id][3]
        self.assertIn("MB/s", info.text())
        self.assertIn("/s", self.window.task_bar.text())
        task.status = "completed"
        task.finished = time.time()
        self.window.refresh()
        self.assertNotIn("/s", info.text())

    def test_byte_progress_bar_unknown_total_and_processing_state(self):
        from launcher.core import Task
        from launcher.progress import DownloadProgress
        task = Task("Progress", None)
        task.status = "running"
        self.service.tasks.tasks[task.id] = task
        batch = DownloadProgress(task, [{"size": 100}, {"size": 900}], "Files")
        batch.file(0).complete_download(100)
        batch.file(1).add_bytes(450)
        self.window.refresh()
        self.window.navigate("downloads", animated=False)
        bar = self.window.task_rows[task.id][2]
        self.assertEqual(bar.value(), 550)
        self.assertIn("文件 1 / 2", self.window.task_rows[task.id][3].text())
        self.assertIn("55.0%", self.window.task_bar.text())
        unknown = DownloadProgress(task, [{}], "Unknown")
        unknown.file(0).add_bytes(10)
        self.window.refresh()
        self.assertEqual(bar.maximum(), 0)
        self.assertIn("大小未知", self.window.task_rows[task.id][3].text())
        task.update("解压文件", 99)
        self.window.refresh()
        self.assertEqual(bar.maximum(), 0)
        task.status = "completed"
        task.finished = time.time()
        self.window.refresh()
        self.assertEqual(bar.value(), 1000)

    def test_add_minecraft_folder_ui_selects_and_opens_original_version(self):
        from launcher.core import atomic_json
        original = self.root / "original/.minecraft"
        version = original / "versions/1.20.1"
        version.mkdir(parents=True)
        atomic_json(version / "1.20.1.json", {"id": "1.20.1", "mainClass": "Main", "libraries": []})
        (version / "1.20.1.jar").write_bytes(b"original")
        self.window.navigate("library", animated=False)
        add = next(b for b in self.window.sidebar.findChildren(Button) if b.text() == "添加 .minecraft 文件夹")
        with patch("launcher.desktop.QFileDialog.getExistingDirectory", return_value=str(original)):
            QTest.mouseClick(add, Qt.LeftButton)
        wait_for(lambda: self.window.data["minecraft_path"] == str(original))
        wait_for(lambda: bool(self.window.page.findChildren(VersionRow)))
        self.assertEqual(self.window.library_folders.currentData(), str(original))
        QTest.mouseClick(self.window.page.findChildren(VersionRow)[0], Qt.LeftButton)
        self.assertEqual(self.window.page_id, "home")
        selected = self.window.selected_instance()
        self.assertEqual(self.service.store.game_dir(selected["id"]), original)
        self.window.navigate("library", animated=False)
        self.window.library_folders.setCurrentIndex(0)
        wait_for(lambda: self.window.data["minecraft_path"] == str(self.service.store.minecraft_root))
        self.assertFalse(self.window.page.findChildren(VersionRow))

    def test_scan_folders_selects_multiple_roots_and_keeps_original_game_files(self):
        from launcher.core import atomic_json
        base = self.root / "扫描范围"
        roots = [base / "整合包 A/.Minecraft", base / "游戏 B/.minecraft"]
        for original in roots:
            version = original / "versions/1.20.1"
            version.mkdir(parents=True)
            atomic_json(version / "1.20.1.json", {"id": "1.20.1", "mainClass": "Main", "libraries": []})
            (version / "1.20.1.jar").write_bytes(b"original")
        empty = base / "空目录/.minecraft"
        empty.mkdir(parents=True)
        self.window.navigate("library", animated=False)
        scan = self.window.sidebar.findChild(Button, "scan-minecraft-folders")
        with patch("launcher.folder_scan.local_scan_roots", return_value=[base]):
            QTest.mouseClick(scan, Qt.LeftButton)
            dialog = next(d for d in self.window.dialogs if d.objectName() == "minecraft-folder-scan")
            wait_for(lambda: dialog.results.count() == 3)
        self.assertEqual(len(dialog.selected_paths()), 2)
        self.assertFalse(self.service.store.config()["minecraft_folders"])
        dialog.select_all.setChecked(False)
        self.assertFalse(dialog.add.isEnabled())
        dialog.select_all.setChecked(True)
        self.assertEqual(len(dialog.selected_paths()), 2)
        QTest.mouseClick(dialog.add, Qt.LeftButton)
        wait_for(lambda: len(self.service.store.config()["minecraft_folders"]) == 2)
        wait_for(lambda: not dialog.isVisible())
        self.assertEqual(self.window.page_id, "library")
        external = [instance for instance in self.service.store.instances() if instance.get("external_minecraft")]
        self.assertEqual(len(external), 2)
        self.assertEqual({self.service.store.game_dir(instance["id"]) for instance in external}, set(roots))
        self.assertFalse((empty / "versions").exists())
        self.assertTrue(all((root / "versions/1.20.1/1.20.1.jar").read_bytes() == b"original" for root in roots))
        # A second scan marks registered folders and never adds duplicate entries.
        with patch("launcher.folder_scan.local_scan_roots", return_value=[base]):
            again = self.window.scan_minecraft_folders()
            wait_for(lambda: again.results.count() == 3)
        self.assertFalse(again.selected_paths())
        self.assertFalse(again.add.isEnabled())
        self.assertEqual(sum("已添加" in again.results.item(i).text() for i in range(3)), 2)
        again.reject()

    def test_scan_stop_close_and_scope_selection_keep_interface_responsive(self):
        from launcher.folder_scan import MinecraftFolderScanner
        actual_run = MinecraftFolderScanner.run

        def delayed_run(scanner):
            scanner.cancelled.wait(3)
            return actual_run(scanner)

        with patch("launcher.folder_scan.local_scan_roots", return_value=[self.root]), patch.object(MinecraftFolderScanner, "run", delayed_run):
            dialog = self.window.scan_minecraft_folders()
            QTest.mouseClick(dialog.stop, Qt.LeftButton)
            wait_for(lambda: dialog.all_disks.isEnabled())
            self.assertIn("已停止", dialog.status.text())
            self.assertTrue(dialog.scanner.cancelled.is_set())
        empty = self.root / "指定目录/.minecraft"
        (empty / "versions").mkdir(parents=True)
        with patch("launcher.desktop.QFileDialog.getExistingDirectory", return_value=str(empty.parent)):
            QTest.mouseClick(dialog.choose_scope, Qt.LeftButton)
            wait_for(lambda: dialog.results.count() == 1)
        self.assertEqual(dialog.selected_paths(), [str(empty)])
        dialog.resize(460, 420)
        QTest.qWait(30)
        self.assertEqual(dialog.scope.full_text, str(empty.parent))
        self.assertEqual(dialog.scope.toolTip(), str(empty.parent))
        self.assertTrue(dialog.scope.text())
        self.assertNotEqual(dialog.scope.text(), "本机磁盘")
        dialog.reject()
        with patch("launcher.folder_scan.local_scan_roots", return_value=[self.root]), patch.object(MinecraftFolderScanner, "run", delayed_run):
            closing = self.window.scan_minecraft_folders()
            closing.reject()
            wait_for(lambda: closing.scanner.snapshot()["complete"])
            self.assertTrue(closing.scanner.cancelled.is_set())
        self.assertFalse(self.service.store.config()["minecraft_folders"])

    def test_scan_batch_import_retains_successes_and_reports_failed_folders(self):
        from launcher.core import LauncherError, atomic_json
        from launcher.folders import add_folder
        base = self.root / "扫描范围"
        first, second = base / "A/.minecraft", base / "B/.minecraft"
        for original in (first, second):
            path = original / "versions/1.20.1/1.20.1.json"
            atomic_json(path, {"id": "1.20.1", "mainClass": "Main", "libraries": []})

        def import_one(manager, path, select=True):
            if Path(path) == second:
                raise LauncherError("文件夹已被移动")
            return add_folder(manager, path, select)

        with patch("launcher.folder_scan.local_scan_roots", return_value=[base]):
            dialog = self.window.scan_minecraft_folders()
            wait_for(lambda: dialog.results.count() == 2)
        with patch("launcher.folders.add_folder", side_effect=import_one):
            QTest.mouseClick(dialog.add, Qt.LeftButton)
            wait_for(lambda: "文件夹已被移动" in dialog.error_label.text())
        self.assertEqual(self.service.store.config()["minecraft_folders"], [str(first)])
        self.assertEqual(dialog.selected_paths(), [str(second)])
        self.assertTrue(dialog.add.isEnabled())
        QTest.mouseClick(dialog.add, Qt.LeftButton)
        wait_for(lambda: not dialog.isVisible())
        self.assertEqual(len(self.service.store.config()["minecraft_folders"]), 2)

    def test_dropdown_search_keyboard_and_no_orphan_popup(self):
        from launcher.combobox import GlassComboBox
        self.window.set_settings_category("download")
        QTest.qWait(430)
        control = self.window.settings_fields["source"]
        self.assertIsInstance(control,GlassComboBox)
        control.clear()
        for i in range(40): control.addItem("Option "+str(i),i)
        QTest.mouseClick(control,Qt.LeftButton)
        QTest.qWait(170)
        self.assertTrue(control.popup.isVisible())
        self.assertTrue(control.search.isVisible())
        control.search.setText("Option 23")
        self.assertEqual(control.proxy.rowCount(),1)
        QTest.keyClick(control.search,Qt.Key_Down)
        QTest.keyClick(control.menu,Qt.Key_Return)
        self.assertEqual(control.currentData(),23)
        self.assertFalse(control.popup.isVisible())
        control.showPopup()
        self.window.navigate("home",animated=False)
        self.assertFalse(control.popup.isVisible())

    def test_dropdown_wheel_stays_in_popup_and_fits_screen(self):
        self.window.set_settings_category("download")
        self.window.page.body.setMinimumHeight(1400)
        control = self.window.settings_fields["source"]
        for i in range(35): control.addItem("Long menu item "+str(i),i)
        QTest.qWait(430)
        pagebar = self.window.page.verticalScrollBar()
        control.showPopup()
        QTest.qWait(170)
        before = pagebar.value()
        wheel(control.menu.viewport())
        self.assertGreater(control.menu.verticalScrollBar().value(),0)
        self.assertEqual(pagebar.value(),before)
        self.assertTrue(control.screen().availableGeometry().contains(control.popup.geometry()))
        QTest.keyClick(control.menu,Qt.Key_Escape)
        self.assertFalse(control.popup.isVisible())

    def test_dropdown_first_open_layout_and_repeated_anchor_toggle(self):
        self.window.set_settings_category("download")
        QTest.qWait(430)
        control = self.window.settings_fields["source"]
        for _ in range(5):
            QTest.mouseClick(control, Qt.LeftButton)
            QTest.qWait(160)
            self.assertTrue(control.popup.isVisible())
            rect = control.menu.visualRect(control.menu.currentIndex())
            self.assertGreater(rect.height(), 0)
            self.assertTrue(control.menu.viewport().rect().intersects(rect))
            QTest.mouseClick(control, Qt.LeftButton)
            APP.processEvents()
            self.assertFalse(control.popup.isVisible(), "Clicking the anchor reopens the popup")
        control.showPopup()
        # Native Popup routing sends anchor clicks as an outside popup press.
        anchor = control.mapToGlobal(control.rect().center())
        QTest.mouseClick(control.popup, Qt.LeftButton, pos=control.popup.mapFromGlobal(anchor))
        APP.processEvents()
        self.assertFalse(control.popup.isVisible())
        QTest.mouseClick(control, Qt.LeftButton)
        self.assertTrue(control.popup.isVisible())

    def test_offline_skin_ui_import_avatar_and_reset(self):
        account=self.service.accounts.offline("SkinPreview")
        self.window.refresh()
        source=self.root/"preview.png"
        image=QImage(64,64,QImage.Format_ARGB32)
        image.fill(QColor("#e4608f"))
        image.save(str(source))
        self.window.edit_skin(account["id"])
        dialog=self.window.dialogs[-1]
        with patch("launcher.desktop.QFileDialog.getOpenFileName",return_value=(str(source),"")):
            dialog.choose()
        self.assertFalse(dialog.preview.image.isNull())
        dialog.model.setCurrentIndex(1)
        QTest.mouseClick(dialog.submit,Qt.LeftButton)
        wait_for(lambda: bool(self.service.skins.account(account["id"]).get("skin_path")))
        self.assertEqual(self.service.skins.account(account["id"])["skin_model"],"slim")
        self.window.navigate("home",animated=False)
        self.assertFalse(self.window.avatar_label.pixmap().isNull())
        self.window.edit_skin(account["id"])
        dialog=self.window.dialogs[-1]
        dialog.reset()
        QTest.mouseClick(dialog.submit,Qt.LeftButton)
        wait_for(lambda: "skin_path" not in self.service.skins.account(account["id"]))

    def test_running_page_closes_finished_items_without_cancelling_active(self):
        import threading
        first = self.service.tasks.submit("Finished One", lambda t: None)["task_id"]
        second = self.service.tasks.submit("Finished Two", lambda t: None)["task_id"]
        wait_for(lambda: all(t["status"] == "completed" for t in self.service.tasks.list()))
        gate = threading.Event()
        active = self.service.tasks.submit("Still running", lambda t: gate.wait(6))["task_id"]
        try:
            self.window.refresh()
            self.window.navigate("downloads")
            QTest.qWait(100)
            headings = [w for w in self.window.page.findChildren(ElidedLabel) if w.full_text == "Finished One"]
            self.assertEqual(len(headings), 1)
            self.assertGreater(headings[0].width(), 50)
            self.assertTrue(headings[0].text())
            close = self.window.page.findChild(Button, "close-task-" + first)
            QTest.mouseClick(close, Qt.LeftButton)
            wait_for(lambda: first not in self.service.tasks.tasks)
            QTest.mouseClick(self.window.clear_finished_button, Qt.LeftButton)
            wait_for(lambda: second not in self.service.tasks.tasks)
            self.assertIn(active, self.service.tasks.tasks)
            self.assertFalse(self.service.tasks.tasks[active].cancel.is_set())
        finally:
            gate.set()
            wait_for(lambda: self.service.tasks.tasks[active].finished is not None)
            self.window.refresh()

    def test_auto_memory_controls_and_instance_override(self):
        self.window.navigate("settings")
        self.assertTrue(self.window.memory_auto_check.isChecked())
        self.assertFalse(self.window.memory_slider.isEnabled())
        self.window.memory_auto_check.setChecked(False)
        self.window.memory_slider.setValue(6144)
        self.window.flush_settings()
        wait_for(lambda: self.service.store.config()["memory"] == 6144)
        self.assertFalse(self.service.store.config()["memory_auto"])
        instance = self.service.game.create("Independent", "1.21.1")
        self.window.refresh()
        self.window.open_instance(instance["id"])
        self.assertFalse(any(b.text() == "游戏目录" for b in self.window.sidebar.findChildren(Button)))
        self.assertEqual(sum(b.text() == "游戏目录" for b in self.window.instance_panel.findChildren(Button)), 1)
        self.assertFalse(any(b.text() in ("模组管理", "版本设置", "存档管理") for b in self.window.instance_panel.findChildren(Button)))
        self.window.set_instance_category("settings")
        panel = self.window.instance_panel
        panel.instance_fields["memory_mode"].setCurrentIndex(1)
        self.assertFalse(panel.instance_fields["memory"].isEnabled())
        self.window.flush_instance_settings()
        wait_for(lambda: self.service.store.instance(instance["id"]).get("memory_mode") == "auto")

    def test_closing_during_a_game_keeps_log_reader_alive_in_background(self):
        with patch.object(self.service.game, "process_status", return_value=[{"instance_id": "fake", "running": True}]):
            self.window.refresh()
            self.window.close()
            APP.processEvents()
            self.assertTrue(self.window.background_mode)
            self.assertFalse(self.window.isVisible())
            self.window.restore_window()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.background_mode)

    def test_instance_content_logs_and_settings_remain_available(self):
        instance = self.service.game.create("Native Instance", "1.21.1", "fabric")
        directory = self.service.store.instance_dir(instance["id"])
        mods = directory / "game" / "mods"
        mods.mkdir()
        (mods / "example.jar").write_bytes(b"native-test")
        self.window.refresh()
        self.window.open_instance(instance["id"])
        QTest.mouseClick(self.window.sidebar.findChild(Button, "instance-mods"), Qt.LeftButton)
        panel = self.window.instance_panel
        self.assertEqual(self.window.page_id, "instance")
        wait_for(lambda: any(button.text() == "禁用" for button in panel.findChildren(Button)))
        toggle = next(button for button in panel.findChildren(Button) if button.text() == "禁用")
        QTest.mouseClick(toggle, Qt.LeftButton)
        # The normal refresh timer is stopped in this fixture. A rename can be
        # visible before the worker marks its task completed; wait for both.
        wait_for(lambda: (mods / "example.jar.disabled").exists() and
                 all(task["status"] == "completed" for task in self.service.tasks.list()))
        self.window.refresh()
        wait_for(lambda: any(b.text() == "启用" for b in panel.findChildren(Button)))
        QTest.mouseClick(self.window.sidebar.findChild(Button, "instance-logs"), Qt.LeftButton)
        panel = self.window.instance_panel
        wait_for(lambda: bool(panel.log.toPlainText()))
        self.assertIn("还没有日志", panel.log.toPlainText())
        QTest.mouseClick(self.window.sidebar.findChild(Button, "instance-settings"), Qt.LeftButton)
        panel = self.window.instance_panel
        panel.instance_fields["name"].setText("Renamed Native Instance")
        self.window.flush_instance_settings()
        wait_for(lambda: self.service.store.instance(instance["id"])["name"] == "Renamed Native Instance")

    def test_modpack_rows_fit_small_window_and_show_cached_images(self):
        self.service.game.create("Long target instance " * 8, "1.21.1", "fabric")
        self.window.refresh()
        url = "https://cdn.modrinth.com/mock/icon.png"
        path = self.window.images.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(160, 90, QImage.Format_ARGB32)
        image.fill(QColor("#568955"))
        self.assertTrue(image.save(str(path), "PNG"))
        hits = [{"project_id": str(i), "title": "Very long modpack title " * 8, "description": "A detailed description " * 30, "author": "Author " * 8, "downloads": 12345, "icon_url": url} for i in range(3)]
        with patch.object(self.service.content, "search", return_value={"hits": hits, "total_hits": 3}):
            self.window.resize(900, 600)
            self.window.explore("modpack")
            wait_for(lambda: len(self.window.page.findChildren(ProjectPreview)) == 3)
            previews = self.window.page.findChildren(ProjectPreview)
            wait_for(lambda: all(p.property("loaded") for p in previews))
            QTest.qWait(280)
            self.assertFalse(self.window.content_instance.isVisible())
            self.assertLessEqual(self.window.page.body.width(), self.window.page.viewport().width())
            for preview in previews:
                self.assertEqual(preview.width(), preview.height())
                self.assertGreater(preview.image.width(), preview.image.height())
            self.assertEqual(len(self.window.images.cache), 1)

    def test_image_fetch_is_async_deduplicated_and_failure_uses_placeholder(self):
        import io
        import threading
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        image = QImage(24, 24, QImage.Format_ARGB32)
        image.fill(QColor("#123456"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        gate = threading.Event()
        url = "https://cdn.modrinth.com/mock/download.png"
        class Response(io.BytesIO):
            def geturl(self):
                return url
        def fetch(*args, **kwargs):
            gate.wait(3)
            return Response(bytes(payload))
        with patch("launcher.images.urllib.request.urlopen", side_effect=fetch) as request:
            first = ProjectPreview(self.window.images, url, parent=self.window)
            second = ProjectPreview(self.window.images, url, parent=self.window)
            self.assertFalse(first.property("loaded"))
            self.window.navigate("library")
            self.assertEqual(self.window.page_id, "library")
            gate.set()
            wait_for(lambda: first.property("loaded") and second.property("loaded"))
            request.assert_called_once()
            self.assertTrue(self.window.images.path_for(url).is_file())
        with patch("launcher.images.urllib.request.urlopen", return_value=Response(b"not an image")):
            failed = ProjectPreview(self.window.images, url + "?invalid", parent=self.window)
            wait_for(lambda: not self.window.images.jobs)
            self.assertFalse(failed.property("loaded"))

    def test_instance_sidebar_has_separate_content_types_and_worlds(self):
        instance = self.service.game.create("Managed", "1.21.1", "fabric")
        game = self.service.store.instance_dir(instance["id"]) / "game"
        for folder, filename in (("mods", "test.jar"), ("resourcepacks", "resources.zip"), ("shaderpacks", "lighting.zip"), ("saves/World One", "level.dat")):
            directory = game / folder
            directory.mkdir(parents=True, exist_ok=True)
            (directory / filename).write_bytes(b"fixture")
        self.window.refresh()
        self.window.open_instance(instance["id"])
        for category, kind in (("mods", "mod"), ("resourcepacks", "resourcepack"), ("shaders", "shader")):
            QTest.mouseClick(self.window.sidebar.findChild(Button, "instance-" + category), Qt.LeftButton)
            panel = self.window.instance_panel
            wait_for(lambda: len(panel.local_items) == 3)
            self.assertEqual(panel.content_kind, kind)
            expected = {"mod": "test.jar", "resourcepack": "resources.zip", "shader": "lighting.zip"}[kind]
            wait_for(lambda: any(item.full_text == expected for item in panel.findChildren(ElidedLabel)))
        QTest.mouseClick(self.window.sidebar.findChild(Button, "instance-saves"), Qt.LeftButton)
        self.assertTrue(any(w.full_text == "World One" for w in self.window.instance_panel.findChildren(ElidedLabel)))
        self.window.set_settings_category("account")
        self.assertIsNone(self.window.page.findChild(QLineEdit, "client-id"))

    def test_version_management_import_remove_and_settings_draft(self):
        instance = self.service.game.create("Managed", "1.21.1", "fabric")
        source = self.root / "imported.jar"
        source.write_bytes(b"local UI import fixture")
        self.window.refresh()
        self.window.open_instance(instance["id"])
        self.window.set_instance_category("mods")
        with patch("launcher.desktop.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.window.instance_panel.import_local()
        destination = self.service.store.instance_dir(instance["id"]) / "game/mods/imported.jar"
        wait_for(lambda: destination.exists() and all(t["status"] == "completed" for t in self.service.tasks.list()))
        self.window.refresh()
        panel = self.window.instance_panel
        wait_for(lambda: len(panel.local_items) == 1)
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        with patch("launcher.desktop.QMessageBox.question", return_value=QMessageBox.Yes):
            panel.toggle(panel.local_items[0], remove=True)
        wait_for(lambda: not destination.exists() and all(t["status"] == "completed" for t in self.service.tasks.list()))
        self.assertEqual(len(list((self.service.store.instance_dir(instance["id"]) / "trash").glob("*-imported.jar"))), 1)
        self.window.set_instance_category("settings")
        self.window.instance_panel.instance_fields["name"].setText("Draft Name")
        self.window.set_instance_category("mods")
        self.window.set_instance_category("settings")
        self.assertEqual(self.window.instance_panel.instance_fields["name"].text(), "Draft Name")
        self.window.data["tasks"] = [dict(t, status="running") for t in self.service.tasks.list()]
        with patch("launcher.desktop.QMessageBox.exec", side_effect=AssertionError("Completed tasks must not show an exit prompt")):
            self.window.close()

    def test_modpack_version_dialog_fits_and_installs_without_existing_target(self):
        instance = self.service.game.create("Long target " * 30, "1.21.1", "fabric")
        self.window.refresh()
        self.window.search_state.update(kind="modpack", instance=instance["id"])
        project = {"project_id": "pack", "title": "Example Pack", "description": "Description " * 30, "author": "Author"}
        versions = [{"id": "file", "name": "LongVersionFileName" * 25, "game_versions": ["1.21.1"], "date_published": "2026-01-01"}]
        with patch.object(self.service.content, "versions", return_value=versions), patch.object(self.service.content, "install_project", return_value=None) as install:
            dialog = self.window.open_dialog(ProjectDialog(self.window, project, dict(self.window.search_state)))
            dialog.resize(550, 600)
            wait_for(lambda: any(b.text() == "↓ 安装" for b in dialog.findChildren(Button)))
            self.assertFalse(dialog.target.isVisible())
            self.assertLessEqual(dialog.scroll.body.width(), dialog.scroll.viewport().width())
            button = next(b for b in dialog.findChildren(Button) if b.text() == "↓ 安装")
            QTest.mouseClick(button, Qt.LeftButton)
            wait_for(lambda: install.called)
            self.assertEqual(install.call_args.args[2], "")

    def test_fresh_portable_directory_created_only_by_library_button(self):
        portable = self.root / "new-portable"
        service = Application(portable / "data", minecraft_root=portable / ".Minecraft")
        window = LauncherWindow(portable / "data", service=service)
        window.timer.stop()
        self.assertEqual((window.width(), window.height()), (900, 600))
        window.show()
        try:
            self.assertFalse(service.store.minecraft_root.exists())
            window.navigate("settings")
            window.navigate("library")
            APP.processEvents()
            self.assertFalse(service.store.minecraft_root.exists())
            create = window.sidebar.findChild(Button, "create-minecraft")
            self.assertIsNotNone(create)
            QTest.mouseClick(create, Qt.LeftButton)
            wait_for(lambda: (service.store.minecraft_root / "versions").is_dir())
            wait_for(lambda: not window.workers)
            self.assertEqual(service.store.selected_minecraft(), portable / ".Minecraft")
            self.assertFalse(any(b.isVisible() for b in window.sidebar.findChildren(Button, "create-minecraft")))
        finally:
            window.pool.waitForDone(5000)
            window.close()
            window.deleteLater()
            APP.processEvents()

    def test_all_loader_choices_are_parallel_independent_retryable_and_install_exact_selection(self):
        from launcher.core import LauncherError
        def versions(version, loader):
            if loader == "quilt":
                raise LauncherError("fixture unavailable")
            if loader == "neoforge":
                return []
            return [{"version": "beta-2", "stable": False}, {"version": "stable-1", "stable": True}]
        with patch.object(self.service.game, "loader_versions", side_effect=versions) as load:
            class ShowGuard(QObject):
                def __init__(self):
                    super().__init__()
                    self.windows = []
                def eventFilter(self, watched, event):
                    if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
                        self.windows.append(type(watched).__name__)
                    return False
            observer = ShowGuard()
            APP.installEventFilter(observer)
            self.window.install_version = "1.20.1"
            try:
                self.window.navigate("install")
            finally:
                APP.removeEventFilter(observer)
            self.assertEqual(observer.windows, [], "Loader selectors must never flash as top-level windows")
            form = self.window.install_form
            wait_for(lambda: form.rows["quilt"]["retry"].isVisible())
            wait_for(lambda: form.rows["forge"]["versions"].count() == 2)
            self.assertEqual({call.args[1] for call in load.call_args_list}, {"forge", "neoforge", "fabric", "quilt"})
            self.assertTrue(form.submit.isEnabled())
            self.assertFalse(form.rows["neoforge"]["radio"].isEnabled())
            self.assertFalse(form.rows["quilt"]["radio"].isEnabled())
            self.assertEqual(form.rows["forge"]["versions"].currentData(), "stable-1")
            for entry in form.rows.values():
                self.assertTrue(entry["radio"].isVisible())
            # Retry one failed service, keeping the other three results.
            with patch.object(self.service.game, "loader_versions", return_value=[{"version":"quilt-fixed", "stable":True}]):
                form.rows["quilt"]["retry"].click()
                wait_for(lambda: form.rows["quilt"]["radio"].isEnabled())
            self.assertEqual(form.rows["forge"]["versions"].count(), 2)
            self.window.page.ensureWidgetVisible(form.rows["forge"]["radio"])
            form.rows["forge"]["radio"].click()
            self.assertEqual(form.selected_loader, "forge")
            self.window.resize(900, 600)
            QTest.qWait(400)
            self.assertLessEqual(self.window.page.body.width(), self.window.page.viewport().width())
            with patch.object(self.window, "request") as request:
                form.install()
                payload = request.call_args.args[1]
                self.assertEqual(payload["version"], "1.20.1")
                self.assertEqual(payload["loader"], "forge")
                self.assertEqual(payload["loader_version"], "stable-1")

    def test_launcher_size_applies_immediately_and_restores_without_changing_game_resolution(self):
        initial = self.service.store.config()
        self.window.set_settings_category("appearance")
        self.window.settings_fields["launcher_width"].setValue(980)
        self.window.settings_fields["launcher_height"].setValue(660)
        self.assertEqual((self.window.width(),self.window.height()),(980,660))
        current = self.service.store.config()
        self.assertEqual((current["width"],current["height"]),(initial["width"],initial["height"]))
        preset = self.window.page.findChild(QComboBox,"launcher-size-preset")
        preset.setCurrentIndex(preset.findData("900x600"))
        self.assertEqual((self.window.width(),self.window.height()),(900,600))
        self.window.settings_fields["launcher_width"].setValue(980)
        self.window.settings_fields["launcher_height"].setValue(660)
        restored = LauncherWindow(self.root,service=self.service)
        restored.timer.stop()
        try:
            self.assertEqual((restored.width(),restored.height()),(980,660))
        finally:
            restored.close(); restored.deleteLater(); APP.processEvents()

    def test_button_press_rebounds_without_layout_shift_and_supports_reduced_motion(self):
        button = Button("Motion", primary=True, parent=self.window.page.body)
        button.setGeometry(40, 40, 210, 45)
        button.show()
        geometry = button.geometry()
        clicks = []
        button.clicked.connect(lambda: clicks.append(True))
        QTest.mouseMove(button, button.rect().center())
        QTest.mousePress(button, Qt.LeftButton)
        QTest.qWait(110)
        self.assertGreater(button.get_press(), .95)
        self.assertEqual(button.geometry(), geometry)
        QTest.mouseRelease(button, Qt.LeftButton)
        self.assertEqual(button.press_animation.state(), QAbstractAnimation.Running)
        QTest.qWait(390)
        self.assertAlmostEqual(button.get_press(), 0, places=3)
        self.assertEqual(button.geometry(), geometry)
        self.assertEqual(len(clicks), 1)
        QApplication.instance().setProperty("animations", False)
        try:
            QTest.mousePress(button, Qt.LeftButton)
            self.assertEqual(button.get_press(), 1)
            self.assertEqual(button.press_animation.state(), QAbstractAnimation.Stopped)
            QTest.mouseRelease(button, Qt.LeftButton)
            self.assertEqual(button.get_press(), 0)
            button.setEnabled(False)
            self.assertEqual(button.get_hover(), 0)
        finally:
            QApplication.instance().setProperty("animations", True)

    def test_flowing_progress_keeps_exact_value_resets_retries_and_stops_when_hidden(self):
        from launcher.progress_ui import GlassProgress
        bar = GlassProgress(self.window.page.body)
        bar.setGeometry(30, 40, 300, 18)
        bar.setRange(0, 1000)
        bar.setValue(200)
        bar.show()
        QTest.qWait(80)
        self.assertEqual(bar._flow.state(), QAbstractAnimation.Running)
        bar.setValue(550)
        self.assertEqual(bar.value(), 550)
        QTest.qWait(70)
        self.assertLessEqual(bar._fraction, .55)
        QTest.qWait(240)
        self.assertAlmostEqual(bar._fraction, .55, places=3)
        bar.setValue(10)
        self.assertAlmostEqual(bar._fraction, .01)
        bar.setRange(0, 0)
        self.assertEqual(bar._flow.state(), QAbstractAnimation.Running)
        bar.hide()
        self.assertEqual(bar._flow.state(), QAbstractAnimation.Stopped)
        bar.show()
        self.assertEqual(bar._flow.state(), QAbstractAnimation.Running)
        bar.setRange(0, 1000)
        bar.setValue(1000)
        bar.setActivity(False)
        self.assertEqual(bar._flow.state(), QAbstractAnimation.Stopped)
        self.assertEqual(bar._fraction, 1)
        bar.setActivity(True)
        bar.setRange(0, 0)
        QApplication.instance().setProperty("animations", False)
        try:
            bar.setActivity(True)
            self.assertEqual(bar._flow.state(), QAbstractAnimation.Stopped)
            bar.setRange(0, 1000)
            bar.setValue(370)
            self.assertEqual(bar.value(), 370)
            self.assertAlmostEqual(bar._fraction, .37)
        finally:
            QApplication.instance().setProperty("animations", True)

    def test_dropdown_control_and_popup_sizes_remain_stable_after_first_open(self):
        self.window.set_settings_category("download")
        QTest.qWait(430)
        control = self.window.settings_fields["source"]
        for i in range(20):
            control.addItem("Very long option " * 14 + str(i),i)
        APP.processEvents()
        before = (control.size(), control.sizeHint(), self.window.size())
        sizes = []
        for index in range(4):
            control.showPopup()
            QTest.qWait(170)
            self.assertEqual((control.size(),control.sizeHint(),self.window.size()),before)
            sizes.append(control.popup.size())
            self.assertEqual(control.popup.size(),control.popup.minimumSize())
            control.hidePopup()
            QTest.qWait(40)
            self.assertEqual((control.size(),control.sizeHint(),self.window.size()),before)
        self.assertTrue(all(size == sizes[0] for size in sizes))

    def test_top_double_click_is_ignored_and_focus_primitives_draw_no_rings(self):
        from PySide6.QtGui import QPainter
        from PySide6.QtWidgets import QStyle, QStyleOptionFocusRect
        before = self.window.geometry()
        QTest.mouseDClick(self.window.titlebar,Qt.LeftButton,pos=QPoint(400,5))
        APP.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.geometry(),before)
        image = QImage(80,40,QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        empty = image.copy()
        p = QPainter(image)
        option = QStyleOptionFocusRect()
        option.rect = image.rect()
        APP.style().drawPrimitive(QStyle.PE_FrameFocusRect,option,p,self.window)
        p.end()
        self.assertEqual(image,empty)
        button = self.window.top_buttons["download"]
        button.setFocus(Qt.TabFocusReason)
        QTest.keyClick(button,Qt.Key_Space)
        self.assertEqual(self.window.page_id,"install")

    def test_glass_sliders_drag_track_keyboard_rtl_and_nontracking(self):
        from launcher.slider_ui import GlassSlider
        self.window.set_settings_category("appearance")
        slider = self.window.background_opacity
        self.assertIsInstance(slider,GlassSlider)
        self.window.page.ensureWidgetVisible(slider)
        QTest.qWait(430)
        span = slider.width()-52
        QTest.mouseClick(slider,Qt.LeftButton,pos=QPoint(26+round(span*.25),slider.height()//2))
        self.assertAlmostEqual(slider.value(),25,delta=1)
        QTest.keyClick(slider,Qt.Key_Home)
        self.assertEqual(slider.value(),0)
        QTest.keyClick(slider,Qt.Key_End)
        self.assertEqual(slider.value(),100)
        slider.setLayoutDirection(Qt.RightToLeft)
        QTest.mouseClick(slider,Qt.LeftButton,pos=QPoint(26,slider.height()//2))
        self.assertEqual(slider.value(),100)
        slider.setLayoutDirection(Qt.LeftToRight)
        slider.setTracking(False)
        slider.setValue(20)
        point = QPoint(26+round(span*.8),slider.height()//2)
        QTest.mousePress(slider,Qt.LeftButton,pos=point)
        self.assertEqual(slider.value(),20)
        self.assertAlmostEqual(slider.sliderPosition(),80,delta=1)
        QTest.mouseRelease(slider,Qt.LeftButton,pos=point)
        self.assertAlmostEqual(slider.value(),80,delta=1)
        value = slider.value()
        self.window.page.body.setMinimumHeight(1600)
        QTest.qWait(100)
        bar = self.window.page.verticalScrollBar()
        bar.setValue(0)
        wheel(slider)
        self.assertGreater(bar.value(),0)
        self.assertEqual(slider.value(),value)
        bar.setValue(bar.maximum())
        wheel(slider)
        wheel(slider,angle=0,pixels=-37)
        self.assertEqual(slider.value(),value)
        bar.setValue(0)
        wheel(slider,angle=120)
        self.assertEqual(slider.value(),value)
        standalone = GlassSlider()
        standalone.setValue(40)
        for angle,pixels in ((-120,0),(120,0),(0,-30)):
            wheel(standalone,angle=angle,pixels=pixels)
            self.assertEqual(standalone.value(),40)
        event = QWheelEvent(QPointF(15,15),QPointF(15,15),QPoint(35,0),QPoint(120,0),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
        APP.sendEvent(standalone,event)
        self.assertEqual(standalone.value(),40)
        standalone.deleteLater()

    def test_expanded_glass_settings_persist_and_render_across_light_and_dark(self):
        from launcher.core import LauncherError
        from launcher.widgets import card
        self.window.set_settings_category("appearance")
        for opacity,refraction in ((0,0),(120,200)):
            self.window.glass_opacity.setValue(opacity)
            self.window.glass_refraction.setValue(refraction)
            self.assertTrue(self.window.flush_settings())
            self.assertEqual(self.service.store.config()["glass_opacity"],opacity)
            self.assertEqual(self.service.store.config()["glass_refraction"],refraction)
            self.assertEqual(APP.property("glassOpacity"),opacity)
            self.assertEqual(APP.property("glassRefraction"),refraction)
        restarted = Application(self.service.store.root)
        self.assertEqual(restarted.store.config()["glass_opacity"],120)
        self.assertEqual(restarted.store.config()["glass_refraction"],200)
        for changes in ({"glass_opacity":-1},{"glass_opacity":121},{"glass_refraction":-1},{"glass_refraction":201}):
            with self.assertRaises(LauncherError):
                self.service.config_update(changes)
        frame,_ = card()
        frame.resize(180,90)
        for scheme in ("starmoon","night"):
            captures,alphas = [],[]
            for opacity in (0,35,95,120):
                self.service.config_update({"color_scheme":scheme,"glass_opacity":opacity})
                self.window.data = self.service.state()
                self.window.apply_appearance()
                alphas.append(self.window.glass_surface.backdrop().toImage().pixelColor(10,10).alpha())
                capture = frame.grab().toImage()
                self.assertTrue(capture.pixelColor(capture.width()//2,capture.height()//2).isValid())
                captures.append(capture)
            self.assertTrue(all(a<b for a,b in zip(alphas,alphas[1:])),alphas)
            self.assertTrue(all(a!=b for a,b in zip(captures,captures[1:])))
        frame.deleteLater()

    def test_all_setting_sliders_apply_and_save_once_only_after_real_mouse_release(self):
        for category,key,attribute in (("game","memory","memory_slider"),("appearance","background_opacity","background_opacity"),
                                       ("appearance","glass_opacity","glass_opacity"),("appearance","glass_refraction","glass_refraction")):
            self.window.set_settings_category(category)
            if key == "memory": self.window.memory_auto_check.setChecked(False)
            slider = getattr(self.window,attribute)
            self.window.page.ensureWidgetVisible(slider)
            QTest.qWait(400)
            before = self.service.store.config()[key]
            point = QPoint(26+round((slider.width()-52)*.8),slider.height()//2)
            with patch.object(self.service,"config_update",wraps=self.service.config_update) as update:
                QTest.mousePress(slider,Qt.LeftButton,pos=point)
                self.assertTrue(slider.isSliderDown())
                self.assertNotEqual(slider.value(),before)
                QTest.qWait(450)
                self.window.settings_fields["jvm_args"].setPlainText("-Dother=changed")
                QTest.qWait(400)
                self.window.flush_settings()
                self.assertEqual(self.service.store.config()[key],before)
                slider.sliderReleased.emit()
                self.assertEqual(self.service.store.config()[key],before)
                self.assertIn(key,self.window.dragging_settings)
                if key in ("glass_opacity","glass_refraction"):
                    self.assertEqual(APP.property("glassOpacity" if key=="glass_opacity" else "glassRefraction"),before)
                QTest.mouseRelease(slider,Qt.LeftButton,pos=point)
                self.assertEqual(self.service.store.config()[key],slider.value())
                if key in ("glass_opacity","glass_refraction"):
                    self.assertEqual(APP.property("glassOpacity" if key=="glass_opacity" else "glassRefraction"),slider.value())
                self.assertEqual(sum(key in call.args[0] for call in update.call_args_list),1)

    def test_slider_generic_release_does_not_commit_and_hidden_drag_is_canceled(self):
        self.window.set_settings_category("appearance")
        slider = self.window.glass_opacity
        self.window.page.ensureWidgetVisible(slider)
        QTest.qWait(400)
        original = self.service.store.config()["glass_opacity"]
        point = QPoint(26+round((slider.width()-52)*.8),slider.height()//2)
        QTest.mousePress(slider,Qt.LeftButton,pos=point)
        slider.setSliderDown(False)
        QTest.qWait(250)
        self.assertEqual(self.service.store.config()["glass_opacity"],original)
        self.assertIn("glass_opacity",self.window.dragging_settings)
        QTest.mouseRelease(slider,Qt.LeftButton,pos=point)
        self.assertEqual(self.service.store.config()["glass_opacity"],slider.value())
        original = slider.value()
        QTest.mousePress(slider,Qt.LeftButton,pos=QPoint(26,slider.height()//2))
        self.assertNotEqual(slider.value(),original)
        slider.hide()
        self.assertEqual(slider._press,0.0)
        self.assertEqual(slider.value(),original)
        self.assertEqual(self.service.store.config()["glass_opacity"],original)
        self.assertNotIn("glass_opacity",self.window.dragging_settings)
        self.assertNotIn("glass_opacity",self.window.setting_changes)

    def test_default_wallpaper_clear_restart_restore_and_dialog_exclusion(self):
        from launcher.appearance import bundled_background
        self.assertTrue(bundled_background().is_file())
        self.assertEqual(self.service.store.config()["background_mode"],"default")
        self.assertFalse(self.window.glass_surface.image.isNull())
        self.window.set_settings_category("appearance")
        self.window.page.findChild(Button,"clear-wallpaper").click()
        self.assertEqual(self.service.store.config()["background_mode"],"none")
        self.assertTrue(self.window.glass_surface.image.isNull())
        restarted = Application(self.service.store.root)
        self.assertEqual(restarted.store.config()["background_mode"],"none")
        self.window.page.findChild(Button,"reset-wallpaper").click()
        self.assertEqual(self.service.store.config()["background_mode"],"default")
        self.assertFalse(self.window.glass_surface.image.isNull())
        dialog = self.window.open_dialog(NativeDialog(self.window,"Wallpaper exclusion"))
        self.assertTrue(dialog.glass_surface.image.isNull())
        dialog.reject()

    def test_wallpaper_preset_thumbnails_switch_all_main_pages_and_remember_selection(self):
        from launcher.appearance import BACKGROUND_PRESETS, preset_background, wallpaper_path
        from launcher.wallpaper_ui import WallpaperTile
        self.window.set_settings_category("appearance")
        self.window.resize(900,600)
        QTest.qWait(100)
        self.window.page.findChild(Button,"choose-preset-wallpaper").click()
        dialog = self.window.dialogs[-1]
        QTest.qWait(400)
        tiles = dialog.findChildren(WallpaperTile)
        self.assertEqual(len(tiles),6)
        self.assertTrue(dialog.glass_surface.image.isNull())
        self.assertTrue(dialog.findChild(WallpaperTile,"wallpaper-preset-default").isChecked())
        for tile in tiles:
            self.assertFalse(tile.preview.isNull())
            self.assertLessEqual(tile.mapTo(dialog,tile.rect().bottomRight()).x(),dialog.width())
        dialog.reject()
        for preset,_,_ in BACKGROUND_PRESETS:
            dialog = self.window.choose_preset_background()
            tile = dialog.findChild(WallpaperTile,"wallpaper-preset-"+preset)
            QTest.mouseClick(tile,Qt.LeftButton)
            self.assertFalse(dialog.isVisible())
            config = self.service.store.config()
            self.assertEqual(config["background_preset"],preset)
            self.assertEqual(config["background_mode"],"default" if preset == "default" else "preset")
            self.assertEqual(self.window.glass_surface.background_key[0],str(preset_background(preset)))
        restarted = Application(self.service.store.root)
        self.assertEqual(restarted.store.config()["background_preset"],"milky-way")
        self.assertEqual(restarted.store.config()["background_path"],"")
        for page in ("home","library","downloads","about","settings"):
            self.window.navigate(page,animated=False)
            self.assertEqual(self.window.glass_surface.background_key[0],wallpaper_path(restarted.store.config()))
        self.window.set_settings_category("appearance")
        dialog = self.window.choose_preset_background()
        self.assertTrue(dialog.findChild(WallpaperTile,"wallpaper-preset-milky-way").isChecked())
        dialog.reject()
        self.window.clear_background()
        dialog = self.window.choose_preset_background()
        self.assertFalse(any(tile.isChecked() for tile in dialog.findChildren(WallpaperTile)))
        dialog.reject()
        self.window.reset_background()
        self.assertEqual(self.service.store.config()["background_preset"],"default")
        self.assertEqual(self.service.store.config()["background_mode"],"default")

    def test_legacy_opaque_skin_overlay_does_not_hide_the_face(self):
        from launcher.skin_ui import skin_avatar
        image = QImage(64,32,QImage.Format_RGB32)
        image.fill(QColor("black"))
        for x in range(8,16):
            for y in range(8,16): image.setPixelColor(x,y,QColor("red"))
        path = self.root/"legacy-skin.png"
        image.save(str(path))
        self.assertEqual(skin_avatar(path).toImage().pixelColor(20,20),QColor("red"))

    def test_player_skin_fetch_preview_export_and_offline_apply(self):
        account = self.service.accounts.offline("UiSkinTester")
        cache = self.service.player_skins.cache/"fixture"
        cache.mkdir(parents=True)
        path = cache/"preview.png"
        image = QImage(64,64,QImage.Format_ARGB32)
        image.fill(QColor("#408aff")); image.save(str(path))
        result = {"name":"FixturePlayer","uuid":"a"*32,"model":"slim","path":str(path)}
        self.window.navigate("accounts",animated=False)
        self.assertIsNotNone(self.window.page.findChild(Button,"download-player-skin"))
        QTest.qWait(150)
        self.assertLessEqual(self.window.page.body.width(),self.window.page.viewport().width())
        self.window.open_player_skin(account["id"])
        dialog = self.window.dialogs[-1]
        dialog.player.setText("FixturePlayer")
        self.assertFalse(dialog.export_button.isEnabled())
        with patch.object(self.service.player_skins,"download",return_value=result) as download:
            dialog.fetch_button.click()
            wait_for(lambda:dialog.export_button.isEnabled())
            download.assert_called_once_with("FixturePlayer")
            self.assertFalse(dialog.preview.image.isNull())
            exported = self.root/"exported-skin.png"
            with patch("launcher.desktop.QFileDialog.getSaveFileName",return_value=(str(exported),"")):
                dialog.export_button.click()
            wait_for(exported.is_file)
            dialog.apply_button.click()
            wait_for(lambda:self.service.skins.account(account["id"]).get("skin_model")=="slim")
        self.assertTrue(self.service.skins.path(self.service.skins.account(account["id"])).is_file())

    def test_two_same_instance_process_rows_launch_again_and_stop_only_selected_run(self):
        instance = self.service.game.create("Multi UI","1.20.1")
        self.service.accounts.offline("UiTester")
        processes = [{"id":"run-ui-a","instance_id":instance["id"],"pid":1001,"started":1,"running":True,"exit_code":None,"finalized":False},
                     {"id":"run-ui-b","instance_id":instance["id"],"pid":1002,"started":2,"running":True,"exit_code":None,"finalized":False}]
        with patch.object(self.service.game,"process_status",return_value=processes):
            self.window.refresh()
            self.window.navigate("home",animated=False)
            self.assertEqual(self.window.launch_button.text(),"再次启动")
            with patch.object(self.window,"mutate") as mutate:
                self.window.launch(instance["id"])
                self.assertEqual(mutate.call_args.args[0],"instances/launch")
            with patch.object(self.window,"request") as request:
                self.window.launch_current()
                self.assertEqual(request.call_args.args[0],"accounts/offline")
            self.window.navigate("downloads",animated=False)
            self.assertEqual(set(self.window.process_rows),{"run-ui-a","run-ui-b"})
            with patch("launcher.desktop.QMessageBox.question",return_value=QMessageBox.Yes),patch.object(self.window,"mutate") as mutate:
                self.window.process_rows["run-ui-b"][1].click()
                self.assertEqual(mutate.call_args.args,("processes/stop",{"id":"run-ui-b"}))
            processes[0].update(running=False,exit_code=0,finalized=True)
            self.window.refresh()
            self.assertTrue(self.window.process_rows["run-ui-a"][2].isVisible())
            self.assertFalse(self.window.process_rows["run-ui-b"][2].isVisible())

    def test_color_schemes_apply_immediately_restart_and_keep_contrast(self):
        from launcher.themes import SCHEMES,palette
        self.window.set_settings_category("appearance")
        picker = self.window.settings_fields["color_scheme"]
        self.assertEqual(picker.count(),7)
        for key in SCHEMES:
            picker.setCurrentIndex(picker.findData(key))
            self.assertEqual(APP.property("colorScheme"),key)
            self.assertEqual(self.service.store.config()["color_scheme"],key)
            colors = palette()
            self.assertNotEqual(colors["text"],colors["bg"])
            if colors["dark"]: self.assertGreater(colors["text"].lightnessF(),.75)
            self.window.navigate("home",animated=False)
            self.window.set_settings_category("appearance")
            picker = self.window.settings_fields["color_scheme"]
        self.assertEqual(Application(self.service.store.root).store.config()["color_scheme"],"night")

    def test_ai_settings_apply_masked_key_encrypt_clear_and_debounce_text(self):
        self.window.set_settings_category("ai")
        fields = self.window.settings_fields
        fields["ai_base_url"].setText("http://127.0.0.1:12345/v1")
        fields["ai_model"].setText("fixture-model")
        fields["ai_api_key"].setText("unit-ui-private-key")
        self.assertEqual(fields["ai_api_key"].echoMode(),QLineEdit.Password)
        self.window.ai_preflight_check.setChecked(True)
        self.assertTrue(self.service.store.config()["ai_auto_preflight"])
        wait_for(lambda:self.service.store.public_config()["ai_key_configured"])
        self.assertEqual(self.service.diagnostics.key(),"unit-ui-private-key")
        self.assertNotIn("unit-ui-private-key",(self.service.store.root/"ai-credentials.json").read_text())
        self.assertNotIn("ai_api_key",self.service.store.config())
        self.window.ai_clear_key.setChecked(True)
        self.assertEqual(self.service.diagnostics.key(),"")
        self.assertEqual(fields["ai_api_key"].text(),"")
        self.assertFalse(self.window.ai_clear_key.isChecked())
        fields["ai_model"].setText("immediate-model")
        self.window.navigate("home",animated=False)
        self.assertEqual(self.service.store.config()["ai_model"],"immediate-model")

    def test_manual_preflight_result_display_once_and_explicit_continue(self):
        instance = self.service.game.create("Checked","1.20.1","fabric")
        self.service.config_update({"ai_base_url":"http://127.0.0.1:12345/v1","ai_model":"fixture"})
        self.window.refresh()
        report = {"instance_id":instance["id"],"kind":"preflight","status":"risk","source":"ai","cause":"Missing dependency","suggestion":"Install required library"}
        with patch.object(self.service.diagnostics,"analyze",return_value=report):
            self.window.navigate("home",animated=False)
            button = self.window.page.findChild(Button,"preflight-check")
            QTest.mouseClick(button,Qt.LeftButton)
            wait_for(lambda: any(d.objectName()=="diagnostic-result" for d in self.window.dialogs))
            dialog = next(d for d in self.window.dialogs if d.objectName()=="diagnostic-result")
            self.assertEqual(dialog.findChild(QWidget,"diagnostic-cause").text(),"Missing dependency")
            self.window.refresh()
            self.assertEqual(sum(d.objectName()=="diagnostic-result" for d in self.window.dialogs),1)
            dialog.reject()
        self.window.show_diagnostic(report,"original-account")
        dialog = self.window.dialogs[-1]
        with patch.object(self.window,"mutate") as mutate:
            dialog.findChild(Button,"diagnostic-launch-anyway").click()
            payload = mutate.call_args.args[1]
            self.assertTrue(payload["skip_preflight"])
            self.assertEqual(payload["account"],"original-account")
            self.assertEqual(payload["id"],instance["id"])

    def test_finished_crash_analysis_restores_background_window_and_shows_result(self):
        from launcher.core import Task
        instance = self.service.game.create("Crash","1.20.1")
        task = Task("Analysis",None)
        task.status = "completed"
        task.result = {"diagnostic_report":{"instance_id":instance["id"],"kind":"crash","source":"ai","status":"risk","cause":"Fixture crash","suggestion":"Fixture fix"}}
        self.service.tasks.tasks[task.id] = task
        self.window.background_mode = True
        self.window.hide()
        with patch.object(APP,"quit") as quit:
            self.window.refresh()
            quit.assert_not_called()
        self.assertFalse(self.window.background_mode)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(any(d.objectName()=="diagnostic-result" for d in self.window.dialogs))


if __name__ == "__main__":
    unittest.main()

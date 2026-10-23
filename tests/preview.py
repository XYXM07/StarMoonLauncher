"""Verify compact wallpaper picker layout and render each bundled preset."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/".build-env")]
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from launcher.appearance import BACKGROUND_PRESETS
from launcher.desktop import LauncherWindow
from launcher.service import Application
from launcher.wallpaper_ui import WallpaperTile
from launcher.widgets import Button, WheelRouter

app = QApplication([])
app.setStyle("Fusion"); app.setFont(QFont("Microsoft YaHei UI",9))
app.wheel_router = WheelRouter(app); app.installEventFilter(app.wheel_router)
output = ROOT/"test-results/preview"; output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    service = Application(Path(temp)/"data",minecraft_root=Path(temp)/".Minecraft")
    with patch.object(service.game,"manifest",return_value={"versions":[]}),patch.object(service.content,"search",return_value={"hits":[],"total_hits":0}):
        window = LauncherWindow(Path(temp)/"data",service=service)
        window.timer.stop(); window.show(); QTest.qWait(400)
        window.set_settings_category("appearance"); QTest.qWait(400)
        picker_button = window.page.findChild(Button,"choose-preset-wallpaper")
        window.page.ensureWidgetVisible(picker_button); QTest.qWait(150)
        window.grab().save(str(output/"wallpaper-settings.png"))
        coordinates = {"size":[window.width(),window.height()]}
        def point(widget,owner):
            p = widget.mapTo(owner,widget.rect().center())
            return [p.x(),p.y()]
        coordinates["settings"] = point(window.top_buttons["settings"],window)
        coordinates["appearance"] = point(next(b for b in window.sidebar.findChildren(Button) if b.text()=="外观"),window)
        window.page.verticalScrollBar().setValue(window.page.verticalScrollBar().maximum()); QTest.qWait(100)
        coordinates["wheel"] = point(window.page.viewport(),window)
        coordinates["picker"] = point(picker_button,window)
        assert window.page.viewport().rect().contains(picker_button.mapTo(window.page.viewport(),picker_button.rect().center()))
        dialog = window.choose_preset_background(); QTest.qWait(500)
        coordinates["dialog_size"] = [dialog.width(),dialog.height()]
        coordinates["tiles"] = {}
        for preset,_,_ in BACKGROUND_PRESETS:
            tile = dialog.findChild(WallpaperTile,"wallpaper-preset-"+preset)
            coordinates["tiles"][preset] = point(tile,dialog)
            bottom = tile.mapTo(dialog.scroll.viewport(),tile.rect().bottomRight())
            assert dialog.scroll.viewport().rect().contains(bottom),(preset,bottom)
        dialog.grab().save(str(output/"preset-picker.png"))
        dialog.reject()
        for preset,_,_ in BACKGROUND_PRESETS:
            window.set_wallpaper("default" if preset == "default" else "preset",preset=preset)
            window.navigate("home",animated=False); QTest.qWait(100)
            window.grab().save(str(output/(preset+".png")))
        (output/"coordinates.json").write_text(json.dumps(coordinates),encoding="utf-8")
        window.close(); window.pool.waitForDone(5000); window.images.pool.waitForDone(10000)
        window.deleteLater(); app.processEvents()
print("COMPACT_SIX_PRESET_THUMBNAILS_AND_WALLPAPER_PREVIEWS_OK",flush=True)

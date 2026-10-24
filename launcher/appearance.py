"""Wallpaper selection independent of a developer machine's absolute paths."""
import sys
from pathlib import Path
from .i18n import tr


BACKGROUND_PRESETS = (
    ("default", "星月暮色", "default-background.jpg"),
    ("sky-ladder", "云端天梯", "backgrounds/1111117.png"),
    ("forest-reflection", "林间倒影", "backgrounds/222222.png"),
    ("summer-sky", "盛夏晴空", "backgrounds/33333.png"),
    ("starlit-pier", "星夜栈桥", "backgrounds/61657.jpg"),
    ("milky-way", "银河之境", "backgrounds/微信图片_20261022215819_3903_7.jpg"),
)
PRESET_IDS = {item[0] for item in BACKGROUND_PRESETS}


def preset_background(preset="default"):
    filename = next((item[2] for item in BACKGROUND_PRESETS if item[0] == preset), BACKGROUND_PRESETS[0][2])
    return Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parents[1]))/"assets"/filename


def bundled_background():
    return preset_background()


def wallpaper_mode(config):
    return config.get("background_mode") or ("custom" if config.get("background_path") else "default")


def wallpaper_path(config):
    mode = wallpaper_mode(config)
    if mode == "none": return ""
    if mode == "custom" and config.get("background_path"): return config["background_path"]
    if mode == "preset": return str(preset_background(config.get("background_preset","default")))
    return str(bundled_background())


def wallpaper_label(config):
    if wallpaper_mode(config) == "preset":
        name = next((item[1] for item in BACKGROUND_PRESETS if item[0] == config.get("background_preset")), BACKGROUND_PRESETS[0][1])
        return tr("预设壁纸 · ")+tr(name)
    return tr({"default":"默认壁纸 · 背景图.jpg","none":"无壁纸","custom":"自定义壁纸"}.get(wallpaper_mode(config),"默认壁纸"))

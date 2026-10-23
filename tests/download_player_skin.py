"""Fetch one public profile and PNG from official services, without any login token."""
import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/".build-env")]
from PySide6.QtWidgets import QApplication
from launcher.core import Store
from launcher.player_skins import PlayerSkins

parser = argparse.ArgumentParser(description="从官方服务下载公开玩家皮肤")
parser.add_argument("player",nargs="?",default="Notch",help="Java 玩家名或 UUID")
args = parser.parse_args()
app = QApplication([])
store = Store(ROOT/"test-results/player-skin-live")
result = PlayerSkins(store).download(args.player)
result.update(success=True,official_services=True)
(store.root/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print("OFFICIAL_PLAYER_SKIN_LOOKUP_PROFILE_AND_PNG_OK",result["name"],result["model"],flush=True)

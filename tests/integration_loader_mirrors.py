"""Optional live loader metadata check; no game, Java or account access."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher.core import Task, read_json
from launcher.service import Application


def main():
    output = ROOT / "test-results/new-sources"
    output.mkdir(parents=True, exist_ok=True)
    result = {}
    with tempfile.TemporaryDirectory(prefix="mirror-check-", dir=str(output)) as directory:
        app = Application(Path(directory) / "data")
        for loader, minecraft in (("fabric", "1.20.1"), ("neoforge", "1.21.1")):
            versions = app.game.loader_versions(minecraft, loader)
            assert versions
            result[loader] = {"versions": len(versions), "first_version": versions[0]["version"]}
        profile_id = app.game.install_loader({"loader":"fabric", "game_version":"1.20.1", "loader_version":"0.16.9"}, Task("Inspect mirrored Fabric profile", None))
        profile = read_json(app.game.profile_path(profile_id))
        assert profile["inheritsFrom"] == "1.20.1" and profile.get("libraries")
        result["fabric"]["profile_id"] = profile_id
    (output / "loader-mirrors-integration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

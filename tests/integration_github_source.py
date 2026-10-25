"""Opt-in public GitHub metadata and one Release JAR; never installs a game."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/".build-env")]
from launcher.core import Task
from launcher.github_source import API, inspect_mod
from launcher.service import Application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", nargs="?", default="gnembon/fabric-carpet")
    parser.add_argument("--download-one", action="store_true")
    args = parser.parse_args()
    output = ROOT/"test-results"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="github-live-", dir=str(output)) as directory:
        app = Application(Path(directory)/"data")
        source = app.content.github
        hit = source.search(args.repository)["hits"][0]
        repo = hit["project_id"]
        versions = source.versions(repo)
        assert versions, "No Release JARs in this repository"
        record = {"repository": repo, "release_jar_assets": len(versions), "first": versions[0]["name"], "public_no_token": True}
        captured = {"repository": app.content.providers.get(API+repo),
                    "releases": app.content.providers.get(API+repo+"/releases?per_page=100")}
        (output/"github-source-live-metadata.json").write_text(json.dumps(captured, ensure_ascii=False), encoding="utf-8")
        if args.download_one:
            chosen = next((v for v in versions if v["version_type"] == "release"), versions[0])
            asset, path, digest = source.download(repo, chosen["id"], Task("GitHub acceptance", None))
            metadata = inspect_mod(path)
            record.update(downloaded_asset=asset["filename"], bytes=path.stat().st_size, sha256=digest,
                          publisher_digest_available=bool(asset["sha256"]), metadata_ids=[m["id"] for m in metadata])
        assert not app.store.minecraft_root.exists(), "Acceptance must not create a game folder"
    (output/"github-source-network.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=True, indent=2))
    print("PUBLIC_GITHUB_RELEASE_METADATA_AND_OPTIONAL_JAR_OK", flush=True)


if __name__ == "__main__":
    main()

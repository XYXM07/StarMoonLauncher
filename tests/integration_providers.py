"""Opt-in public metadata and tiny-file acceptance; never installs a real game."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher.core import Network, Task
from launcher.providers import ProviderHub


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-files", action="store_true", help="Also verify one file under 256 KiB from each pack source")
    args = parser.parse_args()
    hub = ProviderHub()
    output = ROOT/"test-results"
    output.mkdir(exist_ok=True)
    records = []
    for provider, query, project in (("atlauncher", "All The Forge", "AllTheForge10"), ("ftb", "Evolution", "125")):
        results = hub.search(provider, query, "modpack")
        assert any(hit["project_id"] == project for hit in results["hits"]), provider
        versions = hub.versions(provider, project)
        assert versions, provider
        plan = hub.manifest(provider, project, versions[0]["id"])
        assert plan["files"] and plan["game_version"] and plan["loader"]
        record = {"provider": provider, "search_hits": results["total_hits"], "versions": len(versions),
                  "selected_version": versions[0]["id"], "loader": plan["loader"], "minecraft": plan["game_version"],
                  "client_files": len(plan["files"]), "config_archives": len(plan["archives"])}
        if args.download_files:
            choices = [item for item in plan["files"] if item.get("size") and item["size"] <= 256 * 1024]
            assert choices, "No small acceptance file: " + provider
            item = min(choices, key=lambda value: value["size"])
            network = Network(lambda: {"source": "official", "concurrency": 2, "download_parallel": True, "download_segments": 2})
            with tempfile.TemporaryDirectory(prefix="provider-file-", dir=str(output)) as directory:
                target = Path(directory)/"accepted-file"
                checks = {key: item[key] for key in ("sha1", "sha256", "sha512", "md5") if item.get(key)}
                network.download(item["urls"][0], target, task=Task("Acceptance", None), size=item["size"], **checks)
                record.update(verified_file=item["path"], verified_bytes=target.stat().st_size, checksums=list(checks))
        records.append(record)
        print(json.dumps(record, ensure_ascii=True), flush=True)
    encyclopedia = hub.search("mcmod", "JEI", "mod")
    assert encyclopedia["external_search_url"].startswith("https://search.mcmod.cn/")
    records.append({"provider": "mcmod", "status": "browser_fallback" if encyclopedia.get("unavailable") else "search_available",
                    "search_hits": len(encyclopedia["hits"]), "official_search_fallback": True})
    (output/"content-sources-network.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PUBLIC_PROVIDER_METADATA_AND_OPTIONAL_SMALL_FILES_OK", flush=True)


if __name__ == "__main__":
    main()

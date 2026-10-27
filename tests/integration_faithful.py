"""Optional official Faithful catalog and one real resource-pack install."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from launcher.core import Task, read_json
from launcher.faithful import PACKS
from launcher.service import Application


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--download-one",action="store_true")
    parser.add_argument("--resolve-modrinth",action="store_true")
    args=parser.parse_args()
    output=ROOT/"test-results/new-sources"
    output.mkdir(parents=True,exist_ok=True)
    results={}
    with tempfile.TemporaryDirectory(prefix="faithful-check-",dir=str(output)) as directory:
        app=Application(Path(directory)/"data")
        for project in PACKS:
            versions=app.content.faithful.versions(project)
            assert versions,project
            results[project]={"versions":len(versions),"latest":versions[0]["name"]}
            print(project,results[project],flush=True)
        if args.resolve_modrinth:
            for project,minecraft in (("faithful_64x_java","26.3"),("classic_64x_java","1.21.5")):
                chosen=app.content.faithful.versions(project,minecraft)[0]
                url,checks=app.content.faithful.resolve_download(chosen,minecraft)
                assert url.startswith("https://cdn.modrinth.com/") and checks.get("sha512")
                results[project]["modrinth_resolution"]={"size":checks.get("size"),"sha512":checks["sha512"]}
                print("MODRINTH_FILE_RESOLVED",project,checks.get("size"),flush=True)
        if args.download_one:
            app.store.create_minecraft()
            instance=app.game.create("Faithful verification","1.20.1","vanilla","")
            choices=app.content.versions("faithful_32x_java",instance["id"],"resourcepack","faithful")
            assert choices
            app.content.install_project("faithful_32x_java",choices[0]["id"],instance["id"],"resourcepack","faithful",Task("Faithful verification",None))
            record=read_json(app.store.instance_dir(instance["id"])/"content.json")[0]
            path=app.store.game_dir(instance["id"])/"resourcepacks"/record["filename"]
            results["download"]={"bytes":path.stat().st_size,"sha256":record["sha256"],"name":record["name"]}
            print("REAL_FAITHFUL_RESOURCEPACK_INSTALL_OK",json.dumps(results["download"]),flush=True)
    filename="faithful-integration.json" if args.download_one else "faithful-metadata-integration.json"
    (output/filename).write_text(json.dumps(results,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()

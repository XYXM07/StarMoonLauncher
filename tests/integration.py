"""Opt-in real network/game smoke test; stores all downloads under test-results/."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher.core import Task
from launcher.service import Application


def main():
    app = Application(Path("test-results/integration"))
    app.store.create_minecraft()
    app.store.save({"concurrency": 16, "source": "official"})
    instances = app.store.instances()
    instance = next((i for i in instances if i["game_version"] == "1.21.1" and i["loader"] == "fabric"), None)
    if not instance:
        instance = app.game.create("Fabric integration", "1.21.1", "fabric")
    account = app.accounts.offline("BlockTester")
    task = Task("Real install", None)
    task.started = time.time()
    done = threading.Event()

    def progress():
        while not done.wait(8):
            print(json.dumps({"message": task.message, "progress": round(task.progress, 1), "MB": round(task.bytes / 1048576, 1)}, ensure_ascii=True), flush=True)

    threading.Thread(target=progress, daemon=True).start()
    try:
        app.game.install(instance["id"], task)
        print("GAME_INSTALL_OK", flush=True)
        versions = app.content.versions("AANobbMI", instance["id"], "mod")
        app.content.install_project("AANobbMI", versions[0]["id"], instance["id"], "mod", "modrinth", task)
        print("SODIUM_INSTALL_OK", flush=True)
        exported = app.content.export_pack(instance["id"], task)
        print("PACK_EXPORT_OK", exported["filename"], flush=True)
        imported = app.content.import_pack(exported["path"], task)
        print("PACK_IMPORT_OK", imported["instance_id"], flush=True)
        if "--launch" in sys.argv:
            launch = app.game.launch(instance["id"], account["id"], task)
            print("PROCESS_STARTED", launch["pid"], flush=True)
            for _ in range(60):
                time.sleep(1)
                process = app.game.process_status()[0]
                if not process["running"]:
                    raise RuntimeError("Game exited early: " + app.game.log(instance["id"])[-3000:])
                log = app.game.log(instance["id"])
                if "OpenAL initialized" in log or "Created:" in log and "textures/atlas" in log:
                    print("GAME_RENDERER_READY", flush=True)
                    break
            else:
                raise RuntimeError("Game renderer readiness not observed: " + app.game.log(instance["id"])[-2000:])
            app.game.stop(instance["id"])
            print("GAME_STOP_OK", flush=True)
    finally:
        done.set()


if __name__ == "__main__":
    main()

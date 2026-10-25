"""Install a normalized public pack into a new, incomplete-until-finished instance."""
import concurrent.futures
import os
import shutil
import uuid

from .core import Cancelled, LauncherError, extract_zip, safe_path
from .progress import DownloadProgress
from .pack_session import pack_session


def install_pack(content, provider, project, version, task):
    with pack_session(content, task) as session:
        return _install_pack(content, provider, project, version, task, session)


def _install_pack(content, provider, project, version, task, session):
    content.store.require_minecraft()
    task.check()
    task.update("读取整合包安装清单", 1)
    plan = content.providers.manifest(provider, project, version)
    task.check()
    if not plan.get("game_version"):
        raise LauncherError("整合包未提供游戏版本")
    # Reject Windows aliases/collisions before creating or installing anything.
    seen = set()
    for item in plan["files"]:
        key = item["path"].replace("\\", "/").casefold()
        if key in seen:
            raise LauncherError("整合包包含重复文件路径：" + item["path"])
        seen.add(key)
    instance = session.create(plan["name"] + " " + plan.get("version_name", str(version)), plan["game_version"], plan["loader"], plan["loader_version"])
    identifier = instance["id"]
    content.store.update_instance(identifier, {"pack": plan["name"], "pack_provider": provider,
        "pack_project": project, "pack_version": str(version), "pack_install_pending": True, "ready": False})
    stage = session.archive_path("files")
    stage.mkdir()
    try:
        content.game.install(identifier, task)
        content.store.update_instance(identifier, {"ready": False})
        entries = plan["archives"] + plan["files"]
        jobs = [dict(item, target=safe_path(stage, str(index) + "-" + item["path"].split("/")[-1]))
                for index, item in enumerate(entries)]
        progress = DownloadProgress(task, jobs, "下载整合包文件", 20, 92)
        def download(index, job):
            task.check()
            last = None
            for url in job["urls"]:
                try:
                    kwargs = {key: job[key] for key in ("sha1", "sha256", "sha512", "md5") if job.get(key)}
                    content.net.download(url, job["target"], task=progress.file(index), size=job.get("size"), **kwargs)
                    return
                except Cancelled:
                    raise
                except Exception as exc:
                    last = exc
                    task.check()
            if content.ai_downloads.recover(job, content._pack_context(identifier), progress.file(index)):
                return
            raise LauncherError("整合包文件下载失败：" + job["path"] + " (" + str(last)[:180] + ")")
        workers = max(1, min(8, int(content.store.config()["concurrency"])))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(download, index, job) for index, job in enumerate(jobs)]
            try:
                for future in concurrent.futures.as_completed(futures):
                    future.result()
                    task.check()
            except Exception:
                # Stop active workers as well, including any pending UI choice.
                task.cancel.set()
                for future in futures:
                    future.cancel()
                raise
        directory = content.store.game_dir(identifier)
        task.update("安装整合包配置与模组", 94)
        for item in jobs[:len(plan["archives"])]:
            task.check()
            destination = safe_path(directory, item.get("destination") or "")
            prefix = item.get("prefix", "")
            extract_zip(item["target"], destination, prefix=prefix.rstrip("/") + "/" if prefix else "", task=task)
        for item in jobs[len(plan["archives"]):]:
            task.check()
            target = safe_path(directory, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".pack-" + uuid.uuid4().hex)
            try:
                shutil.copy2(str(item["target"]), str(temporary))
                os.replace(str(temporary), str(target))
            finally:
                if temporary.exists():
                    temporary.unlink()
        task.check()
        content.store.update_instance(identifier, {"ready": True, "pack_install_pending": False})
        task.update("整合包安装完成", 100)
        return {"instance_id": identifier}
    finally:
        # This freshly generated UUID directory contains only this task's files.
        shutil.rmtree(str(stage), ignore_errors=True)

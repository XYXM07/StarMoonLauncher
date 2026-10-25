import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from .accounts import Accounts
from .content import ContentManager
from .core import Cancelled, LauncherError, Network, Store, TaskManager, atomic_json, safe_path
from .game import GameManager
from .diagnostics import Diagnostics, endpoint
from .player_skins import PlayerSkins
from .appearance import PRESET_IDS
from . import __version__
from .updater import UpdateManager


class Application:
    def __init__(self, data_root, minecraft_root=None):
        self.store = Store(data_root, minecraft_root=minecraft_root)
        self.net = Network(self.store.config)
        self.tasks = TaskManager()
        self.accounts = Accounts(self.store)
        self.game = GameManager(self.store, self.net, self.accounts)
        from .folders import add_folder
        for folder in self.store.config().get("minecraft_folders", []):
            try:
                add_folder(self.game, folder, select=False)
            except (LauncherError, OSError, ValueError):
                pass
        self.skins = self.game.skins
        self.player_skins = PlayerSkins(self.store)
        self.content = ContentManager(self.store, self.net, self.game)
        self.diagnostics = Diagnostics(self.store,self.game)
        self.diagnostic_jobs = {}
        self.game.on_exit = self.on_game_exit
        self.updater = UpdateManager(self.store, self.net)

    def state(self):
        return {"config": self.store.public_config(), "instances": self.store.instances(),
                "tasks": self.tasks.list(), "processes": self.game.process_status(),
                "data_path": str(self.store.root), "minecraft_path": str(self.store.selected_minecraft()),
                "minecraft_folders": self.store.folders(), "version": __version__}

    def running_check(self, instance_id):
        if any(p["instance_id"] == instance_id and (p["running"] or not p.get("finalized",True)) for p in self.game.process_status()):
            raise LauncherError("请先关闭此实例的游戏")

    def config_update(self, changes):
        allowed = {"source", "memory", "memory_auto", "java_path", "java_auto", "width", "height", "jvm_args", "game_args", "client_id", "curseforge_key", "concurrency", "selected_instance", "selected_account", "theme", "animations", "login_mode", "offline_name", "background_path", "background_opacity", "glass_opacity", "glass_refraction", "download_parallel", "download_segments", "launcher_width", "launcher_height"}
        allowed.update(("color_scheme","ai_base_url","ai_model","ai_crash_analysis","ai_auto_preflight","ai_github_search","ai_pack_download_fallback"))
        allowed.update(("background_mode","background_preset"))
        allowed.add("language")
        allowed.add("auto_check_updates")
        api_key = str(changes.get("ai_api_key","")).strip()
        clear_key = bool(changes.get("ai_clear_key",False))
        changes = {k: v for k, v in changes.items() if k in allowed}
        if "language" in changes and changes["language"] not in ("zh_CN", "en_US"):
            raise LauncherError("未知语言")
        if "background_path" in changes and "background_mode" not in changes:
            changes["background_mode"] = "custom" if str(changes["background_path"]).strip() else "none"
        if "background_mode" in changes and changes["background_mode"] not in ("default","preset","custom","none"):
            raise LauncherError("未知壁纸模式")
        if "background_preset" in changes and changes["background_preset"] not in PRESET_IDS:
            raise LauncherError("未知预设壁纸")
        if "color_scheme" in changes and changes["color_scheme"] not in ("starmoon","aurora","morandi","graphite","glacier","twilight","night"):
            raise LauncherError("未知配色")
        for key in ("ai_crash_analysis","ai_auto_preflight","auto_check_updates","ai_github_search","ai_pack_download_fallback"):
            if key in changes: changes[key] = bool(changes[key])
        if "ai_base_url" in changes:
            changes["ai_base_url"] = str(changes["ai_base_url"]).strip().rstrip("/")
            if changes["ai_base_url"]: endpoint(changes["ai_base_url"])
        if "ai_model" in changes:
            changes["ai_model"] = str(changes["ai_model"]).strip()
            if len(changes["ai_model"])>200: raise LauncherError("模型名称过长")
        for key, low, high in (("memory", 512, 65536), ("width", 640, 7680), ("height", 480, 4320), ("concurrency", 1, 16), ("background_opacity", 0, 100), ("glass_opacity", 0, 120), ("glass_refraction", 0, 200), ("download_segments",1,16), ("launcher_width",900,7680), ("launcher_height",600,4320)):
            if key in changes:
                changes[key] = int(changes[key])
                if not low <= changes[key] <= high:
                    raise LauncherError(key + " 超出允许范围")
        if "source" in changes and changes["source"] not in ("official", "bmclapi"):
            raise LauncherError("未知的下载源")
        if "theme" in changes and changes["theme"] not in ("dark", "light", "glass"):
            raise LauncherError("未知主题")
        if "java_auto" in changes:
            changes["java_auto"] = bool(changes["java_auto"])
        if "download_parallel" in changes:
            changes["download_parallel"] = bool(changes["download_parallel"])
        if "memory_auto" in changes:
            changes["memory_auto"] = bool(changes["memory_auto"])
        for key in ("java_path", "jvm_args", "game_args", "client_id", "curseforge_key", "background_path"):
            if key in changes:
                changes[key] = str(changes[key]).strip()[:4000]
        if api_key or clear_key:
            self.diagnostics.save_key(api_key,clear_key)
        self.store.save(changes)
        return self.store.public_config()

    def start_game(self,instance_id,account_id,task,skip_preflight=False):
        if self.store.config().get("ai_auto_preflight") and not skip_preflight:
            task.update("启动前智能排查",1)
            try:
                report = self.diagnostics.analyze(instance_id,"preflight",task,reuse=True)
            except Cancelled:
                raise
            except LauncherError as exc:
                report = self.diagnostic_error(instance_id,"preflight",str(exc))
            if report["status"] != "ok":
                return {"launch_blocked":True,"diagnostic_report":report,"resume_account":account_id}
        try:
            return self.game.launch(instance_id,account_id,task)
        except Cancelled:
            raise
        except Exception as exc:
            self.on_game_exit(instance_id,time.time(),-1,False,str(exc))
            raise

    def diagnostic_error(self,instance_id,kind,message):
        return {"instance_id":instance_id,"kind":kind,"status":"unknown","source":"error",
                "cause":message[:180],"suggestion":"检查智能排查设置和网络后重试；也可查看游戏日志自行排查。","time":time.time()}

    def on_game_exit(self,instance_id,started,code,stopped=False,extra_error="",process_id=None):
        if code in (0,None) or stopped or not self.store.config().get("ai_crash_analysis") or not self.diagnostics.configured():
            return
        key = (instance_id,process_id or started)
        with self.diagnostics.lock:
            if key in self.diagnostics.seen_crashes: return
            self.diagnostics.seen_crashes.add(key)
        self.submit_diagnostic(instance_id,"crash",started,extra_error,process_id)

    def submit_diagnostic(self,instance_id,kind="preflight",started=None,extra_error="",process_id=None):
        if kind not in ("preflight","crash"): raise LauncherError("未知排查类型")
        self.store.instance(instance_id)
        def analyze(task):
            task.update("分析崩溃日志" if kind == "crash" else "读取启用模组与前置依赖",1)
            try:
                report = self.diagnostics.analyze(instance_id,kind,task,started,extra_error=extra_error,process_id=process_id)
            except Cancelled:
                raise
            except LauncherError as exc:
                report = self.diagnostic_error(instance_id,kind,str(exc))
            return {"diagnostic_report":report}
        with self.diagnostics.lock:
            key = (instance_id,kind,process_id)
            job = self.diagnostic_jobs.get(key)
            if job in self.tasks.tasks and self.tasks.tasks[job].status in ("queued","running"):
                return {"task_id":job}
            result = self.tasks.submit("智能崩溃分析" if kind == "crash" else "启动前一键排查",analyze)
            self.diagnostic_jobs[key] = result["task_id"]
            return result

    def route(self, method, path, query, body):
        def q(key, default=""):
            return query.get(key, [default])[0]
        if method == "GET":
            if path == "/api/state":
                return self.state()
            if path == "/api/manifest":
                return self.game.manifest()
            if path == "/api/java":
                return self.game.java_candidates()
            if path == "/api/loaders":
                return self.game.loader_versions(q("version"), q("loader"))
            if path == "/api/content/search":
                return self.content.search(q("query"), q("kind", "mod"), q("version"), q("loader"), int(q("offset", "0")), q("provider", "modrinth"), q("sort", "downloads"))
            if path == "/api/content/versions":
                return self.content.versions(q("project"), q("instance"), q("kind", "mod"), q("provider", "modrinth"))
            if path == "/api/content/local":
                return self.content.list_local(q("instance"))
            if path == "/api/content/updates":
                return self.content.check_updates(q("instance"))
            if path == "/api/log":
                return {"text": self.game.log(q("instance"), q("type") == "installer",q("process") or None)}
            if path == "/api/diagnostics/latest":
                return self.diagnostics.latest(q("instance"),q("kind","crash"))
            if path == "/api/auth/status":
                return self.accounts.status(q("id"))
            if path == "/api/backups":
                directory = self.store.instance_dir(q("instance")) / "backups"
                return [{"name": p.name, "size": p.stat().st_size, "date": p.stat().st_mtime} for p in sorted(directory.glob("*.zip"), reverse=True)]
        if method == "POST":
            if path == "/api/config":
                return self.config_update(body)
            if path == "/api/diagnostics/analyze":
                return self.diagnostics.analyze(body["instance"],body.get("kind","preflight"))
            if path == "/api/diagnostics/run":
                return self.submit_diagnostic(body["instance"],body.get("kind","preflight"),process_id=body.get("process"))
            if path == "/api/diagnostics/test":
                config = dict(self.store.config(),ai_base_url=body.get("ai_base_url",""),ai_model=body.get("ai_model",""))
                key = body.get("ai_api_key") or ("" if body.get("ai_clear_key") else self.diagnostics.key())
                self.diagnostics._request({"kind":"connection_test","message":"接口连接测试，请用简短中文返回连接成功。"},config,key)
                return {"success":True}
            if path == "/api/accounts/offline":
                return self.accounts.offline(body.get("name", ""))
            if path == "/api/accounts/delete":
                self.accounts.delete(body["id"])
                return {}
            if path == "/api/accounts/skin":
                return self.skins.save(body["id"],body["path"],body.get("model","default"))
            if path == "/api/accounts/skin/reset":
                return self.skins.reset(body["id"])
            if path == "/api/skins/player":
                return self.player_skins.download(body.get("player",""))
            if path == "/api/skins/export":
                return self.player_skins.export(body["path"],body["destination"])
            if path == "/api/auth/begin":
                return self.accounts.begin(int(body.get("parent_window_handle", 0)))
            if path == "/api/auth/cancel":
                return self.accounts.status(body["id"], True)
            if path == "/api/tasks/cancel":
                self.tasks.cancel_task(body["id"])
                return {}
            if path == "/api/tasks/respond":
                self.tasks.respond(body["id"], body["request"], body["choice"])
                return {}
            if path == "/api/tasks/dismiss":
                self.tasks.dismiss(body.get("id"))
                if not body.get("id"):
                    self.game.dismiss_process()
                return {}
            if path == "/api/processes/dismiss":
                self.game.dismiss_process(body.get("id"))
                return {}
            if path == "/api/processes/stop":
                self.game.stop(process_id=body["id"])
                return {}
            if path == "/api/instances/create":
                instance = self.game.create(body.get("name"), body["version"], body.get("loader", "vanilla"), body.get("loader_version", ""))
                result = self.tasks.submit("安装 " + instance["name"], lambda t: self.game.install(instance["id"], t))
                return dict(result, instance_id=instance["id"])
            if path == "/api/instances/repair":
                self.running_check(body["id"])
                return self.tasks.submit("修复游戏文件", lambda t: self.game.install(body["id"], t))
            if path == "/api/instances/launch":
                return self.tasks.submit("启动游戏", lambda t: self.start_game(body["id"],body["account"],t,bool(body.get("skip_preflight"))))
            if path == "/api/instances/stop":
                self.game.stop(body["id"])
                return {}
            if path == "/api/instances/update":
                self.running_check(body["id"])
                changes = {k: v for k, v in body.items() if k in ("name", "memory", "memory_mode", "java_path", "jvm_args", "game_args")}
                if "memory_mode" in changes and changes["memory_mode"] not in ("inherit", "auto", "manual"):
                    raise LauncherError("未知内存模式")
                if "name" in changes:
                    changes["name"] = str(changes["name"]).strip()[:80]
                    if not changes["name"]:
                        raise LauncherError("实例名称不能为空")
                if "memory" in changes:
                    changes["memory"] = int(changes["memory"])
                    if changes["memory"] != 0 and not 512 <= changes["memory"] <= 65536:
                        raise LauncherError("内存范围为 512–65536 MB；0 表示继承全局设置")
                return self.store.update_instance(body["id"], changes)
            if path == "/api/instances/delete":
                self.running_check(body["id"])
                def recycle(task):
                    self.running_check(body["id"])
                    directory = self.store.instance_dir(body["id"])
                    instance = self.store.instance(body["id"])
                    if instance.get("external_minecraft"):
                        hidden = self.store.config().get("hidden_external_instances", [])
                        if body["id"] not in hidden:
                            self.store.save({"hidden_external_instances": hidden + [body["id"]]})
                    destination = safe_path(self.store.root / "trash", body["id"] + "-" + uuid.uuid4().hex[:8])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(directory), str(destination))
                    if self.store.config()["selected_instance"] == body["id"]:
                        self.store.save({"selected_instance": ""})
                    return {"path": str(destination)}
                return self.tasks.submit("将实例移至回收目录", recycle)
            if path == "/api/instances/duplicate":
                def duplicate(task):
                    self.running_check(body["id"])
                    instance = self.store.instance(body["id"])
                    if instance.get("external_minecraft"):
                        raise LauncherError("外部版本请在原 .minecraft 目录复制后刷新列表")
                    created = self.game.create(instance["name"] + " 副本", instance["game_version"], instance["loader"], instance.get("loader_version", ""))
                    task.update("复制实例文件", 30)
                    shutil.copytree(str(self.store.game_dir(body["id"])), str(self.store.game_dir(created["id"])), dirs_exist_ok=True)
                    content_record = self.store.instance_dir(body["id"]) / "content.json"
                    if content_record.is_file():
                        shutil.copy2(str(content_record), str(self.store.instance_dir(created["id"]) / "content.json"))
                    self.store.update_instance(created["id"], {k: v for k, v in instance.items() if k not in ("id", "name", "created", "last_played", "play_seconds")})
                    # Native binaries live outside game/, so prepare the new instance too.
                    self.game.install(created["id"], task)
                    return {"instance_id": created["id"]}
                return self.tasks.submit("复制实例", duplicate)
            if path == "/api/folders/create":
                return self.store.create_minecraft()
            if path == "/api/folders/select" and Path(body["path"]).resolve() == self.store.minecraft_root and not (self.store.minecraft_root / "versions").is_dir():
                self.store.save({"library_root": str(self.store.minecraft_root)})
                return {"path": str(self.store.minecraft_root), "instances": [], "skipped": []}
            if path in ("/api/instances/import", "/api/folders/add", "/api/folders/select"):
                from .folders import add_folder
                return add_folder(self.game, body["path"])
            if path == "/api/content/install":
                options = {"allow_unknown": body.get("allow_unknown") is True} if body.get("provider") == "github" else {}
                return self.tasks.submit("安装下载内容", lambda t: self.content.install_project(str(body["project"]), str(body["version"]), body.get("instance", ""), body["kind"], body.get("provider", "modrinth"), t, **options))
            if path == "/api/content/toggle":
                return self.tasks.submit("管理实例内容", lambda t: self.content.toggle(body["instance"], body["filename"], body["kind"], body.get("remove", False)))
            if path == "/api/pack/export":
                return self.tasks.submit("导出整合包", lambda t: self.content.export_pack(body["instance"], t))
            if path == "/api/backup":
                def backup(task):
                    self.running_check(body["instance"])
                    directory = self.store.instance_dir(body["instance"])
                    saves = self.store.game_dir(body["instance"]) / "saves"
                    if not saves.is_dir():
                        raise LauncherError("此实例还没有世界存档")
                    destination = directory / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".zip")
                    destination.parent.mkdir(exist_ok=True)
                    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
                        for p in saves.rglob("*"):
                            task.check()
                            if p.is_file() and not p.is_symlink():
                                task.update("备份世界 · " + p.name, 50)
                                zf.write(str(p), p.relative_to(saves).as_posix())
                    return {"path": str(destination)}
                return self.tasks.submit("备份世界存档", backup)
            if path == "/api/folder":
                targets = {"data": self.store.root, "minecraft": self.store.selected_minecraft(), "exports": self.store.root / "exports", "trash": self.store.root / "trash"}
                if body.get("instance"):
                    directory = self.store.instance_dir(body["instance"])
                    game = self.store.game_dir(body["instance"])
                    targets.update(game=game, mods=game / "mods", saves=game / "saves", instance=directory, backups=directory / "backups")
                    if body.get("target") == "world":
                        world = safe_path(game / "saves", body.get("world", ""))
                        if not body.get("world") or not world.is_dir() or world.is_symlink():
                            raise LauncherError("存档目录不存在")
                        targets["world"] = world
                destination = targets.get(body.get("target", "data"))
                if destination is None:
                    raise LauncherError("未知目录")
                if body.get("target") == "minecraft" and not destination.is_dir():
                    raise LauncherError(".minecraft 文件夹不可用，请检查磁盘")
                destination.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    os.startfile(str(destination))
                return {"path": str(destination)}
        raise LauncherError("接口不存在")


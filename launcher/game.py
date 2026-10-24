import concurrent.futures
import copy
import glob
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .core import (MANIFEST, Cancelled, LauncherError, atomic_json, extract_zip,
                   read_json, request_json, safe_path, valid_id, version_id)
from .memory import effective_memory, java_bits
from .skins import SkinManager
from . import __version__


def os_name():
    return {"Windows": "windows", "Darwin": "osx"}.get(platform.system(), "linux")


def rules_allow(rules, features=None):
    if not rules:
        return True
    allowed = False
    for rule in rules:
        system = rule.get("os", {})
        if system.get("name", os_name()) != os_name():
            continue
        arch = system.get("arch")
        machine = platform.machine().lower()
        current_arch = "x86" if machine in ("x86", "i386", "i686") else "arm64" if "arm" in machine or "aarch" in machine else "x86_64"
        if arch and arch.replace("amd64", "x86_64") != current_arch:
            continue
        if "version" in system and not re.search(system["version"], platform.version()):
            continue
        if any((features or {}).get(k, False) != v for k, v in rule.get("features", {}).items()):
            continue
        allowed = rule["action"] == "allow"
    return allowed


def maven_path(name):
    coordinate, _, extension = name.partition("@")
    parts = coordinate.split(":")
    if len(parts) not in (3, 4):
        raise LauncherError("无法识别 Maven 库：" + name)
    group, artifact, version = parts[:3]
    classifier = "-" + parts[3] if len(parts) == 4 else ""
    return "{}/{}/{}/{}-{}{}.{}".format(group.replace(".", "/"), artifact, version, artifact, version, classifier, extension or "jar")


def library_key(library):
    parts = library["name"].split(":")
    return ":".join(parts[:2] + parts[3:])


def merge_profiles(parent, child):
    result = copy.deepcopy(parent)
    result.update({k: v for k, v in child.items() if k not in ("arguments", "libraries")})
    children = child.get("libraries", [])
    replaced = {library_key(lib) for lib in children}
    # Preserve multiple OS-conditioned declarations from the same parent profile.
    result["libraries"] = [lib for lib in parent.get("libraries", []) if library_key(lib) not in replaced] + children
    if "downloads" in parent or "downloads" in child:
        result["downloads"] = dict(parent.get("downloads", {}), **child.get("downloads", {}))
    arguments = copy.deepcopy(parent.get("arguments", {}))
    for key, value in child.get("arguments", {}).items():
        arguments[key] = arguments.get(key, []) + value
    if arguments:
        result["arguments"] = arguments
    return result


def split_args(value):
    # Minecraft JVM options use quotes for paths on Windows too.
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p
            for p in shlex.split(value or "", posix=False)]


def expand_arguments(arguments, values, features=None):
    output = []
    for arg in arguments:
        if isinstance(arg, dict):
            if not rules_allow(arg.get("rules"), features):
                continue
            value = arg["value"]
            items = value if isinstance(value, list) else [value]
        else:
            items = [arg]
        for item in items:
            output.append(re.sub(r"\$\{([^}]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), item))
    return output


def shorten_classpath(command, directory, java_major):
    """Avoid Windows' 32767-character command-line limit without persisting tokens."""
    if java_major < 9 or len(subprocess.list2cmdline(command)) < 28000:
        return command
    for flag in ("-cp", "-classpath", "--class-path"):
        if flag in command:
            index = command.index(flag)
            value = command[index + 1]
            argument_file = Path(directory) / "classpath.args"
            # Java's argument-file syntax treats backslashes and quotes as escapes.
            argument_file.write_text(flag + '\n"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"\n', encoding="utf-8")
            return command[:index] + ["@" + str(argument_file)] + command[index + 2:]
    return command


class GameManager:
    def __init__(self, store, network, accounts):
        self.store = store
        self.net = network
        self.accounts = accounts
        self.skins = SkinManager(store,accounts)
        self.mc = store.minecraft_root
        self.manifest_cache = store.root / "cache" / "manifest.json"
        self.processes = {}
        self.process_lock = threading.RLock()
        self.on_exit = None

    def manifest(self):
        try:
            data = self.net.json(MANIFEST)
            atomic_json(self.manifest_cache, data)
            data["cached"] = False
            return data
        except Exception:
            cached = read_json(self.manifest_cache)
            if cached:
                cached["cached"] = True
                return cached
            raise

    def profile_path(self, version):
        version_id(version)
        return safe_path(self.mc / "versions", version + "/" + version + ".json")

    def context(self, instance):
        root = Path(instance.get("external_minecraft") or self.store.minecraft_root).resolve()
        if root == self.mc:
            return self
        if not root.is_dir():
            raise LauncherError("外部 .minecraft 文件夹不可用")
        manager = copy.copy(self)
        manager.mc = root
        return manager

    def profile(self, version, seen=None):
        seen = set(seen or [])
        if version in seen or len(seen) > 15:
            raise LauncherError("版本继承关系存在循环或过深")
        seen.add(version)
        data = read_json(self.profile_path(version))
        if not data:
            raise LauncherError("版本元数据缺失，请修复实例：" + version)
        if "inheritsFrom" in data:
            data = merge_profiles(self.profile(data["inheritsFrom"], seen), data)
        return data

    def create(self, name, version, loader="vanilla", loader_version=""):
        self.store.require_minecraft()
        valid_id(version)
        if loader not in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
            raise LauncherError("不支持此加载器")
        name = (name or version).strip()[:80]
        instance_id = "instance-" + uuid.uuid4().hex[:10]
        directory = self.store.instance_dir(instance_id)
        (directory / "game").mkdir(parents=True)
        data = {"id": instance_id, "name": name, "game_version": version, "version": version,
                "loader": loader, "loader_version": loader_version, "ready": False,
                "created": time.time(), "last_played": 0, "play_seconds": 0,
                "memory": 0, "java_path": "", "jvm_args": "", "game_args": ""}
        atomic_json(directory / "instance.json", data)
        self.store.save({"selected_instance": instance_id, "library_root": str(self.store.minecraft_root)})
        return data

    def ensure_base(self, version, task):
        task.update("获取版本元数据 · " + version, 2)
        path = self.profile_path(version)
        if path.exists():
            return read_json(path)
        manifest = self.manifest()
        entry = next((v for v in manifest["versions"] if v["id"] == version), None)
        if not entry:
            raise LauncherError("官方版本列表中没有这个版本：" + version)
        self.net.download(entry["url"], path, sha1=entry.get("sha1"), task=task)
        return read_json(path)

    def loader_versions(self, version, loader):
        version = valid_id(version)
        encoded = urllib.parse.quote(version, safe="")
        if loader in ("fabric", "quilt"):
            host = "https://meta.fabricmc.net" if loader == "fabric" else "https://meta.quiltmc.org"
            result = self.net.json(host + "/v2/versions/loader/" + encoded) if loader == "fabric" else self.net.json(host + "/v3/versions/loader/" + encoded)
            return [{"version": r["loader"]["version"], "stable": r["loader"].get("stable", True)} for r in result]
        if loader == "forge":
            try:
                result = self.net.json("https://bmclapi2.bangbang93.com/forge/minecraft/" + encoded)
                return [{"version": r["version"], "stable": True} for r in reversed(result)]
            except LauncherError:
                # Official promotions remain usable when the complete mirror list fails.
                result = self.net.json("https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")
                values = [result.get("promos", {}).get(version + "-" + key) for key in ("recommended", "latest")]
                return [{"version": v, "stable": True} for v in dict.fromkeys(v for v in values if v)]
        if loader == "neoforge":
            from urllib.request import Request, urlopen
            from .core import USER_AGENT
            from .core import SSL_CONTEXT
            with urlopen(Request("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml", headers={"User-Agent": USER_AGENT}), timeout=30, context=SSL_CONTEXT) as response:
                root = ET.fromstring(response.read())
            # NeoForge's major.minor tracks MC 1.major.minor (1.21.1 -> 21.1).
            parts = version.split(".")
            if len(parts) < 2 or parts[0] != "1":
                return []
            prefix = parts[1] + "." + (parts[2] if len(parts) > 2 else "0") + "."
            return [{"version": e.text, "stable": "beta" not in e.text} for e in reversed(root.findall("./versioning/versions/version")) if e.text.startswith(prefix)]
        return []

    def install_loader(self, instance, task):
        loader = instance["loader"]
        version = instance["game_version"]
        if loader == "vanilla":
            return version
        chosen = instance.get("loader_version")
        if not chosen:
            versions = self.loader_versions(version, loader)
            if not versions:
                raise LauncherError("此游戏版本没有可用的 " + loader + " 加载器")
            chosen = next((v["version"] for v in versions if v["stable"]), versions[0]["version"])
            self.store.update_instance(instance["id"], {"loader_version": chosen})
        valid_id(chosen)
        task.update("安装 {} · {}".format(loader.title(), chosen), 8)
        if loader in ("fabric", "quilt"):
            base = "https://meta.fabricmc.net/v2" if loader == "fabric" else "https://meta.quiltmc.org/v3"
            url = base + "/versions/loader/{}/{}/profile/json".format(urllib.parse.quote(version), urllib.parse.quote(chosen))
            profile = self.net.json(url)
            valid_id(profile["id"])
            atomic_json(self.profile_path(profile["id"]), profile)
            return profile["id"]
        # Delegate Forge processor graphs to the official installer.
        if loader == "forge":
            coordinate = version + "-" + chosen
            url = "https://maven.minecraftforge.net/net/minecraftforge/forge/{0}/forge-{0}-installer.jar".format(coordinate)
        else:
            url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/{0}/neoforge-{0}-installer.jar".format(chosen)
        installer = safe_path(self.store.root / "cache", loader + "-" + version + "-" + chosen + ".jar")
        self.net.download(url, installer, task=task)
        if not (self.mc / "launcher_profiles.json").exists():
            atomic_json(self.mc / "launcher_profiles.json", {"profiles": {}, "version": 3})
        before = {p.parent.name: p.stat().st_mtime_ns for p in (self.mc / "versions").glob("*/*.json")}
        base = self.profile(version)
        java = self.ensure_java(base.get("javaVersion", {}).get("majorVersion", 8), task, instance)
        log_path = self.store.instance_dir(instance["id"]) / "installer.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([java, "-jar", str(installer), "--installClient", str(self.mc)],
                                       cwd=str(self.mc), stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = time.time()
            while process.poll() is None:
                if task.cancel.wait(0.4) or time.time() - started > 900:
                    process.terminate()
                    process.wait()
                    task.check()
                    raise LauncherError("加载器安装超时，请查看 installer.log")
            if process.returncode:
                raise LauncherError("官方加载器安装器失败，请在实例目录查看 installer.log（部分旧 Forge 需要手动导入已安装版本）")
        changed = [p.parent.name for p in (self.mc / "versions").glob("*/*.json")
                   if p.parent.name not in before or p.stat().st_mtime_ns != before[p.parent.name]]
        candidates = [v for v in changed if loader in v.lower() and chosen in v]
        if not candidates:
            candidates = [p.parent.name for p in (self.mc / "versions").glob("*/*.json") if loader in p.parent.name.lower() and chosen in p.parent.name and self.profile(p.parent.name).get("inheritsFrom") == version]
        if not candidates:
            raise LauncherError("安装器没有生成可用的客户端版本，请查看 installer.log")
        return candidates[0]

    def libraries(self, profile):
        results = []
        for lib in profile.get("libraries", []):
            if not rules_allow(lib.get("rules")):
                continue
            downloads = lib.get("downloads", {})
            artifact = downloads.get("artifact")
            if artifact:
                results.append(dict(artifact, native=False))
            elif "downloads" not in lib:
                path = maven_path(lib["name"])
                results.append({"path": path, "url": lib.get("url", "https://libraries.minecraft.net/").rstrip("/") + "/" + path, "native": False})
            classifier = lib.get("natives", {}).get(os_name())
            if classifier:
                classifier = classifier.replace("${arch}", "64" if platform.machine().endswith("64") else "32")
                native = downloads.get("classifiers", {}).get(classifier)
                if native:
                    results.append(dict(native, native=True, exclude=lib.get("extract", {}).get("exclude", [])))
        return results

    def download_batch(self, jobs, task, label, start=15, end=85):
        if not jobs:
            return
        task.update(label + " · 0 / " + str(len(jobs)), start)
        from .progress import DownloadProgress
        progress = DownloadProgress(task, jobs, label, start, end)
        workers = max(1, min(16, int(self.store.config()["concurrency"])))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            def download(index, job):
                task.check()
                self.net.download(job["url"], job["target"], sha1=job.get("sha1"), sha512=job.get("sha512"), task=progress.file(index), size=job.get("size"))
            futures = [pool.submit(download, index, job) for index, job in enumerate(jobs)]
            try:
                for count, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    future.result()
                    with task.lock:
                        task.message = "{} · {} / {}".format(label, count, len(jobs))
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    def install_files(self, instance, task, native_directory=None):
        profile = self.profile(instance["version"])
        jobs = []
        client = profile.get("downloads", {}).get("client")
        base_id = instance.get("client_jar_version") or profile.get("jar") or instance["game_version"]
        if client:
            jobs.append(dict(client, target=safe_path(self.mc / "versions", base_id + "/" + base_id + ".jar")))
        libs = self.libraries(profile)
        for lib in libs:
            jobs.append(dict(lib, target=safe_path(self.mc / "libraries", lib["path"])))
        asset = profile.get("assetIndex")
        if asset:
            index_path = safe_path(self.mc / "assets" / "indexes", valid_id(asset["id"]) + ".json")
            task.update("获取资源索引", 12)
            self.net.download(asset["url"], index_path, sha1=asset.get("sha1"), task=task)
            objects = read_json(index_path).get("objects", {})
            for obj in {o["hash"]: o for o in objects.values()}.values():
                digest = obj["hash"]
                if not re.fullmatch(r"[a-f0-9]{40}", digest):
                    raise LauncherError("资源索引包含无效哈希")
                jobs.append({"url": "https://resources.download.minecraft.net/{}/{}".format(digest[:2], digest),
                             "target": self.mc / "assets" / "objects" / digest[:2] / digest, "sha1": digest, "size": obj.get("size")})
        logging = profile.get("logging", {}).get("client", {}).get("file")
        if logging:
            jobs.append(dict(logging, target=safe_path(self.mc / "assets" / "log_configs", logging["id"])))
        self.download_batch(jobs, task, "校验 / 下载游戏文件", 15, 90)
        natives = Path(native_directory) if native_directory else self.store.instance_dir(instance["id"]) / "natives"
        natives.mkdir(parents=True, exist_ok=True)
        for lib in libs:
            if lib["native"]:
                extract_zip(safe_path(self.mc / "libraries", lib["path"]), natives,
                            exclude=["META-INF/"] + lib.get("exclude", []), task=task)
        if asset:
            index = read_json(index_path)
            # Legacy Minecraft versions expect the resources to be materialized.
            for flag, directory in (("virtual", self.mc / "assets" / "virtual" / asset["id"]),
                                    ("map_to_resources", self.store.game_dir(instance["id"]) / "resources")):
                if index.get(flag):
                    for name, obj in index["objects"].items():
                        task.check()
                        path = safe_path(directory, name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(self.mc / "assets" / "objects" / obj["hash"][:2] / obj["hash"]), str(path))
        return profile

    def install(self, instance_id, task):
        instance = self.store.instance(instance_id)
        manager = self.context(instance)
        if manager is not self:
            return manager.install(instance_id, task)
        self.store.update_instance(instance_id, {"ready": False})
        self.ensure_base(instance["game_version"], task)
        # Official installers require the vanilla client JAR before processing patches.
        if instance["loader"] in ("forge", "neoforge"):
            vanilla = dict(instance, version=instance["game_version"])
            self.install_files(vanilla, task)
        if not instance.get("loader_installed"):
            version = self.install_loader(instance, task)
            self.store.update_instance(instance_id, {"version": version, "loader_installed": True})
        instance = self.store.instance(instance_id)
        profile = self.install_files(instance, task)
        task.update("检查 Java 运行环境", 92)
        java = self.ensure_java(profile.get("javaVersion", {}).get("majorVersion", 8), task, instance)
        self.store.update_instance(instance_id, {"ready": not instance.get("pack_install_pending", False), "required_java": profile.get("javaVersion", {}).get("majorVersion", 8), "detected_java": java})
        return {"instance_id": instance_id}

    def java_candidates(self):
        paths = []
        config = self.store.config()
        paths += [config.get("java_path"), shutil.which("java")]
        if os.environ.get("JAVA_HOME"):
            paths.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"))
        patterns = [str(self.store.root / "runtime" / "**" / "bin" / "java.exe")]
        for directory in ("C:/Program Files/Java", "C:/Program Files/Eclipse Adoptium", "C:/Program Files/Microsoft", "C:/Program Files/Zulu", "C:/Program Files/Oracle"):
            patterns.append(directory + "/*/bin/java.exe")
        for pattern in patterns:
            paths.extend(glob.glob(pattern, recursive=True))
        results = []
        for path in dict.fromkeys(p for p in paths if p):
            java = self.inspect_java(path)
            if java:
                results.append(java)
        return results

    def ensure_java(self, required, task, instance=None):
        config = self.store.config()
        custom = (instance or {}).get("java_path") or self.store.config().get("java_path")
        candidates = self.java_candidates()
        if custom:
            if not Path(custom).is_file():
                raise LauncherError("指定的 Java 路径不存在，请选择 java.exe")
            # Instance-specific Java is not necessarily included in the global scan.
            selected = next((j for j in candidates if os.path.normcase(j["path"]) == os.path.normcase(custom)), None)
            if selected is None:
                selected = self.inspect_java(custom)
            if not selected:
                raise LauncherError("无法识别指定的 Java，请选择有效的 java.exe")
            if selected["major"] == required:
                return custom
            if not config["java_auto"]:
                raise LauncherError("此版本需要 Java {}，指定的是 Java {}。请更换 Java 或启用自动匹配与下载。".format(required, selected["major"]))
            task.update("指定的 Java {} 不匹配，自动选择 Java {}".format(selected["major"], required), 93)
        exact = next((j for j in candidates if j["major"] == required), None)
        if exact:
            return exact["path"]
        if not config["java_auto"]:
            raise LauncherError("游戏需要 Java {}，请在设置中指定 Java，或打开自动下载".format(required))
        if os.name != "nt":
            raise LauncherError("自动 Java 下载当前支持 Windows，请手动指定 Java")
        task.update("下载 Java {} · Eclipse Temurin".format(required), 93)
        architecture = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
        assets = self.net.json("https://api.adoptium.net/v3/assets/latest/{}/hotspot?architecture={}&image_type=jre&os=windows&vendor=eclipse".format(required, architecture))
        if not assets:
            raise LauncherError("没有找到对应的 Java 运行环境，请手动设置 Java 路径")
        package = assets[0]["binary"]["package"]
        archive = self.store.root / "cache" / ("java-{}.zip".format(required))
        self.net.download(package["link"], archive, task=task, size=package.get("size"))
        # Adoptium publishes SHA-256 rather than SHA-1.
        import hashlib
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                task.check()
                digest.update(chunk)
        if digest.hexdigest() != package["checksum"]:
            archive.unlink()
            raise LauncherError("Java 下载 SHA-256 校验失败")
        destination = self.store.root / "runtime" / str(required)
        extract_zip(archive, destination, task=task)
        candidates = list(destination.glob("*/bin/java.exe"))
        if not candidates:
            raise LauncherError("Java 解压后没有找到 java.exe")
        return str(candidates[0])

    @staticmethod
    def inspect_java(path):
        try:
            result = subprocess.run([path, "-version"], capture_output=True, timeout=6,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = (result.stderr + result.stdout).decode("utf-8", errors="replace")
            match = re.search(r'(?:version\s+"|openjdk\s+)([0-9][^\s"]*)', output)
            if match and result.returncode == 0:
                version = match.group(1)
                major = int(version.split(".")[1] if version.startswith("1.") else re.match(r"\d+", version).group())
                return {"path": str(path), "version": version, "major": major}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def command(self, instance, account, java, runtime_directory=None):
        manager = self.context(instance)
        if manager is not self:
            return manager.command(instance, account, java, runtime_directory)
        profile = self.profile(instance["version"])
        config = self.store.config()
        directory = self.store.instance_dir(instance["id"])
        classpath = [str(safe_path(self.mc / "libraries", lib["path"])) for lib in self.libraries(profile) if not lib["native"]]
        jar_version = instance.get("client_jar_version") or profile.get("jar") or instance["game_version"]
        classpath.append(str(safe_path(self.mc / "versions", jar_version + "/" + jar_version + ".jar")))
        asset_id = profile.get("assetIndex", {}).get("id", profile.get("assets", "legacy"))
        values = {"auth_player_name": account["name"], "version_name": instance["version"],
                  "game_directory": str(self.store.game_dir(instance["id"])), "assets_root": str(self.mc / "assets"),
                  "assets_index_name": asset_id, "auth_uuid": account["id"],
                  "auth_access_token": account["access_token"], "auth_session": "token:{}:{}".format(account["access_token"], account["id"]),
                  "user_type": account["user_type"], "version_type": profile.get("type", "release"),
                  "natives_directory": str(Path(runtime_directory or directory) / "natives"), "launcher_name": "StarMoon",
                  "launcher_version": __version__, "classpath": os.pathsep.join(classpath),
                  "classpath_separator": os.pathsep, "library_directory": str(self.mc / "libraries"),
                  "resolution_width": str(config["width"]), "resolution_height": str(config["height"]),
                  "user_properties": "{}", "clientid": account.get("client_id", ""), "auth_xuid": account.get("xuid", ""),
                  "game_assets": str(self.mc / "assets" / "virtual" / asset_id)}
        features = {"has_custom_resolution": True, "is_demo_user": False, "has_quick_plays_support": False}
        arguments = profile.get("arguments", {})
        jvm = expand_arguments(arguments.get("jvm", []), values, features)
        # Forge's profile assumes the client JAR is named after the child
        # version. Our shared cache retains the parent JAR's filename.
        # BootstrapLauncher must ignore that actual file when building modules.
        if profile.get("mainClass") == "cpw.mods.bootstraplauncher.BootstrapLauncher":
            for index, argument in enumerate(jvm):
                if argument.startswith("-DignoreList="):
                    jvm[index] = argument + "," + jar_version + ".jar"
        if not jvm:
            jvm = ["-Djava.library.path=" + values["natives_directory"], "-cp", values["classpath"]]
        mods = self.store.game_dir(instance["id"]) / "mods"
        mod_count = sum(1 for path in mods.glob("*.jar") if path.is_file())
        memory = effective_memory(config, instance, mod_count=mod_count,
                                  legacy=profile.get("javaVersion", {}).get("majorVersion", 8) < 17, bits=java_bits(java))
        command = [java, "-Xms256M", "-Xmx{}M".format(memory), "-Dfile.encoding=UTF-8"]
        command += jvm + split_args(config["jvm_args"]) + split_args(instance.get("jvm_args"))
        log = profile.get("logging", {}).get("client")
        if log:
            path = safe_path(self.mc / "assets" / "log_configs", log["file"]["id"])
            command.append(log["argument"].replace("${path}", str(path)))
        command.append(profile["mainClass"])
        if "game" in arguments:
            command += expand_arguments(arguments["game"], values, features)
        else:
            command += expand_arguments(split_args(profile.get("minecraftArguments", "")), values, features)
            command += ["--width", str(config["width"]), "--height", str(config["height"])]
        command += split_args(config["game_args"]) + split_args(instance.get("game_args"))
        unresolved = [arg for arg in command if "${" in arg]
        if unresolved:
            raise LauncherError("版本包含不支持的启动变量：" + ", ".join(unresolved)[:150])
        return command

    def launch(self, instance_id, account_id, task):
        instance = self.store.instance(instance_id)
        manager = self.context(instance)
        if manager is not self:
            return manager.launch(instance_id, account_id, task)
        process_id = "run-"+uuid.uuid4().hex
        directory = self.store.instance_dir(instance_id)
        runtime = directory / "runs" / process_id
        runtime.mkdir(parents=True,exist_ok=True)
        instance = self.store.instance(instance_id)
        if instance.get("external_minecraft"):
            # Reuse original assets/libraries and prepare private native binaries.
            # No external profile or game content is copied into managed storage.
            self.install_files(instance, task, native_directory=runtime/"natives")
        if not instance.get("ready"):
            self.install(instance_id, task)
            instance = self.store.instance(instance_id)
        if not instance.get("ready"):
            raise LauncherError("整合包内容尚未完整安装，请重新导入整合包；修复游戏文件不会跳过缺失的整合包内容")
        task.update("验证账号与 Java 环境", 96)
        account = self.accounts.for_launch(account_id)
        profile = self.profile(instance["version"])
        java = self.ensure_java(profile.get("javaVersion", {}).get("majorVersion", 8), task, instance)
        if not (runtime/"natives").exists():
            original_natives = directory/"natives"
            if original_natives.is_dir():
                shutil.copytree(str(original_natives),str(runtime/"natives"))
            else:
                (runtime/"natives").mkdir()
                for lib in self.libraries(profile):
                    if lib["native"]:
                        extract_zip(safe_path(self.mc/"libraries",lib["path"]),runtime/"natives",exclude=["META-INF/"]+lib.get("exclude",[]),task=task)
        skin = self.skins.prepare(account)
        try:
            command = self.command(instance, account, java, runtime)
            if skin:
                command[1:1] = skin.arguments
            task.check()
            if os.name == "nt":
                command = shorten_classpath(command, runtime, profile.get("javaVersion", {}).get("majorVersion", 8))
        except Exception:
            if skin: skin.close()
            raise
        log_path = runtime / "launcher-game.log"
        # Pipe output through a redactor before writing any game output to disk.
        try:
            game_directory = self.store.game_dir(instance_id)
            game_directory.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(command, cwd=str(game_directory), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            if skin: skin.close()
            raise
        started = time.time()
        record = {"instance_id":instance_id,"process": process, "started": started, "exit_code": None,
                  "skin_session":skin,"stop_requested":False,"log_path":str(log_path),"game_directory":str(game_directory),"shared_directory":False}
        with self.process_lock:
            for item in self.processes.values():
                if item["process"].poll() is None and item.get("game_directory") == str(game_directory):
                    item["shared_directory"] = record["shared_directory"] = True
            self.processes[process_id] = record
        atomic_json(directory/"latest-run.json",{"id":process_id,"started":started,"shared_directory":record["shared_directory"]})
        self.store.update_instance(instance_id, {"last_played": started})

        def monitor():
            with log_path.open("w", encoding="utf-8") as out:
                out.write("星月启动器 · {} · {}\n".format(instance["name"], time.strftime("%Y-%m-%d %H:%M:%S")))
                for line in iter(process.stdout.readline, b""):
                    message = line.decode("utf-8", errors="replace")
                    if account["access_token"] != "0":
                        message = message.replace(account["access_token"], "[REDACTED]")
                    out.write(message)
                    out.flush()
                code = process.wait()
                out.write("\n游戏进程结束，退出代码：{}\n".format(code))
            process.stdout.close()
            if skin: skin.close()
            with self.store.lock:
                current = self.store.instance(instance_id)
                self.store.update_instance(instance_id, {"play_seconds": current.get("play_seconds", 0) + int(time.time() - started), "last_exit_code": code})
            try:
                if self.on_exit:
                    self.on_exit(instance_id,started,code,record.get("stop_requested",False),process_id=process_id)
            except Exception:
                # A diagnostic callback must never prevent process finalization.
                pass
            finally:
                with self.process_lock:
                    record["exit_code"] = code

        threading.Thread(target=monitor, daemon=True).start()
        return {"instance_id": instance_id,"process_id":process_id,"log_path":str(log_path),"pid":process.pid}

    def process_status(self):
        with self.process_lock:
            return [{"id":key,"instance_id":value.get("instance_id",key),"pid":value["process"].pid,"running":value["process"].poll() is None,
                     "started": value["started"], "exit_code": value["process"].poll(), "finalized": value["exit_code"] is not None,
                     "stop_requested": value.get("stop_requested", False)} for key, value in self.processes.items()]

    def dismiss_process(self, instance_id=None):
        with self.process_lock:
            if instance_id is not None and instance_id not in self.processes:
                raise LauncherError("游戏进程不存在")
            keys = [instance_id] if instance_id is not None else list(self.processes)
            for key in keys:
                item = self.processes[key]
                if item["process"].poll() is None or item["exit_code"] is None:
                    if instance_id is not None:
                        raise LauncherError("只能关闭已结束的游戏记录")
                    continue
                del self.processes[key]

    def stop(self, instance_id=None, process_id=None):
        with self.process_lock:
            if process_id is not None:
                if process_id not in self.processes: raise LauncherError("游戏进程不存在")
                items = [self.processes[process_id]]
            else:
                items = [value for key,value in self.processes.items() if value.get("instance_id",key) == instance_id]
            for item in items:
                if item["process"].poll() is None:
                    item["stop_requested"] = True
                    item["process"].terminate()

    def run_info(self,instance_id,process_id=None):
        self.store.instance(instance_id)
        with self.process_lock:
            if process_id:
                record = self.processes.get(process_id)
                if record and record.get("instance_id",process_id) == instance_id:
                    return dict(record,id=process_id)
                if not re.fullmatch(r"run-[a-f0-9]{32}",process_id): raise LauncherError("游戏进程不存在")
                return {"id":process_id,"shared_directory":True}
            records = [dict(value,id=key) for key,value in self.processes.items() if value.get("instance_id",key) == instance_id]
            return max(records,key=lambda record:record["started"]) if records else read_json(self.store.instance_dir(instance_id)/"latest-run.json",{})

    def log_path(self,instance_id,process_id=None):
        directory = self.store.instance_dir(instance_id)
        run = self.run_info(instance_id,process_id)
        identifier = run.get("id","")
        if re.fullmatch(r"run-[a-f0-9]{32}",identifier):
            return directory/"runs"/identifier/"launcher-game.log"
        return directory/"launcher-game.log"

    def log(self, instance_id, installer=False, process_id=None):
        path = self.store.instance_dir(instance_id)/"installer.log" if installer else self.log_path(instance_id,process_id)
        if not path.exists():
            return "还没有日志。启动游戏或安装加载器后，日志会显示在这里。"
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 100000))
            return stream.read().decode("utf-8", errors="replace")

    def import_existing(self, path, name):
        from .folders import add_folder
        result = add_folder(self, path)
        return dict(result, imported=result["instances"])

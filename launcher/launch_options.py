"""Per-instance launch controls and the verified Windows Mesa agent."""
import platform
import struct
from pathlib import Path

from .core import Cancelled, LauncherError
from .memory import java_bits

DEFAULTS = {"renderer":"opengl", "no_default_jvm_args":False, "skip_game_check":False,
            "skip_java_check":False, "skip_native_patch":False}
RENDERERS = (("opengl","OpenGL（默认）"), ("zink","Vulkan（Mesa Zink）"),
             ("d3d12","DirectX 12（Mesa D3D12）"), ("llvmpipe","Mesa LLVMpipe（软件渲染）"))
MESA_VERSION = "26.0.4"
# Published Maven Central artifacts referenced by HMCL's native-library manifest.
MESA = {
    "x64": ("f8da709c59ef61f531c91434ca0e3b4f39202981",49873846),
    "x86": ("ac6afaa8baa7c17468267c09e77e1296ee92d5ed",41742113),
    "arm64": ("5c761a344700a07eaded51c5cf0cde36ee614706",43504189),
}


def java_architecture(java):
    """Read the selected Windows JVM architecture without executing it."""
    try:
        with Path(java).open("rb") as stream:
            if stream.read(2)==b"MZ":
                stream.seek(60)
                offset=struct.unpack("<I",stream.read(4))[0]
                stream.seek(offset)
                if stream.read(4)==b"PE\0\0":
                    machine=struct.unpack("<H",stream.read(2))[0]
                    if machine in (0x14c,0x8664,0xaa64):
                        return {0x14c:"x86",0x8664:"x64",0xaa64:"arm64"}[machine]
    except (OSError,ValueError,struct.error,TypeError):
        pass
    if java_bits(java)==32:
        return "x86"
    return "arm64" if platform.machine().lower() in ("arm64","aarch64") else "x64"


def native_architecture(instance, java):
    if instance and not instance.get("skip_native_patch") and java:
        return {"x86":"x86", "x64":"x86_64", "arm64":"arm64"}[java_architecture(java)]
    return None


def mesa_path(store, java):
    arch=java_architecture(java)
    return store.root/"runtime"/"renderers"/MESA_VERSION/("mesa-loader-windows-"+MESA_VERSION+"-"+arch+".jar")


def prepare_renderer(manager, instance, java, task):
    renderer=instance.get("renderer","opengl")
    if renderer=="opengl":
        return
    if renderer not in dict(RENDERERS):
        raise LauncherError("未知渲染器")
    if platform.system()!="Windows" or int(platform.version().split(".")[0])<10:
        raise LauncherError("Mesa 渲染器需要 Windows 10 或更高版本")
    arch=java_architecture(java)
    sha1,size=MESA[arch]
    target=mesa_path(manager.store,java)
    relative="org/glavo/mesa-loader-windows/{0}/mesa-loader-windows-{0}-{1}.jar".format(MESA_VERSION,arch)
    task.update("准备 Mesa 渲染运行库",94)
    for base in ("https://repo.maven.apache.org/maven2/","https://repo1.maven.org/maven2/"):
        try:
            manager.net.download(base+relative,target,sha1=sha1,size=size,task=task)
            return
        except Cancelled:
            raise
        except LauncherError:
            task.check()
    raise LauncherError("Mesa 渲染运行库下载失败，请重试或选择 OpenGL")


def renderer_arguments(store, instance, java, runtime):
    renderer=instance.get("renderer","opengl")
    if renderer=="opengl":
        return []
    if renderer not in dict(RENDERERS):
        raise LauncherError("未知渲染器")
    return ["-Dorg.glavo.mesa.loader.nativeDir="+str(Path(runtime)/"renderer"),
            "-javaagent:"+str(mesa_path(store,java))+"="+renderer]

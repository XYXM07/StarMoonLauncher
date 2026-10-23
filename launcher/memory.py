"""Physical-memory-aware Java heap selection. Sizes are in MiB."""
import ctypes
import os
import struct


def physical_memory():
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"total": status.total // 1048576, "available": status.available // 1048576}
    if hasattr(os, "sysconf"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            return {"total": os.sysconf("SC_PHYS_PAGES") * page // 1048576,
                    "available": os.sysconf("SC_AVPHYS_PAGES") * page // 1048576}
        except (ValueError, OSError):
            pass
    return {"total": 8192, "available": 4096}


def automatic_memory(total, available, mod_count=0, legacy=False, bits=64):
    desired = (1536 if legacy else 3072) + min(9216, max(0, mod_count) * 32)
    reserve = max(768, min(2048, int(total * .15)))
    budget = min(total * .60, available - reserve, desired, 16384)
    if bits == 32:
        budget = min(budget, 1280)
    return max(512, int(budget // 256) * 256)


def java_bits(path):
    try:
        with open(path, "rb") as stream:
            header = stream.read(64)
            if header[:2] == b"MZ":
                stream.seek(struct.unpack_from("<I", header, 60)[0])
                pe = stream.read(6)
                if pe[:4] == b"PE\0\0":
                    return 32 if struct.unpack_from("<H", pe, 4)[0] == 0x14c else 64
            if header[:4] == b"\x7fELF":
                return 32 if header[4] == 1 else 64
    except (OSError, ValueError, struct.error):
        pass
    return struct.calcsize("P") * 8


def effective_memory(config, instance, memory=None, mod_count=0, legacy=False, bits=None):
    mode = instance.get("memory_mode", "inherit")
    if mode == "manual" or (mode == "inherit" and instance.get("memory", 0) > 0):
        return max(512, min(65536, int(instance["memory"])))
    if mode != "auto" and not config.get("memory_auto", False):
        return int(config["memory"])
    memory = memory or physical_memory()
    return automatic_memory(memory["total"], memory["available"], mod_count, legacy,
                            bits if bits is not None else struct.calcsize("P") * 8)

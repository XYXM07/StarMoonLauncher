"""Own only this installation's fresh instance directories and temporary archives."""
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import Cancelled, LauncherError, safe_path, valid_id


class PackSession:
    def __init__(self, content, task):
        self.content, self.task = content, task
        self.token = uuid.uuid4().hex
        self.previous = {key: content.store.config().get(key, "") for key in ("selected_instance", "library_root")}
        self.instances = []
        self.stage = None
        self.report = {"status": "installing", "cleaned": False, "cleanup_errors": []}
        task.pack_install = self.report

    def register(self, identifier, directory):
        root = self.content.store.instances_root.resolve()
        target = Path(directory).absolute()
        expected = root / valid_id(identifier)
        if target != expected or target.resolve() != expected or target.is_symlink():
            raise LauncherError("整合包实例目录不安全，已停止安装")
        marker = target / ".pack-install-owner"
        self.instances.append((identifier, target, root))
        marker.write_text(self.token, encoding="ascii")

    def create(self, *args):
        return self.content.game.create(*args, on_created=self.register)

    def archive_path(self, filename):
        if self.stage is None:
            root = self.content.store.root.resolve()
            parent = root / "cache" / "pack-sessions"
            if parent.resolve() != parent:
                raise LauncherError("整合包临时目录不安全，已停止安装")
            self.stage = parent / self.token
            self.stage.mkdir(parents=True)
        return safe_path(self.stage, filename)

    def _remove(self, target, parent, owned_instance=False):
        # Verify both the literal and resolved absolute paths immediately before deletion.
        target, parent = target.absolute(), parent.resolve()
        identity = None
        for attempt in range(5):
            if target.parent != parent or target.resolve() != target or target.is_symlink():
                raise LauncherError("拒绝清理非本次安装的目录")
            if not target.exists():
                return
            try:
                info = target.stat()
                current = (info.st_dev, info.st_ino)
                if identity is None:
                    if owned_instance and (target / ".pack-install-owner").read_text(encoding="ascii") != self.token:
                        raise LauncherError("拒绝清理非本次安装的目录")
                    identity = current
                elif current != identity:
                    raise LauncherError("拒绝清理非本次安装的目录")
                # Python 3.8+ on Windows removes nested junctions without following their targets.
                shutil.rmtree(str(target))
                return
            except OSError:
                if not target.exists():
                    return
                if attempt == 4:
                    raise
                # Antivirus/indexing may briefly hold new archives; retain directory identity on retry.
                time.sleep(.05 * (2 ** attempt))

    def finish(self, status):
        errors = []
        if status != "completed":
            for identifier, target, parent in reversed(self.instances):
                try:
                    self._remove(target, parent, owned_instance=True)
                except (OSError, ValueError, LauncherError) as exc:
                    errors.append(str(target) + " · " + str(exc)[:180])
            if self.content.store.config().get("selected_instance") in {item[0] for item in self.instances}:
                try:
                    self.content.store.save(self.previous)
                except OSError as exc:
                    errors.append(str(exc)[:180])
        else:
            for _, target, _ in self.instances:
                try:
                    (target / ".pack-install-owner").unlink()
                except OSError as exc:
                    errors.append(str(exc)[:180])
        if self.stage is not None:
            try:
                self._remove(self.stage, self.stage.parent)
            except (OSError, ValueError, LauncherError) as exc:
                errors.append(str(self.stage) + " · " + str(exc)[:180])
        with self.task.lock:
            self.report.update(status=status, cleaned=not errors, cleanup_errors=errors)


@contextmanager
def pack_session(content, task):
    existing = getattr(task, "_pack_session", None)
    if existing is not None:
        yield existing
        return
    session = PackSession(content, task)
    task._pack_session = session
    try:
        yield session
        task.check()
    except Cancelled:
        session.finish("cancelled")
        raise
    except Exception:
        session.finish("failed")
        raise
    else:
        session.finish("completed")
    finally:
        task._pack_session = None

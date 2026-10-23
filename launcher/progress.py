"""Byte-weighted progress, separate from wire traffic and installation stages."""
import threading


class DownloadProgress:
    def __init__(self, task, jobs, label, start=0, end=95):
        self.task, self.label, self.start, self.end = task, label, start, end
        self.lock = threading.RLock()
        self.entries = [{"size": job.get("size"), "bytes": 0, "done": False} for job in jobs]
        self.total = sum(entry["size"] or 0 for entry in self.entries)
        self.unknown = sum(entry["size"] is None for entry in self.entries)
        self.current = self.done = 0
        self.publish()

    def change(self, index, **changes):
        entry = self.entries[index]
        def credit(item):
            return min(item["bytes"], item["size"]) if item["size"] is not None else item["bytes"]
        self.current -= credit(entry)
        self.total -= entry["size"] or 0
        self.unknown -= entry["size"] is None
        self.done -= entry["done"]
        entry.update(changes)
        self.current += credit(entry)
        self.total += entry["size"] or 0
        self.unknown += entry["size"] is None
        self.done += entry["done"]
        self.publish()

    def file(self, index):
        return FileProgress(self, index)

    def publish(self):
        with self.lock:
            known, total, current, done = not self.unknown, self.total, self.current, self.done
            complete = done == len(self.entries)
            fraction = (current / total if total else 1 if complete else 0) if known else 0
            # Receiving every byte is followed by integrity verification and commit.
            fraction = min(fraction, 1 if complete else .999)
            state = {"label": self.label, "bytes": current, "total": total if known else None,
                     "files": done, "files_total": len(self.entries), "active": not complete,
                     "percent": fraction * 100 if known else None}
            self.task.set_download_state(state, self.start + (self.end - self.start) * fraction)


class FileProgress:
    def __init__(self, batch, index):
        self.batch, self.index = batch, index

    def check(self):
        self.batch.task.check()

    def reset_download(self):
        with self.batch.lock:
            self.batch.change(self.index, bytes=0, done=False)

    def set_download_size(self, size):
        with self.batch.lock:
            entry = self.batch.entries[self.index]
            if entry["size"] is None and size is not None:
                self.batch.change(self.index, size=max(0, int(size)))

    def add_bytes(self, size):
        self.batch.task.add_bytes(size)
        with self.batch.lock:
            self.batch.change(self.index, bytes=self.batch.entries[self.index]["bytes"] + size)

    def complete_download(self, size):
        with self.batch.lock:
            self.batch.change(self.index, size=size, bytes=size, done=True)

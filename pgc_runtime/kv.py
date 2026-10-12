"""localStorage stand-in: one JSON file per key in a data directory (keys are the app's, e.g. pgc.mem.notes)."""
import os
import re
import threading


class KV:
    def __init__(self, data_dir):
        self.dir = os.path.abspath(data_dir)
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key):
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', lambda m: "%%%02X" % ord(m.group(0)), key)
        return os.path.join(self.dir, safe + ".json")

    def get_item(self, key):
        try:
            with open(self._path(key), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def set_item(self, key, value):
        with self._lock:
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(value)
            os.replace(tmp, self._path(key))

    def remove_item(self, key):
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass

    def keys(self):
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".json"):
                out.append(re.sub(r"%([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), fn[:-5]))
        return out


class MemoryKV(KV):
    """In-memory KV (tests, ephemeral bots)."""

    def __init__(self):
        self.d = {}

    def get_item(self, key):
        return self.d.get(key)

    def set_item(self, key, value):
        self.d[key] = value

    def remove_item(self, key):
        self.d.pop(key, None)

    def keys(self):
        return list(self.d)

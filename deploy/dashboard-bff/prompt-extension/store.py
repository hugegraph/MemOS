"""Atomic, fail-closed rules store. No provider credentials or duplicate memory model."""
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_PROMPT = 12000


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Invalid prompt scope")
    return value


def rule(value):
    if not isinstance(value, str) or len(value) > MAX_PROMPT:
        raise ValueError("Prompt must contain at most 12000 characters")
    return value


class PromptStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()

    def _path(self, user, cube):
        key = json.dumps([identity(user), identity(cube)], ensure_ascii=False).encode()
        return self.directory / (hashlib.sha256(key).hexdigest() + ".json")

    def get(self, user, cube):
        path = self._path(user, cube)
        with self.lock:
            try:
                with path.open("rb") as stream:
                    raw = stream.read(100001)
            except FileNotFoundError:
                return {"prompt": "", "updatedAt": "", "revision": 0}
            try:
                if len(raw) > 100000:
                    raise ValueError()
                data = json.loads(raw)
                if data["user_id"] != user or data["mem_cube_id"] != cube:
                    raise ValueError()
                rule(data["prompt"])
                if type(data["revision"]) is not int or data["revision"] < 1:
                    raise ValueError()
                if not isinstance(data["updatedAt"], str):
                    raise ValueError()
                return {k: data[k] for k in ("prompt", "updatedAt", "revision")}
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("Prompt storage is invalid; refusing default fallback") from None

    def save(self, user, cube, prompt):
        rule(prompt)
        path = self._path(user, cube)
        with self.lock:
            old = self.get(user, cube)
            data = {"user_id": user, "mem_cube_id": cube, "prompt": prompt,
                    "updatedAt": datetime.now(timezone.utc).isoformat(), "revision": old["revision"] + 1}
            fd, name = tempfile.mkstemp(prefix=".prompt-", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return {k: data[k] for k in ("prompt", "updatedAt", "revision")}

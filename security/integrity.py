import hashlib
import os
from pathlib import Path
from typing import Iterable


def iter_project_files(base_path: str) -> Iterable[Path]:
    base = Path(base_path)
    for path in base.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        yield path


def verify_project_integrity(base_path: str) -> str:
    sha = hashlib.sha256()
    files = sorted(iter_project_files(base_path), key=lambda p: str(p))

    for path in files:
        sha.update(str(path.relative_to(base_path)).encode("utf-8"))
        sha.update(b"\0")
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha.update(chunk)
    return sha.hexdigest()

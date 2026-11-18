import hashlib
from pathlib import Path


def hash_tree(root: Path) -> str:
    ignored_parts = {".venv", ".pytest_cache", "__pycache__"}
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and not ignored_parts.intersection(path.relative_to(root).parts)
    )
    for path in files:
        relative_path = path.relative_to(root).as_posix().encode()
        digest.update(len(relative_path).to_bytes(4, "big"))
        digest.update(relative_path)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()

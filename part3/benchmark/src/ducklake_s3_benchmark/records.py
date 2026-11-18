"""Small create-only JSON records for stage status and evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def write_create_only_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

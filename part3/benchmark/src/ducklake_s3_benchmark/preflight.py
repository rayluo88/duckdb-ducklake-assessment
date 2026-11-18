"""Local, network-free runtime preflight."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import platform
import sys
from typing import Any

import duckdb

from .config import PreflightConfig


def collect_local_preflight(config: PreflightConfig) -> dict[str, Any]:
    actual_duckdb = duckdb.__version__
    version_match = actual_duckdb == config.expected_duckdb_version
    return {
        "schema_version": 1,
        "stage": "local-preflight",
        "state": "passed" if version_match else "stopped",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_probe_performed": False,
        "config": config.public_view(),
        "checks": {
            "config_validated": True,
            "duckdb_version": version_match,
        },
        "runtime": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "duckdb": actual_duckdb,
            "operating_system": platform.system(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
            "byte_order": sys.byteorder,
        },
    }

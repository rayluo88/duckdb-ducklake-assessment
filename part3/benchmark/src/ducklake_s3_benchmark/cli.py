"""Command-line entry point for local and explicitly gated live preflight."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config import ConfigError, load_config
from .preflight import collect_local_preflight
from .records import write_create_only_json
from .redaction import alias
from .s3_probe import run_s3_probe


_CONFIRMATION = "I-APPROVE-ONE-TINY-PROBE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DuckLake Part 3 SageMaker/S3 preflight")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--remote-probe", action="store_true")
    parser.add_argument("--confirm-live-prefix-probe", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        report = collect_local_preflight(config)
        if args.remote_probe and report["state"] == "passed":
            if args.confirm_live_prefix_probe != _CONFIRMATION:
                raise ConfigError(
                    "live probe requires --confirm-live-prefix-probe " + _CONFIRMATION
                )
            import boto3

            session = boto3.session.Session()
            compute_region = session.region_name
            compute_region_match = compute_region == config.expected_region
            report["compute_region"] = compute_region
            report["checks"]["compute_region"] = compute_region_match
            report["network_probe_performed"] = True
            if not compute_region_match:
                report["state"] = "stopped"
            else:
                identity = session.client("sts").get_caller_identity()
                report["identity"] = {
                    "account_alias": alias(str(identity.get("Account", "")), config.campaign_id),
                    "arn_alias": alias(str(identity.get("Arn", "")), config.campaign_id),
                }
                report["s3_probe"] = run_s3_probe(
                    config,
                    session.client("s3"),
                    confirmation=True,
                )
                report["state"] = report["s3_probe"]["state"]
        write_create_only_json(args.output, report)
    except (ConfigError, FileExistsError, OSError, ValueError) as error:
        print(f"preflight stopped: {error}")
        return 2

    print(f"preflight {report['state']}: {args.output}")
    return 0 if report["state"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

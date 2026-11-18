# Stage 0 Completion — Local Preparation

**Run ID:** `p3-stage0-20260824`  
**Date:** 24 August 2026  
**State:** `passed`  
**Next gate:** Stage 1 SageMaker/S3 preflight

## Completed scope

- Saved the independent Part 3 S3 performance plan, permission boundary, stage protocol and
  primary-source register.
- Pinned DuckDB 1.5.5 in `pyproject.toml` and `uv.lock`, matching the latest stable release checked
  on 24 August 2026. The compatible stable DuckLake 1.0 line is documented; its signed extension
  will be resolved and frozen only in SageMaker after preflight.
- Built a strict, non-secret active-config contract and a network-free local preflight.
- Built a doubly gated one-object S3 probe using the SageMaker execution-role credential chain.
- Added deterministic redaction aliases and private create-only JSON output.
- Packaged only Part 3 preflight source, tests, non-secret configuration examples and provenance.

## Verification evidence

| Check | Result |
|---|---|
| Test-first failure observed | missing Part 3 modules failed collection before implementation |
| Source and boundary suite | 31 passed |
| Installed entry-point regression | passed under locked non-editable wheel install |
| Extracted archive internal checksums | all 20 listed files passed |
| Extracted archive clean install | passed |
| Extracted archive tests | 31 passed in 0.84 seconds |
| Extracted local-only preflight | `passed`; `network_probe_performed=false` |
| Output location redaction | raw synthetic bucket, prefix and work root absent |
| External archive SHA-256 | `96a0e28bcf78f96dfe9cabc6281b91fe7bc37af33ae15dd59a0b1ad607896ad2` |

The macOS verification found that a default editable `uv` install can rely on a hidden `.pth` file
that current Python skips. The package contract therefore uses `--no-editable`; a subprocess
regression test verifies the installed launcher without source-path injection.

## Explicit non-events

No Amazon WorkSpaces or SageMaker session was opened. No AWS identity was requested, no S3 API was
called, no DuckDB extension was installed in staging, no object was created or deleted, and no
performance timing was taken. Part 3 therefore has no S3 result at this checkpoint.

## Stage 1 entry action

the researcher starts Amazon WorkSpaces and waits for the intended the target environment staging SageMaker app to show
`Running`. Upload the archive and adjacent hash, follow `OPERATOR-RUNBOOK.md`, populate the approved
non-secret S3 location locally in staging, run the local-only preflight, and enable the live probe
only after reviewing that report.

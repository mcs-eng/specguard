# Repository guidance

## Verified project facts

- Python support starts at 3.12; the project and dependency groups are defined in `pyproject.toml`.
- `specguard.gate` is the deterministic PDF quote-verification core. It checks only the cited page after normalization and token-boundary matching.
- `specguard.cli` exposes the local `specguard quote-check` command. The hosted demo dependencies are optional under the `demo` extra.
- `specguard.web` contains the FastAPI demo; `scripts/eval_fixtures.py` runs the live evaluation workflow; `scripts/reset_demo_ledger.py` archives and clears demo data and requires `--confirm` for its live operation.
- `fixtures/` contains generated fictional inputs. Tests use in-process fakes for cloud services and are intended to run offline.

## Changes and verification

- Keep fixtures fictional. Do not add credentials, private hostnames, or private IP addresses to source, tests, or documentation.
- Preserve existing assertions when adding test coverage.
- Run the full offline suite with `uv run pytest -q` after code or test changes.
- Do not run the live evaluation or confirm a demo-ledger reset as part of ordinary tests; those operations can call external services or change live data.

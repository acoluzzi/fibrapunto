"""Export the stub's OpenAPI document to contracts/billing-openapi.v1.yaml (§7).

"The interface is described in a machine-readable interface definition, because
the integration layer registers against that description rather than against
hand-written request code. The schema is the contract between the two sides, and
it is versioned with the repository."

The code is the source; the YAML is the reviewable, versioned artefact; a test
fails if they diverge. Schema-first codegen was considered and rejected: Python
OpenAPI client generators would add a build step and a toolchain for no demo
value at this size.

    uv run python -m mock_billing.export_openapi           # write
    uv run python -m mock_billing.export_openapi --check   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from mock_billing.main import app

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_PATH = REPO_ROOT / "contracts" / "billing-openapi.v1.yaml"

BANNER = """\
# GENERATED FILE — DO NOT EDIT.
#
# Source: mock-billing/src/mock_billing/models.py and main.py
# Regenerate: uv run python -m mock_billing.export_openapi
#
# This is §7's machine-readable interface definition. The integration layer
# registers against this document rather than against hand-written request
# code, and validates responses back through it — so a response that does not
# match the contract becomes the typed outcome `serviceUnavailable` rather than
# a parse error thrown at the reasoning layer (§5.1).
#
# The version below is semver and is bumped by hand, in a pull request. The
# /_control endpoints are absent on purpose: a real legacy system does not
# offer a button that makes it fail, and publishing one here would be a lie
# about the thing being integrated with.
"""


def render() -> str:
    schema = app.openapi()
    body = yaml.safe_dump(schema, sort_keys=True, allow_unicode=True, width=100)
    return BANNER + body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    rendered = render()

    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist. Run without --check.", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} is stale — the stub's interface changed.\n"
                f"Regenerate it, and bump `info.version` if the change is "
                f"breaking:\n  uv run python -m mock_billing.export_openapi",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output.name} is up to date.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

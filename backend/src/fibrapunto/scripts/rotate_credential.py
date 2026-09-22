"""Rotate a stored credential (§17.7).

The point of this script is what it does *not* touch. It changes a value in the
credential store and tells the billing system about it. It does not edit code,
it does not edit an instruction, and it does not restart anything — the next
conversation simply works.

That is what "external to the automation and to the instructions" means, made
demonstrable in one command:

    make rotate-credential

Also seeds the store on first run, so a clean clone can reach the stub.
"""

from __future__ import annotations

import argparse
import secrets
import sys

import httpx

from fibrapunto.credentials.store import BILLING_API_TOKEN, FileSecretStore
from fibrapunto.settings import get_settings

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", default=BILLING_API_TOKEN)
    parser.add_argument(
        "--value",
        help="The new secret. Generated if omitted.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Set the value the stub expects by default, for a first run.",
    )
    args = parser.parse_args()

    store = FileSecretStore()
    settings = get_settings()

    if args.seed:
        # Seeding only touches our side: the stub already expects this value.
        store.rotate(args.handle, "billing-dev-token")
        print(f"{GREEN}✓{RESET} {args.handle} seeded in the credential store")
        return 0

    new_value = args.value or f"billing-{secrets.token_urlsafe(24)}"

    # Reachability is checked BEFORE the store is touched. Rotating first and
    # discovering the far side is down leaves the two ends disagreeing about
    # the current credential — every lookup then fails on authorization, and
    # it fails in a way that looks like a perimeter bug rather than a
    # half-finished rotation. Nothing is changed unless both ends can be.
    try:
        httpx.get(f"{settings.billing_base_url}/_control/health", timeout=5.0).raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"{RED}✗{RESET} billing system not reachable ({exc}).\n"
            f"  Nothing was changed — the credential store is untouched.\n"
            f"  Start it with `make up` and run this again."
        )
        return 1

    store.rotate(args.handle, new_value)
    print(f"{GREEN}✓{RESET} {args.handle} rotated in the credential store")

    try:
        response = httpx.post(
            f"{settings.billing_base_url}/_control/credential",
            json={"token": new_value},
            timeout=5.0,
        )
        response.raise_for_status()
        print(f"{GREEN}✓{RESET} billing system now expects the new token")
    except httpx.HTTPError as exc:
        # The narrow window: reachable a moment ago, not now. Say exactly what
        # state things are in and how to get out of it.
        print(
            f"{RED}✗{RESET} the store was rotated but the billing system did "
            f"not accept it ({exc}).\n"
            f"  The two ends now disagree. Recover with:\n"
            f"    make seed-credential && make up"
        )
        return 1

    print(
        f"\n{DIM}No code changed. No instruction changed. Nothing restarted.\n"
        f"The next conversation uses the new credential because the client holds\n"
        f"a handle, not a value (§17.7).{RESET}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

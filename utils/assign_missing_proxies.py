"""Assign proxies to accounts missing proxy links.

Run from repository root after activating the virtual environment:
    python -m utils.assign_missing_proxies --per-account 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Sequence

try:
    from tqdm import tqdm
except ImportError as exc:  # pragma: no cover - utility guard
    raise SystemExit("tqdm is required: pip install tqdm") from exc

from main_logic.database import get_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Link least-used proxies to accounts without any assigned ones."
    )
    parser.add_argument(
        "--per-account",
        type=int,
        default=3,
        help="Number of proxies to ensure for every account (default: 3)",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Allow inactive proxies to be used (default: active only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N accounts without proxies",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be assigned without writing to the database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show backend logs (default: suppress INFO/DEBUG)",
    )
    return parser.parse_args()


async def load_accounts_missing_proxies(limit: int | None) -> Sequence:
    db = get_db()
    accounts = await db.load_all_accounts()
    missing = [acc for acc in accounts if not (getattr(acc, "assigned_proxies", None) or [])]
    if limit is not None:
        return missing[: max(limit, 0)]
    return missing


async def assign_proxies(per_account: int, include_inactive: bool, dry_run: bool, limit: int | None) -> None:
    if per_account < 1:
        raise ValueError("per-account must be at least 1")

    accounts = await load_accounts_missing_proxies(limit)
    total = len(accounts)

    if total == 0:
        tqdm.write("All accounts already have at least one proxy assigned.")
        return

    db = get_db()
    successes: list[str] = []
    partials: list[str] = []
    errors: list[tuple[str, str]] = []

    with tqdm(total=total, desc="Assigning proxies", unit="acct") as progress:
        for account in accounts:
            try:
                if dry_run:
                    result = {
                        "phone_number": account.phone_number,
                        "added": ["dry-run"] * per_account,
                        "remaining": 0,
                    }
                else:
                    result = await db.auto_assign_proxies(
                        account.phone_number,
                        desired_count=per_account,
                        active_only=not include_inactive,
                    )

                added = result.get("added", [])
                remaining = result.get("remaining", 0)

                if added and not remaining:
                    successes.append(f"{account.phone_number}: {len(added)} new proxies")
                elif added and remaining:
                    partials.append(
                        f"{account.phone_number}: {len(added)} added, {remaining} still needed"
                    )
                elif remaining:
                    partials.append(
                        f"{account.phone_number}: no proxies added, {remaining} still needed"
                    )
            except Exception as exc:  # pragma: no cover - defensive utility script
                errors.append((account.phone_number or "<unknown>", str(exc)))
            finally:
                progress.update(1)

    tqdm.write("")
    tqdm.write("Assignment summary")
    tqdm.write("-------------------")
    tqdm.write(f"Accounts processed: {total}")
    tqdm.write(f"Fully satisfied:   {len(successes)}")
    tqdm.write(f"Partial/missing:   {len(partials)}")
    tqdm.write(f"Errors:            {len(errors)}")

    if successes:
        tqdm.write("\nAccounts updated:")
        for line in successes:
            tqdm.write(f"  - {line}")

    if partials:
        tqdm.write("\nAccounts still lacking enough proxies:")
        for line in partials:
            tqdm.write(f"  - {line}")

    if errors:
        tqdm.write("\nErrors encountered:")
        for phone, message in errors:
            tqdm.write(f"  - {phone}: {message}")


def main() -> None:
    args = parse_args()

    if not args.verbose:
        logging.disable(logging.INFO)

    try:
        asyncio.run(
            assign_proxies(
                per_account=args.per_account,
                include_inactive=args.include_inactive,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        )
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")


if __name__ == "__main__":
    main()

# python -m utils.assign_missing_proxies --per-account 3
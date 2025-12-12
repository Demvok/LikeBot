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


def _get_account_field(account: object, field: str, default=None):
    if isinstance(account, dict):
        return account.get(field, default)
    return getattr(account, field, default)


def _coerce_proxy_list(account: object) -> list:
    value = _get_account_field(account, "assigned_proxies")
    return value if isinstance(value, list) else []


def _proxy_field_is_list(account: object) -> bool:
    return isinstance(_get_account_field(account, "assigned_proxies"), list)


async def _normalize_proxy_field(db, phone_number: str) -> None:
    if not phone_number:
        raise ValueError("Cannot normalize assigned_proxies without phone_number")

    collection = getattr(db, "_accounts", None)
    if collection is None:
        raise RuntimeError("Database handle is missing the _accounts collection")

    await collection.update_one(
        {"phone_number": phone_number},
        {"$set": {"assigned_proxies": []}},
    )


def _set_proxy_field(account: object, value: list[str]) -> None:
    if isinstance(account, dict):
        account["assigned_proxies"] = value
        return
    try:
        setattr(account, "assigned_proxies", value)
    except AttributeError:
        pass


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
    missing = [acc for acc in accounts if not _coerce_proxy_list(acc)]
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
    normalized_accounts: list[str] = []
    pending_normalization: list[str] = []

    with tqdm(total=total, desc="Assigning proxies", unit="acct") as progress:
        for account in accounts:
            try:
                phone_number = getattr(account, "phone_number", None)
                if not phone_number:
                    raise ValueError("Encountered an account entry without phone_number")

                needs_normalization = not _proxy_field_is_list(account)
                if needs_normalization:
                    if dry_run:
                        if phone_number not in pending_normalization:
                            pending_normalization.append(phone_number)
                    else:
                        await _normalize_proxy_field(db, phone_number)
                        if phone_number not in normalized_accounts:
                            normalized_accounts.append(phone_number)
                    _set_proxy_field(account, [])

                if dry_run:
                    result = {
                        "phone_number": phone_number,
                        "added": ["dry-run"] * per_account,
                        "remaining": 0,
                    }
                else:
                    result = await db.auto_assign_proxies(
                        phone_number,
                        desired_count=per_account,
                        active_only=not include_inactive,
                    )

                added = result.get("added", [])
                remaining = result.get("remaining", 0)

                if added and not remaining:
                    successes.append(f"{phone_number}: {len(added)} new proxies")
                elif added and remaining:
                    partials.append(
                        f"{phone_number}: {len(added)} added, {remaining} still needed"
                    )
                elif remaining:
                    partials.append(
                        f"{phone_number}: no proxies added, {remaining} still needed"
                    )
            except Exception as exc:  # pragma: no cover - defensive utility script
                label = getattr(account, "phone_number", None) or "<unknown>"
                errors.append((label, str(exc)))
            finally:
                progress.update(1)

    tqdm.write("")
    tqdm.write("Assignment summary")
    tqdm.write("-------------------")
    tqdm.write(f"Accounts processed: {total}")
    tqdm.write(f"Fully satisfied:   {len(successes)}")
    tqdm.write(f"Partial/missing:   {len(partials)}")
    tqdm.write(f"Errors:            {len(errors)}")
    if dry_run:
        tqdm.write(f"Needs normalization: {len(pending_normalization)}")
    else:
        tqdm.write(f"Fields normalized: {len(normalized_accounts)}")

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

    if normalized_accounts:
        tqdm.write("\nAccounts normalized before assignment:")
        for phone in normalized_accounts:
            tqdm.write(f"  - {phone}")

    if pending_normalization:
        tqdm.write("\nAccounts needing normalization (run without --dry-run to fix):")
        for phone in pending_normalization:
            tqdm.write(f"  - {phone}")


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
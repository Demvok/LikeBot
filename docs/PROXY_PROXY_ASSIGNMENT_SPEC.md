# Proxy Assignment Rework Specification

## Overview
- Goal: tie proxies directly to accounts, drop global load-balancer counters, and pick a random proxy from each accounts list on every connect.
- Maximum of five proxies per account; stored as proxy names referencing existing entries in the `proxies` collection.
- Accounts without assigned proxies must log a warning and obey `config.yaml`\'s `proxy.mode` (`strict` aborts, non-strict proceeds without proxy).

## Account Schema & Models
- Extend account models (`AccountBase`, `AccountCreate`, `AccountUpdate`, responses) with `assigned_proxies: List[str] = Field(default_factory=list, max_items=5)`.
- Validators:
  - Normalize names (trim, lowercase).
  - Ensure uniqueness and `len <= 5`.
- `main_logic/account.py` must load/save `assigned_proxies` and include it inside `to_dict()`/`from_keys()`.
- Account CRUD endpoints must accept/emit the list and validate referenced proxies exist before persisting.

## Database Layer
- Add `linked_accounts_count` (int, default 0) to each proxy document; create an index on it for least-linked queries.
- Implement helper methods in `main_logic/database.py`:
  1. `link_proxy_to_account(phone_number, proxy_name)`
     - Guard: proxy exists & active, account has <5 proxies.
     - `$addToSet` proxy name into account; only proceed if modified.
     - `$inc` proxy `linked_accounts_count` by +1 in same transaction or via retry loop to avoid races.
  2. `unlink_proxy_from_account(phone_number, proxy_name)`
     - `$pull` name from account; if removed, `$inc` proxy count by -1.
  3. `get_account_assigned_proxies(phone_number)` returning both raw names and proxy metadata (active flag, host, counts).
- Remove deprecated `connected_accounts` counters or keep them only for live-session tracking; all API/UI usage swaps to `linked_accounts_count`.

## Runtime Proxy Selection
- `ProxyMixin._get_proxy_config()` now:
  1. Read `self.account.assigned_proxies`.
  2. If empty: emit `self.logger.warning("Account has no assigned proxies; proxy.mode=%s", proxy_mode)` and follow config rule:
     - `strict`: raise `RuntimeError("Proxy required but none assigned")` so `ConnectionMixin.connect` aborts.
     - Otherwise return `(None, None)` to continue without proxy.
  3. If non-empty:
     - Use `random.choice` to pick a proxy name.
     - Fetch proxy via `db.get_proxy(name)`; ensure `active=True`. If inactive/missing, log warning, temporarily drop it for this attempt, and retry with remaining assigned proxies. If all invalid, fall back to step 2 handling.
     - Build Telethon proxy candidates via `build_proxy_candidates(proxy_data)` and proceed with connection.
- Remove `increment_proxy_usage`/`decrement_proxy_usage`; the new counters change only on link/unlink.
- Log which proxy name was chosen for traceability.

## API Surface (`main.py`)
- Account endpoints include `assigned_proxies` in responses and accept updates.
- Add endpoints:
  1. `GET /accounts/{phone}/proxies`  returns the list plus proxy metadata (host, active, linked count).
  2. `POST /accounts/{phone}/proxies/{proxy_name}`  validates and calls `link_proxy_to_account`.
  3. `DELETE /accounts/{phone}/proxies/{proxy_name}`  calls `unlink_proxy_from_account`.
- Existing proxy CRUD responses now expose `linked_accounts_count`.
- `/proxies/least-linked` (new) sorts by `linked_accounts_count` asc, supports filtering by `active_only` and `limit`.
- When deleting a proxy, block if `linked_accounts_count > 0`.

## Configuration Behavior
- Document `proxy.mode` in `config.yaml` and enforce it in `ConnectionMixin.connect` as described above.
- Optionally add `proxy.max_per_account` config (default 5) if you want to make the limit configurable.

## Logging & Documentation
- Update `docs/PROXY_CONFIGURATION.md` and README changelog to describe the new workflow, API endpoints, and failure modes.
- Logging expectations:
  - Info when proxies are linked/unlinked.
  - Warning when accounts have zero valid proxies or when strict mode blocks a connection.
  - Debug/Info when a proxy is selected for a session.

## Testing
- Add tests (e.g., `tests/test_proxy_assignment.py`):
  - Linking fails on >5 entries.
  - Linking/unlinking adjusts `linked_accounts_count` consistently even under concurrent calls.
  - Proxy selection chooses only from assigned list, and strict mode errors out when list empty/inactive.
  - New API endpoints enforce validation and return expected payloads.
  - `GET /proxies/least-linked` ordering verified.

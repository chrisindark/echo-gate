"""Mint, list, and revoke admin API keys.

This is the ONLY way to create an admin key. There is intentionally no HTTP
endpoint for it: admin keys grant cross-tenant Postgres access, so an endpoint
that could mint one would let the admin surface grant itself more admin.

Usage:
    PYTHON_ENV=local poetry run python -m app.scripts.create_admin_key create \\
        --name ops --scopes quota:read,quota:write [--expires-in-days 90]

    poetry run python -m app.scripts.create_admin_key list
    poetry run python -m app.scripts.create_admin_key revoke --id 3

The plaintext key is printed exactly once, on creation. Only its SHA-256 hash is
stored.
"""

import argparse
import sys
from datetime import UTC, datetime

from sqlalchemy.exc import ProgrammingError

from app.core.database import task_db_session
from app.modules.admin.admin_api_keys_service import (
    ADMIN_WILDCARD_SCOPE,
    KNOWN_SCOPES,
    AdminApiKeyService,
    InvalidScopeError,
)

SCOPE_HELP = (
    "Comma-separated scopes. "
    f"Valid: {', '.join(sorted(KNOWN_SCOPES))}, or {ADMIN_WILDCARD_SCOPE} for all. "
    "A ':write' scope implies the matching ':read'."
)


def _format_key_expiry(expires_at: datetime | None) -> str:
    if expires_at is None:
        return "never"
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at.isoformat()


def cmd_create(args: argparse.Namespace) -> int:
    service = AdminApiKeyService()
    try:
        record, plaintext = service.create_admin_key(
            name=args.name,
            scopes=args.scopes,
            expires_in_days=args.expires_in_days,
        )
    except InvalidScopeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print("Admin API key created.")
    print(f"  id       : {record.id}")
    print(f"  name     : {record.name}")
    print(f"  scopes   : {record.scopes}")
    print(f"  expires  : {_format_key_expiry(record.expires_at)}")
    print()
    print(f"  key      : {plaintext}")
    print()
    print("This is the only time the key is shown. Store it now.")
    print("Send it as the X-Admin-Key header to use the admin API.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    service = AdminApiKeyService()
    rows = service.list_admin_keys()
    if not rows:
        print("No admin API keys exist yet.")
        print(
            "Create one with:\n"
            "  python -m app.scripts.create_admin_key create --name ops "
            "--scopes quota:read,quota:write"
        )
        return 0

    print(f"{'ID':<5}{'PREFIX':<16}{'NAME':<20}{'SCOPES':<40}{'ACTIVE':<8}EXPIRES")
    for row in rows:
        print(
            f"{row.id:<5}{row.key_prefix:<16}{row.name:<20}"
            f"{row.scopes:<40}{'yes' if row.is_active else 'no':<8}"
            f"{_format_key_expiry(row.expires_at)}"
        )
    print("\nPlaintext keys are never recoverable; revoke and mint a new one instead.")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    service = AdminApiKeyService()
    if service.revoke_admin_key(args.id):
        print(f"Admin API key {args.id} revoked.")
        return 0
    print(f"error: no admin API key with id {args.id}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_admin_key",
        description="Mint, list, and revoke admin API keys.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a new admin API key")
    create.add_argument("--name", required=True, help="Label for the key holder")
    create.add_argument("--scopes", required=True, help=SCOPE_HELP)
    create.add_argument(
        "--expires-in-days",
        type=int,
        default=None,
        help="Expire the key after N days (default: never expires)",
    )
    create.set_defaults(func=cmd_create)

    list_cmd = sub.add_parser("list", help="List admin keys (metadata only)")
    list_cmd.set_defaults(func=cmd_list)

    revoke = sub.add_parser("revoke", help="Revoke an admin API key")
    revoke.add_argument("--id", type=int, required=True, help="Key ID to revoke")
    revoke.set_defaults(func=cmd_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Migrations create this table; running without them should fail with a clear
    # message rather than a bare UndefinedTable traceback. Match the specific
    # undefined-*relation* condition only, so a genuine bug (a missing column,
    # say) still surfaces its real traceback instead of being mislabelled as
    # "table missing".
    try:
        return args.func(args)
    except ProgrammingError as e:
        message = str(getattr(e, "orig", e))
        if "admin_api_keys" in message and "does not exist" in message:
            print(
                "error: the admin_api_keys table is missing. "
                "Run `poetry run alembic upgrade head` first.",
                file=sys.stderr,
            )
            return 1
        raise


if __name__ == "__main__":
    with task_db_session():
        raise SystemExit(main())

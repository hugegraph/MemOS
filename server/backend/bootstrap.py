"""Local account provisioning. Passwords never appear in command-line arguments."""

import argparse
import getpass
import json
import os
import secrets
from pathlib import Path

from .control import ControlStore
from .identity_store import IdentityStore
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(
        description="Initialize a local account; no public registration endpoint"
    )
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true")
    parser.add_argument(
        "--bootstrap", action="store_true", help="Only succeeds with an empty account store"
    )
    parser.add_argument(
        "--credential-file",
        type=Path,
        help="Generate a random password into a new private local file",
    )
    args = parser.parse_args()
    config = Settings.from_env()
    store = IdentityStore(ControlStore(config), config)
    if args.credential_file:
        # Exclusive create before provisioning: never overwrite a credential file.
        descriptor = os.open(args.credential_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            password = secrets.token_urlsafe(24)
            account = store.create_account(
                args.username,
                password,
                admin=args.admin or args.bootstrap,
                bootstrap=args.bootstrap,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                descriptor = None
                json.dump(
                    {"username": args.username, "password": password, "accountId": account["id"]},
                    output,
                )
            print("Account provisioned; credentials saved locally to", args.credential_file)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            args.credential_file.unlink(missing_ok=True)
            raise
    else:
        store.create_account(
            args.username,
            getpass.getpass("New password (12+ characters): "),
            admin=args.admin or args.bootstrap,
            bootstrap=args.bootstrap,
        )
        print("Account provisioned")


if __name__ == "__main__":
    main()

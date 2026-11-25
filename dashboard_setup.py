"""Print a salted password hash for dashboard configuration."""

import getpass
import secrets

from dashboard import password_hash


if __name__ == "__main__":
    password = getpass.getpass("Admin password: ")
    if not password or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords must be nonempty and match.")
    salt = secrets.token_hex(16)
    print(f"{salt}:{password_hash(password, salt)}")

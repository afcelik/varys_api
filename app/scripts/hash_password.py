"""Password hash generation script.

Run with: uv run python -m app.scripts.hash_password
Prompts for password (hidden input) and outputs Argon2 hash.
"""

import getpass
import sys

from app.core.security import hash_password


def main() -> None:
    """Generate Argon2 password hash from user input."""
    print("Varys Password Hash Generator")
    print("=" * 40)

    # Get password with hidden input
    password = getpass.getpass("Enter password: ")
    if not password:
        print("Error: Password cannot be empty", file=sys.stderr)
        sys.exit(1)

    # Confirm password
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: Passwords do not match", file=sys.stderr)
        sys.exit(1)

    # Generate hash
    password_hash = hash_password(password)

    print("\n" + "=" * 40)
    print("Add this to your .env file as PASSWORD_HASH:")
    print()
    print(password_hash)
    print()
    print("=" * 40)


if __name__ == "__main__":
    main()
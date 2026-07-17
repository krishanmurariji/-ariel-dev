"""
Encrypt / decrypt a file with a password, for securely sharing the database
(or any sensitive file) between machines.

Uses AES-128-CBC + HMAC (Fernet) with a PBKDF2-derived key (480k iterations).
Requires only the `cryptography` package, which is already in requirements.txt.

Usage:
    python scripts/secure_share.py encrypt TestDB.sqlite3
        -> writes TestDB.sqlite3.enc  (share this file)

    python scripts/secure_share.py decrypt TestDB.sqlite3.enc
        -> writes TestDB.sqlite3      (run on the receiving machine)

The password is asked interactively (not shown, not stored anywhere).
Share the password over a DIFFERENT channel than the file itself
(e.g. file via drive link, password via phone call).
"""

import base64
import getpass
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"ARIELHR1"  # file header: identifies format/version
ITERATIONS = 480_000


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt(path: str) -> None:
    out_path = path + ".enc"
    password = getpass.getpass("Choose a password: ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Repeat password:  "):
        sys.exit("Passwords do not match.")

    salt = os.urandom(16)
    token = Fernet(derive_key(password, salt)).encrypt(open(path, "rb").read())
    with open(out_path, "wb") as f:
        f.write(MAGIC + salt + token)
    print(f"Encrypted -> {out_path}")
    print("Share the .enc file freely; share the password separately.")


def decrypt(path: str) -> None:
    out_path = path[:-4] if path.endswith(".enc") else path + ".dec"
    if os.path.exists(out_path):
        sys.exit(f"Refusing to overwrite existing {out_path} — move it first.")

    blob = open(path, "rb").read()
    if not blob.startswith(MAGIC):
        sys.exit("Not a file produced by this tool (bad header).")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]

    password = getpass.getpass("Password: ")
    try:
        data = Fernet(derive_key(password, salt)).decrypt(token)
    except InvalidToken:
        sys.exit("Wrong password or corrupted file.")
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"Decrypted -> {out_path}")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("encrypt", "decrypt"):
        sys.exit(__doc__)
    action, path = sys.argv[1], sys.argv[2]
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")
    encrypt(path) if action == "encrypt" else decrypt(path)


if __name__ == "__main__":
    main()

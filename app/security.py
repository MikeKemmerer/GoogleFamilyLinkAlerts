"""Password hashing for this app's own optional login (unrelated to the
Google account credentials, which are handled entirely by the separate
familylink-auth container via browser cookies -- see app/familylink/).

Uses the `bcrypt` package directly rather than passlib: passlib's bcrypt
backend has had compatibility breakage with newer bcrypt releases (it reads
a `__about__.__version__` attribute bcrypt >=4.1 removed), so calling
bcrypt's hashpw/checkpw directly avoids depending on passlib ever fixing
that.
"""
from __future__ import annotations

import bcrypt

# bcrypt truncates/rejects passwords over 72 bytes -- reject up front with a
# clear error instead of silently hashing only the first 72 bytes.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (shouldn't happen for hashes we generated ourselves).
        return False

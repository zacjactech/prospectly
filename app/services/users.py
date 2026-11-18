"""Account credentials: scrypt hashing, password policy, reset tokens.

Owner decision 2026-11-18 (full accounts + roles). SECURITY_AND_PRIVACY §3:
hashing is stdlib ``hashlib.scrypt`` (n=2**14, r=8, p=1, 16-byte salt,
32-byte key) — zero new dependencies per the dependency policy. Unknown-user
logins verify against a module-level dummy hash so a missing username costs
the same wall time as a wrong password (no user enumeration by timing).

Storage format: ``scrypt$<n>$<r>$<p>$<b64salt>$<b64hash>``.

Services flush; callers commit (project convention).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

#: Minimum password length (documented in the UI copy).
PASSWORD_MIN_LENGTH = 8

#: Password-reset token lifetime (local-first: the link is shown on screen).
RESET_TOKEN_TTL = dt.timedelta(minutes=30)

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")

#: Lazy dummy hash for constant-cost unknown-user logins.
_dummy_hash: str | None = None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def hash_password(password: str) -> str:
    """Hash a password with scrypt; returns the self-describing store string."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time-ish password check; never raises on garbage input."""
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_unb64(digest_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, _unb64(digest_b64))


def _verify_dummy(password: str) -> None:
    """Burn the same CPU as a real verify when the username does not exist."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)


def validate_username(value: str) -> str:
    """Normalize and validate a username; raises ValueError with UI copy."""
    username = (value or "").strip().lower()
    if not _USERNAME_RE.match(username):
        raise ValueError(
            "Username must be 3–40 characters: lowercase letters, digits, dots, dashes or underscores."
        )
    return username


def validate_password(password: str, *, username: str | None = None) -> str:
    """Password policy: length first, then no-username, no-whitespace-only."""
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if username and password.strip().lower() == username.lower():
        raise ValueError("Password must not be the username.")
    if not password.strip():
        raise ValueError("Password must not be blank.")
    return password


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username.strip().lower()))


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Return the user when the credentials match (disabled checked by caller)."""
    user = get_user_by_username(db, username)
    if user is None:
        _verify_dummy(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_user(db: Session, *, username: str, password: str, role: str = "member") -> User:
    """Create an account (caller validates the role and commits)."""
    uname = validate_username(username)
    validate_password(password, username=uname)
    if get_user_by_username(db, uname) is not None:
        raise ValueError("That username is already taken.")
    user = User(username=uname, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, user: User, password: str) -> None:
    """Set a new password (caller revokes sessions and commits)."""
    validate_password(password, username=user.username)
    user.password_hash = hash_password(password)
    user.reset_token_hash = None
    user.reset_expires_at = None
    db.flush()


def issue_reset_token(db: Session, user: User) -> str:
    """Create a single-use reset token; returns the *plain* token for the link."""
    token = secrets.token_urlsafe(32)
    user.reset_token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    user.reset_expires_at = dt.datetime.now(dt.UTC) + RESET_TOKEN_TTL
    db.flush()
    return token


def consume_reset_token(db: Session, token: str, new_password: str) -> User | None:
    """Validate + consume a reset token; sets the password on success."""
    if not token:
        return None
    digest = hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
    user = db.scalar(select(User).where(User.reset_token_hash == digest))
    if user is None or user.reset_expires_at is None:
        return None
    expires = user.reset_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.UTC)
    if expires < dt.datetime.now(dt.UTC):
        return None
    set_password(db, user, new_password)  # also clears the token fields
    db.flush()
    return user

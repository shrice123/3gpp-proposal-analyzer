from __future__ import annotations

from cryptography.fernet import Fernet

from .config import settings


def _master_fernet() -> Fernet:
    if not settings.key_path.exists():
        settings.key_path.write_bytes(Fernet.generate_key())
        try:
            settings.key_path.chmod(0o600)
        except OSError:
            pass
    return Fernet(settings.key_path.read_bytes().strip())


def encrypt_secret(value: str) -> str:
    return _master_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _master_fernet().decrypt(value.encode("ascii")).decode("utf-8")



from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import CONFIG_DIR, SECRET_NAME_HINTS, VAULT_KEY_PATH, ensure_dirs

_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|credential)\s*[=:]\s*([^\s,;]+)"
)
_LONG_TOKEN_RE = re.compile(r"\b([A-Za-z0-9_\-]{24,})\b")


def is_secret_name(name: str) -> bool:
    upper = (name or "").upper()
    if not upper:
        return False
    return any(hint in upper for hint in SECRET_NAME_HINTS)


def mask_secret(value: str | None, visible: int = 4) -> str:
    if value is None or value == "":
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def redact_text(text: str, extra_values: list[str] | None = None) -> str:
    if not text:
        return text
    out = _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}=***REDACTED***", text)
    extras = extra_values or []
    for val in extras:
        if val and len(val) >= 4 and val in out:
            out = out.replace(val, "***REDACTED***")
    return out


def _protect_file(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(path.parent, stat.S_IRWXU)
    except OSError:
        pass


class SecretVault:
    def __init__(self, key_path: Path | None = None):
        ensure_dirs()
        self.key_path = key_path or VAULT_KEY_PATH
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        tmp = self.key_path.with_suffix(".tmp")
        tmp.write_bytes(key)
        tmp.replace(self.key_path)
        _protect_file(self.key_path)
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt secret") from exc


def generate_token(n: int = 32) -> str:
    return secrets.token_hex(n)


def sanitize_bot_id_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())[:80]
    return cleaned or "bot"


def is_safe_relative_path(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if p.is_absolute():
        return False
    return ".." not in p.parts

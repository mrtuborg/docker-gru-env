"""
Credential Vault — AES-256-GCM encryption for secrets stored in the config DB.

Vault key lives in ~/.gru/vault.key (256-bit random, base64-encoded).
Each credential is encrypted independently: nonce||ciphertext||tag, all b64.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import aiosqlite
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_db_path

logger = logging.getLogger(__name__)

_vault_key: bytes | None = None


def _key_path() -> Path:
    data_dir = Path(os.environ.get("GRU_DATA_DIR", Path.home() / ".gru"))
    return data_dir / "vault.key"


def load_vault_key() -> bytes:
    """Load or generate the 256-bit vault key. Called once at startup."""
    global _vault_key
    if _vault_key is not None:
        return _vault_key

    kp = _key_path()
    if kp.exists():
        raw = kp.read_bytes().strip()
        _vault_key = base64.b64decode(raw)
        logger.info("Vault key loaded from %s", kp)
    else:
        _vault_key = AESGCM.generate_key(bit_length=256)
        kp.parent.mkdir(parents=True, exist_ok=True)
        kp.write_bytes(base64.b64encode(_vault_key))
        kp.chmod(0o600)
        logger.info("New vault key generated at %s", kp)

    return _vault_key


def _encrypt(plaintext: str) -> bytes:
    key = load_vault_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    # Store as nonce||ciphertext, base64-encoded
    return base64.b64encode(nonce + ct)


def _decrypt(blob: bytes) -> str:
    key = load_vault_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()


# ── Async DB operations ───────────────────────────────────────────────────────

async def store_secret(plugin_id: str, key: str, value: str, expires_at: str | None = None) -> None:
    """Encrypt and store a secret. Overwrites any existing value."""
    encrypted = _encrypt(value)
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO credentials(plugin_id, key, value, expires_at, updated_at)
               VALUES(?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(plugin_id,key) DO UPDATE SET
                 value=excluded.value,
                 expires_at=excluded.expires_at,
                 updated_at=excluded.updated_at""",
            (plugin_id, key, encrypted, expires_at),
        )
        await db.commit()


async def load_secret(plugin_id: str, key: str) -> str | None:
    """Load and decrypt a secret. Returns None if not found."""
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT value FROM credentials WHERE plugin_id=? AND key=?", (plugin_id, key)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            try:
                return _decrypt(row[0])
            except Exception:
                logger.error("Failed to decrypt secret %s/%s — vault key may have changed", plugin_id, key)
                return None


async def delete_secret(plugin_id: str, key: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM credentials WHERE plugin_id=? AND key=?", (plugin_id, key))
        await db.commit()


async def list_secret_keys(plugin_id: str) -> list[dict]:
    """Return metadata (key, expires_at, updated_at) without values."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT key, expires_at, updated_at FROM credentials WHERE plugin_id=?", (plugin_id,)
        ) as cur:
            return [dict(r) async for r in cur]

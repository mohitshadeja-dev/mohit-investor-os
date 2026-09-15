from __future__ import annotations
import os, sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

DB = Path(os.getenv('APP_DB_PATH', './mohit_os.db'))
KEYFILE = Path(os.getenv('APP_KEY_PATH', './.mohit_os_fernet.key'))

def _conn():
    c = sqlite3.connect(DB)
    c.execute('CREATE TABLE IF NOT EXISTS secrets (k TEXT PRIMARY KEY, v BLOB NOT NULL)')
    return c

def _get_key() -> bytes:
    env = os.getenv('APP_ENCRYPTION_KEY','').strip()
    if env:
        return env.encode()
    if KEYFILE.exists():
        return KEYFILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEYFILE.write_bytes(key)
    try:
        KEYFILE.chmod(0o600)
    except Exception:
        pass
    return key

def _fernet():
    return Fernet(_get_key())

def set_secret(k: str, value: str):
    token = _fernet().encrypt(value.encode())
    with _conn() as c:
        c.execute('INSERT INTO secrets(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(k,token))

def get_secret(k: str):
    with _conn() as c:
        row = c.execute('SELECT v FROM secrets WHERE k=?',(k,)).fetchone()
    if not row:
        return None
    try:
        return _fernet().decrypt(row[0]).decode()
    except Exception:
        return None

def delete_secret(k: str):
    with _conn() as c:
        c.execute('DELETE FROM secrets WHERE k=?',(k,))

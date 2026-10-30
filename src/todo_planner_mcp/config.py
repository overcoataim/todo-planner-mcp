"""Configuration loaded from environment (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


@dataclass(frozen=True)
class Settings:
    client_id: str
    authority: str          # 'common' | 'consumers' | 'organizations' | tenant id
    scopes: list[str]
    token_cache: Path
    client_secret: str = ""  # set -> confidential client (auth-code flow); empty -> public (device code)
    redirect_uri: str = "http://localhost:8400/callback"
    optin_group: str = ""    # Entra group of signed-up users (name or id); see optin.py

    @property
    def is_confidential(self) -> bool:
        return bool(self.client_secret)

    @property
    def authority_url(self) -> str:
        a = self.authority.strip()
        # Accept a bare tenant id/domain or one of the named authorities.
        return f"https://login.microsoftonline.com/{a}"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency); does not override real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def load_settings() -> Settings:
    # Load .env from project root (two levels up from this file) if present.
    root = Path(__file__).resolve().parents[2]
    _load_dotenv(root / ".env")

    client_id = os.environ.get("TPM_CLIENT_ID", "").strip()
    if not client_id or client_id == "your-app-client-id-here":
        raise RuntimeError(
            "TPM_CLIENT_ID is not set. Copy .env.example to .env and set your "
            "Entra app registration client id."
        )
    # TPM_TENANT_ID (if set) pins a specific tenant and overrides TPM_AUTHORITY.
    authority = (os.environ.get("TPM_TENANT_ID", "").strip()
                 or os.environ.get("TPM_AUTHORITY", "common").strip())
    scopes = os.environ.get("TPM_SCOPES", "User.Read Tasks.ReadWrite").split()
    if "User.Read" not in scopes:  # GET /me (whoami) needs it
        scopes.insert(0, "User.Read")
    cache = _expand(os.environ.get("TPM_TOKEN_CACHE", "~/.todo_planner_mcp/token_cache.bin"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("TPM_CLIENT_SECRET", "").strip()
    redirect = os.environ.get("TPM_REDIRECT_URI", "").strip() or "http://localhost:8400/callback"
    optin = os.environ.get("TPM_OPTIN_GROUP", "").strip()
    return Settings(client_id=client_id, authority=authority, scopes=scopes, token_cache=cache,
                    client_secret=secret, redirect_uri=redirect, optin_group=optin)

"""GitHub App manifest-flow helpers: the one-time "create the App" dance.

Flow (https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest):
1. GET /api/github/app/new  -> serves an auto-submitting HTML form that
   POSTs our manifest (app/core/github_app.py) to github.com.
2. A human (Manda) reviews + confirms on GitHub's own UI.
3. GitHub redirects to our redirect_url with a one-time `code`.
4. GET /api/github/manifest-callback exchanges that code for the App's
   real credentials (id, pem, webhook_secret, client secret) via this
   module, and shows them ONCE so she can copy them into env vars. Nothing
   here persists those values to disk/db/logs.

CSRF protection: a `state` token is issued when serving the form and must
come back unchanged on the callback. The token is self-verifying:
`<expiry>.<nonce>.<hmac>`, so the callback can be served by a different
instance than the form (serverless hosts such as Vercel do exactly that).
The HMAC key is MANIFEST_STATE_SECRET, falling back to
OMNI_AGENT_INTERNAL_TOKEN; with neither set, a per-process key is used,
which only works when one process serves both requests. Each token is also
single-use within a process. GitHub's manifest `code` is itself single-use,
so a cross-instance replay of `state` cannot mint a second App.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Set

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
_STATE_TTL_SECONDS = 15 * 60
_PROCESS_KEY = secrets.token_bytes(32)
_used_states: Set[str] = set()


class ManifestExchangeError(RuntimeError):
    """Raised when GitHub rejects the manifest-code exchange."""


def _state_key() -> bytes:
    secret = os.environ.get("MANIFEST_STATE_SECRET") or os.environ.get("OMNI_AGENT_INTERNAL_TOKEN")
    if secret:
        return secret.encode("utf-8")
    logger.warning("MANIFEST_STATE_SECRET is not set; manifest state only verifies on this instance")
    return _PROCESS_KEY


def _sign(payload: str) -> str:
    digest = hmac.new(_state_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_state() -> str:
    payload = f"{int(time.time()) + _STATE_TTL_SECONDS}.{secrets.token_urlsafe(16)}"
    return f"{payload}.{_sign(payload)}"


def consume_state(token: Optional[str]) -> bool:
    """True iff the token carries a valid signature, has not expired, and
    has not already been used in this process."""
    if not token or token.count(".") != 2:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        expires = int(payload.split(".", 1)[0])
    except ValueError:
        return False
    if expires < time.time() or token in _used_states:
        return False
    _used_states.add(token)
    return True


async def exchange_manifest_code(code: str) -> Dict[str, Any]:
    """POST /app-manifests/{code}/conversions — trades the one-time code
    from the manifest flow for the App's real id/pem/webhook_secret/client
    credentials. Must happen within an hour of the code being issued.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
    if resp.status_code >= 400:
        raise ManifestExchangeError(
            f"GitHub rejected the manifest code exchange "
            f"({resp.status_code}): {resp.text}"
        )
    return resp.json()

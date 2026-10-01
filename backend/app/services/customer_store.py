"""Lightweight persistence for paid-customer events.

Design goal: the billing flow must work even before Mongo is provisioned
(see `backend/server.py`, which now makes `MONGO_URL` optional). So this
store writes to Mongo when a database handle is available, and always
also appends to a local JSONL file as a zero-dependency fallback / audit
trail. Never deletes or overwrites — append-only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core import storage
from app.core.storage import append_jsonl

logger = logging.getLogger(__name__)

DATA_DIR: Optional[Path] = None  # tests override; None = app.core.storage.data_dir()
CUSTOMERS_FILE = "customers.jsonl"


def _append_jsonl(record: Dict[str, Any]) -> None:
    append_jsonl((DATA_DIR or storage.data_dir()) / CUSTOMERS_FILE, record)


async def record_checkout_started(
    *, plan: str, customer_email: Optional[str], session_id: str, db=None
) -> None:
    record = {
        "event": "checkout_started",
        "plan": plan,
        "customer_email": customer_email,
        "stripe_session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl(record)
    if db is not None:
        try:
            await db.billing_events.insert_one(dict(record))
        except Exception:  # pragma: no cover - best-effort secondary store
            logger.exception("Failed to write checkout_started to Mongo")


async def record_checkout_completed(
    *,
    plan: str,
    customer_email: Optional[str],
    session_id: str,
    subscription_id: Optional[str],
    db=None,
) -> None:
    record = {
        "event": "checkout_completed",
        "plan": plan,
        "customer_email": customer_email,
        "stripe_session_id": session_id,
        "stripe_subscription_id": subscription_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl(record)
    if db is not None:
        try:
            await db.billing_events.insert_one(dict(record))
            if customer_email:
                await db.customers.update_one(
                    {"email": customer_email},
                    {"$set": {"plan": plan, "stripe_subscription_id": subscription_id}},
                    upsert=True,
                )
        except Exception:  # pragma: no cover - best-effort secondary store
            logger.exception("Failed to write checkout_completed to Mongo")

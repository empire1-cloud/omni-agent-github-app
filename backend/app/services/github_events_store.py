"""Persistence for GitHub App installation + Marketplace purchase events.

Same pattern as app/services/customer_store.py (the Stripe-side store):
append-only JSONL always, Mongo also when MONGO_URL is configured. Two
JSONL files so an install audit and a billing audit don't interleave.
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
INSTALLATIONS_FILE = "github_installations.jsonl"
MARKETPLACE_FILE = "github_marketplace_events.jsonl"

# Marketplace webhook actions that mean "this account should be paying /
# still-paying customer at `plan`" vs "cancelled". See
# https://docs.github.com/en/webhooks/webhook-events-and-payloads#marketplace_purchase
ACTIVE_ACTIONS = {"purchased", "changed", "pending_change"}
INACTIVE_ACTIONS = {"cancelled"}


def _append_jsonl(filename: str, record: Dict[str, Any]) -> None:
    if not append_jsonl((DATA_DIR or storage.data_dir()) / filename, record):
        return
    if not storage.event_storage_durable():
        logger.warning("%s recorded to %s, which this host does not keep; set MONGO_URL "
                       "or OMNI_AGENT_DATA_DIR", record.get("event"), storage.data_dir())


async def record_installation_event(
    *, action: str, installation_id: int, account_login: Optional[str],
    account_type: Optional[str], repository_selection: Optional[str], db=None,
) -> None:
    record = {
        "event": "installation",
        "action": action,  # created | deleted | suspend | unsuspend | new_permissions_accepted
        "installation_id": installation_id,
        "account_login": account_login,
        "account_type": account_type,
        "repository_selection": repository_selection,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl(INSTALLATIONS_FILE, record)
    if db is not None:
        try:
            await db.github_installations.update_one(
                {"installation_id": installation_id},
                {"$set": record},
                upsert=True,
            )
        except Exception:  # pragma: no cover - best-effort secondary store
            logger.exception("Failed to upsert installation event to Mongo")


async def record_marketplace_event(
    *,
    action: str,
    account_id: Optional[int],
    account_login: Optional[str],
    account_type: Optional[str],
    plan_name: Optional[str],
    plan_id: Optional[int],
    unit_count: Optional[int],
    billing_cycle: Optional[str],
    on_free_trial: bool,
    effective_date: Optional[str],
    db=None,
) -> None:
    """Record one marketplace_purchase webhook delivery, and — when Mongo
    is available — upsert the account's *current* entitlement so the rest
    of the app has a cheap "is this account paying, and for what plan"
    lookup without replaying the whole event log.
    """
    status = "active" if action in ACTIVE_ACTIONS else (
        "cancelled" if action in INACTIVE_ACTIONS else "unknown"
    )
    record = {
        "event": "marketplace_purchase",
        "action": action,
        "status": status,
        "account_id": account_id,
        "account_login": account_login,
        "account_type": account_type,
        "plan_name": plan_name,
        "plan_id": plan_id,
        "unit_count": unit_count,
        "billing_cycle": billing_cycle,
        "on_free_trial": on_free_trial,
        "effective_date": effective_date,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl(MARKETPLACE_FILE, record)
    if db is not None:
        try:
            await db.marketplace_events.insert_one(dict(record))
            if account_id is not None:
                await db.marketplace_accounts.update_one(
                    {"account_id": account_id},
                    {"$set": {
                        "account_login": account_login,
                        "account_type": account_type,
                        "plan_name": plan_name,
                        "plan_id": plan_id,
                        "unit_count": unit_count,
                        "billing_cycle": billing_cycle,
                        "on_free_trial": on_free_trial,
                        "status": status,
                        "updated_at": record["timestamp"],
                    }},
                    upsert=True,
                )
        except Exception:  # pragma: no cover - best-effort secondary store
            logger.exception("Failed to write marketplace_purchase event to Mongo")

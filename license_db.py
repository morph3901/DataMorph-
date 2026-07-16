"""
license_db.py
==============
Shared license key store, backed by Supabase (Postgres) instead of a
local SQLite file.

Why: app.py (Streamlit Cloud) and webhook_server.py (Render/Heroku/etc.)
run as two separate processes on two separate machines with two separate
filesystems. A local SQLite file written by one process is invisible to
the other. Supabase gives both processes a single shared, persistent
database over the network, so a key written by the webhook the instant
someone pays is immediately visible to the Streamlit app when they type
it in.

Function names and signatures are unchanged from the SQLite version, so
app.py and webhook_server.py don't need any changes.

------------------------------------------------------------------------
SETUP
------------------------------------------------------------------------
1. Create a free project at https://supabase.com
2. In the SQL Editor, run:

    CREATE TABLE license_keys (
        license_key TEXT PRIMARY KEY,
        email TEXT,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL
    );

3. Go to Project Settings -> API and copy:
     - Project URL          -> set as env var SUPABASE_URL
     - anon / service key   -> set as env var SUPABASE_KEY

   For the webhook server (which writes data), a "service_role" key is
   recommended over the "anon" key, since anon keys are typically
   restricted by Row Level Security policies meant for client-side use.
   If you enable Row Level Security on this table, either use the
   service_role key for webhook_server.py, or add policies that allow
   the operations this module performs.

4. pip install supabase
------------------------------------------------------------------------
"""

import os
import secrets
import string
from datetime import datetime, timezone

from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_supabase: Client | None = None


def _client() -> Client:
    """Lazily create (and cache) the Supabase client."""
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY environment variables must be set."
            )
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def generate_license_key() -> str:
    """Generate a unique, human-typeable key like MORPH-4F9K-7QXP."""
    alphabet = string.ascii_uppercase + string.digits
    part = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
    return f"MORPH-{part()}-{part()}"


def create_license_key(
    email: str,
    stripe_customer_id: str,
    stripe_subscription_id: str = "",
    key_type: str = "subscription",
) -> str:
    """
    Generate a fresh key and store it in Supabase, tied to the Stripe
    customer and subscription/payment that paid for it. Returns the new key.

    key_type: 'one_time' for single-use, 'subscription' for monthly access.
    Retries once on the (rare) chance of a primary-key collision.
    """
    for _ in range(2):
        key = generate_license_key()
        try:
            _client().table("license_keys").insert(
                {
                    "license_key": key,
                    "email": email,
                    "stripe_customer_id": stripe_customer_id,
                    "stripe_subscription_id": stripe_subscription_id,
                    "status": "active",
                    "type": key_type,
                    "usage_count": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
            return key
        except Exception as e:
            if _ == 0:
                continue
            raise RuntimeError(f"Failed to create license key in Supabase: {e}") from e
    raise RuntimeError("Failed to create a unique license key after retrying.")


def is_key_valid(license_key: str) -> bool:
    """Check whether a submitted key exists, is active, and has uses remaining."""
    if not license_key:
        return False

    try:
        response = (
            _client()
            .table("license_keys")
            .select("status,type,usage_count")
            .eq("license_key", license_key.strip())
            .limit(1)
            .execute()
        )
    except Exception:
        return False

    rows = response.data or []
    if not rows or rows[0]["status"] != "active":
        return False

    key_type = rows[0].get("type", "subscription")
    usage = rows[0].get("usage_count", 0)

    if key_type == "one_time" and usage >= 1:
        return False

    return True


def get_key_info(license_key: str) -> dict | None:
    """Return full info about a key, or None if invalid."""
    if not license_key:
        return None

    try:
        response = (
            _client()
            .table("license_keys")
            .select("status,type,usage_count")
            .eq("license_key", license_key.strip())
            .limit(1)
            .execute()
        )
    except Exception:
        return None

    rows = response.data or []
    return rows[0] if rows else None


def mark_key_used(license_key: str):
    """Increment usage_count for a key (used after one-time download)."""
    if not license_key:
        return
    try:
        row = (
            _client()
            .table("license_keys")
            .select("usage_count")
            .eq("license_key", license_key.strip())
            .limit(1)
            .execute()
        )
        current = (row.data or [{}])[0].get("usage_count", 0)
        _client().table("license_keys").update(
            {"usage_count": current + 1}
        ).eq("license_key", license_key.strip()).execute()
    except Exception:
        pass


def revoke_keys_for_subscription(stripe_subscription_id: str):
    """
    Called when a subscription is cancelled/payment fails, so keys tied
    to that subscription stop working immediately.
    """
    if not stripe_subscription_id:
        return
    _client().table("license_keys").update({"status": "revoked"}).eq(
        "stripe_subscription_id", stripe_subscription_id
    ).execute()


def reactivate_keys_for_subscription(stripe_subscription_id: str):
    """Called if a subscription is renewed/reactivated after being revoked."""
    if not stripe_subscription_id:
        return
    _client().table("license_keys").update({"status": "active"}).eq(
        "stripe_subscription_id", stripe_subscription_id
    ).execute()


# --------------------------------------------------------------------------
# NOTES
# --------------------------------------------------------------------------
# - Row Level Security: Supabase enables RLS by default on new tables in
#   some project configurations. If create_license_key / is_key_valid
#   start failing with permission errors, either disable RLS on
#   license_keys for now, or add explicit policies -- and prefer the
#   service_role key for webhook_server.py either way, since it bypasses
#   RLS by design and this table is never queried directly from a browser.
# - Both app.py and webhook_server.py need SUPABASE_URL and SUPABASE_KEY
#   set in their respective environments (Streamlit Cloud secrets, and
#   Render/Heroku environment variables).
# - This module intentionally fails closed in is_key_valid(): if Supabase
#   is temporarily unreachable, users see "invalid key" rather than the
#   app crashing or silently unlocking downloads.

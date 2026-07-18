"""
DataMorph Admin Dashboard
=========================
Streamlit dashboard for monitoring Stripe payments, subscriptions,
license keys (Supabase), and app traffic.

Deploy as a SEPARATE Streamlit Cloud app pointing to this file.
Set the same secrets as the main app (STRIPE_API_KEY, SUPABASE_URL, SUPABASE_KEY).
"""

import os
from datetime import datetime, timezone, timedelta

import streamlit as st
import pandas as pd
import stripe
from supabase import create_client

# ── Config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="DataMorph Dashboard", page_icon="📊", layout="wide")

# ── Secrets / Env ───────────────────────────────────────────────────────
def _get(key: str) -> str:
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ.get(key, "")


STRIPE_KEY = _get("STRIPE_API_KEY")
SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_KEY = _get("SUPABASE_KEY")

stripe.api_key = STRIPE_KEY
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# ── Helpers ─────────────────────────────────────────────────────────────
def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    diff = now - dt
    if diff.days > 30:
        return f"{diff.days // 30}mo ago"
    if diff.days > 0:
        return f"{diff.days}d ago"
    hours = diff.seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = diff.seconds // 60
    return f"{minutes}m ago" if minutes > 0 else "just now"


# ── Stripe Data ─────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def fetch_stripe_data():
    data = {}
    try:
        # Balance
        bal = stripe.Balance.retrieve()
        data["available"] = bal.available[0].amount if bal.available else 0
        data["pending"] = bal.pending[0].amount if bal.pending else 0

        # Payment Intents (last 100)
        pi = stripe.PaymentIntent.list(limit=100)
        data["payments"] = pi.data

        # Subscriptions
        subs = stripe.Subscription.list(limit=100, status="all")
        data["subscriptions"] = subs.data

        # Customers
        custs = stripe.Customer.list(limit=100)
        data["customers"] = custs.data

        # Products
        prods = stripe.Product.list(limit=20)
        data["products"] = prods.data

        # Prices
        prices = stripe.Price.list(limit=20)
        data["prices"] = prices.data

    except Exception as e:
        data["error"] = str(e)
    return data


# ── Supabase Data ───────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def fetch_supabase_data():
    if not supabase:
        return {"error": "Supabase not configured"}
    data = {}
    try:
        resp = supabase.table("license_keys").select("*").execute()
        data["keys"] = resp.data or []
    except Exception as e:
        data["error"] = str(e)
    return data


# ── Main ────────────────────────────────────────────────────────────────
def main():
    st.title("📊 DataMorph Dashboard")
    st.caption("Real-time overview of revenue, subscriptions, and license keys.")

    if not STRIPE_KEY:
        st.error("STRIPE_API_KEY not set. Add it to your Streamlit secrets.")
        st.stop()
    if not SUPABASE_URL:
        st.error("SUPABASE_URL not set. Add it to your Streamlit secrets.")
        st.stop()

    stripe_data = fetch_stripe_data()
    supa_data = fetch_supabase_data()

    if "error" in stripe_data:
        st.error(f"Stripe error: {stripe_data['error']}")
    if "error" in supa_data:
        st.error(f"Supabase error: {supa_data['error']}")

    # ── KPI Cards ───────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    payments = stripe_data.get("payments", [])
    subs = stripe_data.get("subscriptions", [])
    keys = supa_data.get("keys", [])

    # Revenue calculations
    succeeded_payments = [p for p in payments if p.status == "succeeded"]
    total_revenue = sum(p.amount for p in succeeded_payments)
    month_revenue = sum(
        p.amount for p in succeeded_payments
        if p.created and datetime.fromtimestamp(p.created, tz=timezone.utc) >= month_start
    )
    today_revenue = sum(
        p.amount for p in succeeded_payments
        if p.created and datetime.fromtimestamp(p.created, tz=timezone.utc) >= today_start
    )

    # Subscription calculations
    active_subs = [s for s in subs if s.status == "active"]
    canceled_subs = [s for s in subs if s.status in ("canceled", "unpaid")]
    mrr = sum(
        s.get("items", {}).get("data", [{}])[0].get("price", {}).get("unit_amount", 0)
        for s in active_subs
    ) if active_subs else 0

    # License key calculations
    total_keys = len(keys)
    active_keys = len([k for k in keys if k.get("status") == "active"])
    used_keys = len([k for k in keys if k.get("usage_count", 0) > 0])
    one_time_keys = len([k for k in keys if k.get("type") == "one_time"])
    sub_keys = len([k for k in keys if k.get("type") == "subscription"])

    # ── Display KPIs ────────────────────────────────────────────────
    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Total Revenue", money(total_revenue))
    c2.metric("📅 This Month", money(month_revenue))
    c3.metric("📆 Today", money(today_revenue))
    c4.metric("🔄 Active Subs", len(active_subs))
    c5.metric("📈 MRR", money(mrr))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("🔑 Total Keys", total_keys)
    c7.metric("✅ Active Keys", active_keys)
    c8.metric("📥 Used (One-Time)", used_keys)
    c9.metric("🎫 One-Time Keys", one_time_keys)
    c10.metric("🔁 Subscription Keys", sub_keys)

    # ── Revenue Chart ───────────────────────────────────────────────
    st.divider()
    st.subheader("Revenue Over Time")

    if succeeded_payments:
        rev_df = pd.DataFrame([
            {
                "date": datetime.fromtimestamp(p.created, tz=timezone.utc).date(),
                "amount": p.amount / 100,
            }
            for p in succeeded_payments
            if p.created
        ])
        rev_df = rev_df.groupby("date", as_index=False)["amount"].sum()
        rev_df = rev_df.sort_values("date")
        st.bar_chart(rev_df, x="date", y="amount", height=300)
    else:
        st.info("No successful payments yet.")

    # ── Tabs: Transactions / Subscriptions / Keys ───────────────────
    st.divider()
    tab1, tab2, tab3 = st.tabs(["💳 Transactions", "🔄 Subscriptions", "🔑 License Keys"])

    with tab1:
        st.subheader("Recent Transactions")
        if succeeded_payments:
            tx_data = []
            for p in succeeded_payments[:50]:
                cust_email = ""
                if p.customer:
                    try:
                        cust = stripe.Customer.retrieve(p.customer)
                        cust_email = cust.email or ""
                    except Exception:
                        cust_email = p.customer
                tx_data.append({
                    "Date": datetime.fromtimestamp(p.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "Amount": money(p.amount),
                    "Status": p.status,
                    "Email": cust_email,
                    "Payment ID": p.id,
                })
            st.dataframe(pd.DataFrame(tx_data), use_container_width=True, hide_index=True)
        else:
            st.info("No transactions yet.")

    with tab2:
        st.subheader("All Subscriptions")
        if subs:
            sub_data = []
            for s in subs:
                cust_email = ""
                if s.customer:
                    try:
                        cust = stripe.Customer.retrieve(s.customer)
                        cust_email = cust.email or ""
                    except Exception:
                        cust_email = s.customer
                price = 0
                try:
                    price = s.get("items", {}).get("data", [{}])[0].get("price", {}).get("unit_amount", 0)
                except Exception:
                    pass
                sub_data.append({
                    "Status": s.status,
                    "Email": cust_email,
                    "Price": money(price) + "/mo",
                    "Created": datetime.fromtimestamp(s.created, tz=timezone.utc).strftime("%Y-%m-%d") if s.created else "",
                    "Subscription ID": s.id,
                })
            st.dataframe(pd.DataFrame(sub_data), use_container_width=True, hide_index=True)
        else:
            st.info("No subscriptions yet.")

    with tab3:
        st.subheader("All License Keys")
        if keys:
            key_data = []
            for k in keys:
                key_data.append({
                    "Key": k.get("license_key", ""),
                    "Email": k.get("email", ""),
                    "Type": k.get("type", ""),
                    "Status": k.get("status", ""),
                    "Usage": k.get("usage_count", 0),
                    "Created": k.get("created_at", "")[:10],
                })
            st.dataframe(pd.DataFrame(key_data), use_container_width=True, hide_index=True)
        else:
            st.info("No license keys yet.")

    # ── Footer ──────────────────────────────────────────────────────
    st.divider()
    st.caption(f"Last refreshed: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC · Auto-refreshes every 2 min")


if __name__ == "__main__":
    main()

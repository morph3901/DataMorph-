"""
DataMorph Admin Dashboard
=========================
Dark-themed dashboard for monitoring Stripe payments, subscriptions,
and license keys (Supabase).
"""

import os
from datetime import datetime, timezone, timedelta

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import stripe
from supabase import create_client

st.set_page_config(page_title="DataMorph Dashboard", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    [data-testid="stSidebar"] { background-color: #161b22; }
    .block-container { padding-top: 1rem; }
    .kpi-card {
        background: linear-gradient(135deg, #161b22 0%, #1c2333 100%);
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        text-align: center;
    }
    .kpi-card .kpi-label { font-size: 0.78rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 0.3rem; }
    .kpi-card .kpi-value { font-size: 2rem; font-weight: 800; color: #f0f6fc; margin-bottom: 0.2rem; }
    .kpi-card .kpi-sub { font-size: 0.75rem; color: #8b949e; }
    .kpi-card .kpi-sub .up { color: #3fb950; }
    .kpi-card .kpi-sub .down { color: #f85149; }
    .section-card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 1.2rem; }
    .section-title { font-size: 0.9rem; font-weight: 600; color: #f0f6fc; margin-bottom: 0.8rem; }
    .activity-row { display: flex; align-items: center; padding: 0.55rem 0; border-bottom: 1px solid #21262d; font-size: 0.82rem; }
    .activity-row:last-child { border-bottom: none; }
    .activity-icon { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 0.7rem; font-size: 0.75rem; flex-shrink: 0; }
    .activity-icon.signup { background: rgba(56,139,253,0.15); color: #58a6ff; }
    .activity-icon.upgrade { background: rgba(63,185,80,0.15); color: #3fb950; }
    .activity-icon.cancel { background: rgba(248,81,73,0.15); color: #f85149; }
    .activity-icon.first { background: rgba(163,113,247,0.15); color: #a371f7; }
    .activity-text { flex: 1; color: #c9d1d9; }
    .activity-text strong { color: #f0f6fc; }
    .activity-time { color: #8b949e; font-size: 0.75rem; white-space: nowrap; }
    .activity-badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 9999px; font-size: 0.7rem; font-weight: 600; margin-right: 0.5rem; }
    .badge-signup { background: rgba(56,139,253,0.15); color: #58a6ff; }
    .badge-upgrade { background: rgba(63,185,80,0.15); color: #3fb950; }
    .badge-cancel { background: rgba(248,81,73,0.15); color: #f85149; }
    .badge-first { background: rgba(163,113,247,0.15); color: #a371f7; }
    div[data-testid="stMetric"] { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 0.8rem 1rem; }
</style>
""", unsafe_allow_html=True)

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

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c9d1d9", size=12),
    xaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    yaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    margin=dict(l=0, r=0, t=30, b=0),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=11, color="#8b949e")),
)

def money(cents: int) -> str:
    return f"${cents / 100:,.0f}"

def relative_time(ts: int) -> str:
    now = datetime.now(timezone.utc)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    diff = now - dt
    if diff.days > 30:
        return f"{diff.days // 30}mo ago"
    if diff.days > 0:
        return f"{'a ' if diff.days == 1 else ''}{diff.days} day{'s' if diff.days > 1 else ''} ago"
    hours = diff.seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = diff.seconds // 60
    return f"{minutes}m ago" if minutes > 0 else "just now"

@st.cache_data(ttl=120)
def fetch_stripe():
    data = {}
    try:
        data["payments"] = stripe.PaymentIntent.list(limit=100).data
        data["subscriptions"] = stripe.Subscription.list(limit=100, status="all").data
        data["customers"] = stripe.Customer.list(limit=100).data
    except Exception as e:
        data["error"] = str(e)
    return data

@st.cache_data(ttl=120)
def fetch_supabase():
    if not supabase:
        return {"error": "Supabase not configured", "keys": []}
    try:
        resp = supabase.table("license_keys").select("*").execute()
        return {"keys": resp.data or []}
    except Exception as e:
        return {"error": str(e), "keys": []}

def get_customer_email(customer_id: str, customer_cache: dict) -> str:
    if not customer_id:
        return ""
    if customer_id in customer_cache:
        return customer_cache[customer_id]
    try:
        c = stripe.Customer.retrieve(customer_id)
        email = c.email or ""
    except Exception:
        email = ""
    customer_cache[customer_id] = email
    return email

def main():
    if not STRIPE_KEY:
        st.error("STRIPE_API_KEY not set.")
        st.stop()
    if not SUPABASE_URL:
        st.error("SUPABASE_URL not set.")
        st.stop()

    stripe_data = fetch_stripe()
    supa_data = fetch_supabase()

    if "error" in stripe_data:
        st.error(f"Stripe: {stripe_data['error']}")
    if "error" in supa_data:
        st.error(f"Supabase: {supa_data['error']}")

    payments = stripe_data.get("payments", [])
    subs = stripe_data.get("subscriptions", [])
    keys = supa_data.get("keys", [])
    customer_cache = {}

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_90 = now - timedelta(days=90)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

    succeeded = [p for p in payments if p.status == "succeeded"]
    total_revenue = sum(p.amount for p in succeeded)
    month_revenue = sum(p.amount for p in succeeded if p.created and datetime.fromtimestamp(p.created, tz=timezone.utc) >= month_start)
    prev_month_revenue = sum(p.amount for p in succeeded if p.created and prev_month_start <= datetime.fromtimestamp(p.created, tz=timezone.utc) < month_start)

    active_subs = [s for s in subs if s.status == "active"]
    canceled_recent = [s for s in subs if s.status in ("canceled", "unpaid") and s.canceled_at and datetime.fromtimestamp(s.canceled_at, tz=timezone.utc) >= month_start]

    month_customers = len(set(
        get_customer_email(p.customer, customer_cache)
        for p in succeeded if p.created and datetime.fromtimestamp(p.created, tz=timezone.utc) >= month_start and p.customer
    ))
    total_customers = len(set(p.customer for p in succeeded if p.customer))
    month_keys = len([k for k in keys if k.get("created_at", "") >= month_start.isoformat()])
    one_time_keys = len([k for k in keys if k.get("type") == "one_time"])
    sub_keys = len([k for k in keys if k.get("type") == "subscription"])

    one_time_customers = set(k.get("stripe_customer_id") for k in keys if k.get("type") == "one_time")
    sub_customers = set(k.get("stripe_customer_id") for k in keys if k.get("type") == "subscription")
    converted = one_time_customers & sub_customers
    conversion_rate = (len(converted) / len(one_time_customers) * 100) if one_time_customers else 0

    st.markdown("## DataMorph Owner Dashboard")
    st.markdown(f'<span style="color:#8b949e;font-size:0.85rem">Admin Overview &nbsp;·&nbsp; {now.strftime("%B %d, %Y")}</span>', unsafe_allow_html=True)

    st.markdown("")
    k1, k2, k3, k4 = st.columns(4)
    rev_delta = month_revenue - prev_month_revenue
    rev_color = "up" if rev_delta >= 0 else "down"
    rev_arrow = "+" if rev_delta >= 0 else ""

    with k1:
        st.markdown(f"""<div class="kpi-card"><div class="kpi-label">Customers</div><div class="kpi-value">{total_customers}</div><div class="kpi-sub"><span class="up">+{month_customers}</span> this month</div></div>""", unsafe_allow_html=True)
    with k2:
        st.markdown(f"""<div class="kpi-card"><div class="kpi-label">Revenue</div><div class="kpi-value">{money(total_revenue)}</div><div class="kpi-sub"><span class="{rev_color}">{rev_arrow}{money(rev_delta)}</span> from last month</div></div>""", unsafe_allow_html=True)
    with k3:
        st.markdown(f"""<div class="kpi-card"><div class="kpi-label">License Keys</div><div class="kpi-value">{len(keys)}</div><div class="kpi-sub"><span class="up">+{month_keys}</span> this month</div></div>""", unsafe_allow_html=True)
    with k4:
        st.markdown(f"""<div class="kpi-card"><div class="kpi-label">One-Time → Sub</div><div class="kpi-value">{conversion_rate:.1f}%</div><div class="kpi-sub">last 90 days</div></div>""", unsafe_allow_html=True)

    st.markdown("")

    col_mrr, col_rev = st.columns([3, 2])

    with col_mrr:
        st.markdown('<div class="section-card"><div class="section-title">MRR Evolution</div>', unsafe_allow_html=True)
        if active_subs:
            mrr_data = []
            for s in active_subs:
                if s.get("items", {}).get("data"):
                    price = s["items"]["data"][0].get("price", {})
                    amount = price.get("unit_amount", 0) / 100
                    created = datetime.fromtimestamp(s.created, tz=timezone.utc).date() if s.created else now.date()
                    mrr_data.append({"date": created, "amount": amount})
            if mrr_data:
                mrr_df = pd.DataFrame(mrr_data).groupby("date", as_index=False)["amount"].sum().sort_values("date")
                mrr_df["cumulative"] = mrr_df["amount"].cumsum()
                fig = px.line(mrr_df, x="date", y="cumulative", markers=True)
                fig.update_layout(**PLOTLY_LAYOUT, height=280, yaxis_title="$", xaxis_title="")
                fig.update_traces(line_color="#a371f7", line_width=2.5, marker_size=6)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No MRR data yet.")
        else:
            st.info("No active subscriptions yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_rev:
        st.markdown('<div class="section-card"><div class="section-title">Revenue by Day (30 Days)</div>', unsafe_allow_html=True)
        last_30 = now - timedelta(days=30)
        recent_rev = [
            {"date": datetime.fromtimestamp(p.created, tz=timezone.utc).date(), "revenue": p.amount / 100}
            for p in succeeded if p.created and datetime.fromtimestamp(p.created, tz=timezone.utc) >= last_30
        ]
        if recent_rev:
            rev_day_df = pd.DataFrame(recent_rev).groupby("date", as_index=False)["revenue"].sum()
            all_dates = pd.date_range(end=now.date(), periods=30, freq="D")
            rev_day_df = pd.DataFrame({"date": all_dates.date}).merge(rev_day_df, on="date", how="left").fillna(0)
            fig = px.bar(rev_day_df, x="date", y="revenue")
            fig.update_layout(**PLOTLY_LAYOUT, height=280, yaxis_title="$", xaxis_title="")
            fig.update_traces(marker_color="#58a6ff", marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No revenue in last 30 days.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("")

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.metric("Monthly Revenue", money(month_revenue), delta=f"{rev_arrow}{money(rev_delta)}")
    with s2:
        st.metric("Active Subscribers", len(active_subs))
    with s3:
        st.metric("Keys Generated", len(keys), delta=f"+{month_keys} this month")
    with s4:
        st.metric("Cancellations", len(canceled_recent), delta=f"-{len(canceled_recent)}" if canceled_recent else "0")

    st.markdown("")

    col_signups, col_dist = st.columns([3, 2])

    with col_signups:
        st.markdown('<div class="section-card"><div class="section-title">Monthly Signups by Type</div>', unsafe_allow_html=True)
        if keys:
            signup_data = []
            for k in keys:
                created = k.get("created_at", "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        signup_data.append({"month": dt.strftime("%b %y"), "type": "One-Time" if k.get("type") == "one_time" else "Subscription"})
                    except Exception:
                        pass
            if signup_data:
                signups_df = pd.DataFrame(signup_data)
                signups_grouped = signups_df.groupby(["month", "type"], as_index=False).size()
                fig = px.bar(signups_grouped, x="month", y="size", color="type", color_discrete_map={"One-Time": "#58a6ff", "Subscription": "#a371f7"}, barmode="group")
                fig.update_layout(**PLOTLY_LAYOUT, height=280, xaxis_title="", yaxis_title="Signups", legend_title_text="")
                fig.update_traces(marker_line_width=0)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No signup data.")
        else:
            st.info("No keys generated yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_dist:
        st.markdown('<div class="section-card"><div class="section-title">Key Distribution</div>', unsafe_allow_html=True)
        if keys:
            dist_data = {"One-Time": one_time_keys, "Subscription": sub_keys}
            fig = go.Figure(data=[go.Pie(labels=list(dist_data.keys()), values=list(dist_data.values()), hole=0.55, marker=dict(colors=["#58a6ff", "#a371f7"]), textfont=dict(size=13, color="#f0f6fc"), textinfo="label+percent")])
            fig.update_layout(**PLOTLY_LAYOUT, height=280, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No keys yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("")

    st.markdown('<div class="section-card"><div class="section-title">Recent Activity</div>', unsafe_allow_html=True)
    activities = []
    for k in keys:
        created = k.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            continue
        ts = int(dt.timestamp())
        email = k.get("email", "")
        ktype = k.get("type", "subscription")
        activities.append({"type": "signup", "label": "+ Signup", "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown", "email": email, "plan": "One-Time" if ktype == "one_time" else "Subscription", "detail": f"{'Purchased one-time key' if ktype == 'one_time' else 'Subscribed to monthly plan'}", "ts": ts})
        if k.get("usage_count", 0) > 0:
            activities.append({"type": "first", "label": "▷ First Use", "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown", "email": email, "plan": "One-Time" if ktype == "one_time" else "Subscription", "detail": "Used license key for download", "ts": ts + 1})

    for s in subs:
        if s.status == "active" and s.get("canceled_at"):
            ts = s["canceled_at"]
            email = get_customer_email(s.customer, customer_cache)
            activities.append({"type": "cancel", "label": "✕ Cancellation", "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown", "email": email, "plan": "Subscription", "detail": "Cancelled subscription", "ts": ts})

    activities.sort(key=lambda x: x["ts"], reverse=True)

    if activities:
        icon_map = {"signup": ("+", "badge-signup"), "first": ("▷", "badge-first"), "cancel": ("✕", "badge-cancel"), "upgrade": ("↑", "badge-upgrade")}
        for a in activities[:15]:
            icon_char, badge_cls = icon_map.get(a["type"], ("•", "badge-signup"))
            st.markdown(f"""<div class="activity-row"><div class="activity-icon {a['type']}">{icon_char}</div><div class="activity-text"><span class="activity-badge {badge_cls}">{a['label']}</span><strong>{a['user']}</strong> &nbsp;<span style="color:#8b949e">· {a['plan']} · {a['detail']}</span><br><span style="color:#8b949e;font-size:0.72rem">{a['email']}</span></div><div class="activity-time">{relative_time(a['ts'])}</div></div>""", unsafe_allow_html=True)
    else:
        st.info("No activity yet.")

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("")
    st.markdown(f'<div style="text-align:center;color:#484f58;font-size:0.75rem">● Live &nbsp;·&nbsp; Auto-refreshes every 2 min &nbsp;·&nbsp; {now.strftime("%H:%M:%S")} UTC</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()

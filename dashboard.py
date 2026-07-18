"""
DataMorph Admin Dashboard
=========================
Dark-themed dashboard for monitoring Stripe payments, subscriptions,
license keys (Supabase), and landing page analytics (GA4).
"""

import os
import json
from datetime import datetime, timezone, timedelta

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import stripe
from supabase import create_client

# ── Page Config ─────────────────────────────────────────────────────────
st.set_page_config(page_title="DataMorph Dashboard", page_icon="📊", layout="wide")

# ── Dark Theme CSS ──────────────────────────────────────────────────────
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
    .kpi-card .kpi-label {
        font-size: 0.78rem;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 0.3rem;
    }
    .kpi-card .kpi-value {
        font-size: 2rem;
        font-weight: 800;
        color: #f0f6fc;
        margin-bottom: 0.2rem;
    }
    .kpi-card .kpi-sub {
        font-size: 0.75rem;
        color: #8b949e;
    }
    .kpi-card .kpi-sub .up { color: #3fb950; }
    .kpi-card .kpi-sub .down { color: #f85149; }

    .section-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 1.2rem;
    }
    .section-title {
        font-size: 0.9rem;
        font-weight: 600;
        color: #f0f6fc;
        margin-bottom: 0.8rem;
    }

    .activity-row {
        display: flex;
        align-items: center;
        padding: 0.55rem 0;
        border-bottom: 1px solid #21262d;
        font-size: 0.82rem;
    }
    .activity-row:last-child { border-bottom: none; }
    .activity-icon {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-right: 0.7rem;
        font-size: 0.75rem;
        flex-shrink: 0;
    }
    .activity-icon.signup { background: rgba(56,139,253,0.15); color: #58a6ff; }
    .activity-icon.upgrade { background: rgba(63,185,80,0.15); color: #3fb950; }
    .activity-icon.cancel { background: rgba(248,81,73,0.15); color: #f85149; }
    .activity-icon.first { background: rgba(163,113,247,0.15); color: #a371f7; }
    .activity-text { flex: 1; color: #c9d1d9; }
    .activity-text strong { color: #f0f6fc; }
    .activity-time { color: #8b949e; font-size: 0.75rem; white-space: nowrap; }
    .activity-badge {
        display: inline-block;
        padding: 0.15rem 0.5rem;
        border-radius: 9999px;
        font-size: 0.7rem;
        font-weight: 600;
        margin-right: 0.5rem;
    }
    .badge-signup { background: rgba(56,139,253,0.15); color: #58a6ff; }
    .badge-upgrade { background: rgba(63,185,80,0.15); color: #3fb950; }
    .badge-cancel { background: rgba(248,81,73,0.15); color: #f85149; }
    .badge-first { background: rgba(163,113,247,0.15); color: #a371f7; }

    div[data-testid="stMetric"] {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 0.8rem 1rem;
    }
</style>
""", unsafe_allow_html=True)

# ── Secrets / Env ───────────────────────────────────────────────────────
def _get(key: str) -> str:
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ.get(key, "")


STRIPE_KEY = _get("STRIPE_API_KEY")
SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_KEY = _get("SUPABASE_KEY")
GA4_PROPERTY_ID = "546060130"

stripe.api_key = STRIPE_KEY
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c9d1d9", size=12),
    xaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    yaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    margin=dict(l=0, r=0, t=30, b=0),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        font=dict(size=11, color="#8b949e"),
    ),
)


# ── Helpers ─────────────────────────────────────────────────────────────
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


def get_ga4_client():
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
    except ImportError:
        return None
    # Method 1: Try individual secret fields
    try:
        private_key = _get("GA4_PRIVATE_KEY")
        client_email = _get("GA4_CLIENT_EMAIL")
        if private_key and client_email:
            info = {
                "type": "service_account",
                "project_id": "datamorph-502810",
                "private_key_id": _get("GA4_PRIVATE_KEY_ID") or "",
                "private_key": private_key.replace("\\n", "\n"),
                "client_email": client_email,
                "client_id": _get("GA4_CLIENT_ID") or "",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{client_email.replace('@', '%40')}",
            }
            return BetaAnalyticsDataClient.from_service_account_info(info)
    except Exception as e:
        st.warning(f"GA4 individual secrets error: {e}")
    # Method 2: Try JSON string secret
    try:
        sa_json = _get("GA4_SERVICE_ACCOUNT_JSON")
        if sa_json:
            info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
            return BetaAnalyticsDataClient.from_service_account_info(info)
    except Exception as e:
        st.warning(f"GA4 JSON secret error: {e}")
    return None


def ga4_query(client, metrics, dimensions=None, days=30, limit=None):
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric as G4Metric, Dimension as G4Dimension
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        metrics=[G4Metric(name=m) for m in metrics],
        dimensions=[G4Dimension(name=d) for d in (dimensions or [])],
        limit=limit,
    )
    return client.run_report(request)


# ── Data Fetching ───────────────────────────────────────────────────────
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


@st.cache_data(ttl=300)
def fetch_ga4_overview():
    client = get_ga4_client()
    if not client:
        return None
    try:
        r = ga4_query(client, ["totalUsers", "sessions", "screenPageViews", "bounceRate", "averageSessionDuration"], days=30)
        row = r.rows[0] if r.rows else None
        if not row:
            return None
        return {
            "users": int(row.metric_values[0].value),
            "sessions": int(row.metric_values[1].value),
            "views": int(row.metric_values[2].value),
            "bounce_rate": float(row.metric_values[3].value),
            "avg_duration": float(row.metric_values[4].value),
        }
    except Exception:
        return None


@st.cache_data(ttl=300)
def fetch_ga4_daily():
    client = get_ga4_client()
    if not client:
        return None
    try:
        r = ga4_query(client, ["sessions", "totalUsers"], dimensions=["date"], days=30)
        rows = [{"date": row.dimension_values[0].value, "sessions": int(row.metric_values[0].value), "users": int(row.metric_values[1].value)} for row in r.rows]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df.sort_values("date")
    except Exception:
        return None


@st.cache_data(ttl=300)
def fetch_ga4_sources():
    client = get_ga4_client()
    if not client:
        return None
    try:
        r = ga4_query(client, ["sessions"], dimensions=["sessionSource"], days=30, limit=10)
        return [{"source": row.dimension_values[0].value or "(direct)", "sessions": int(row.metric_values[0].value)} for row in r.rows]
    except Exception:
        return None


@st.cache_data(ttl=300)
def fetch_ga4_pages():
    client = get_ga4_client()
    if not client:
        return None
    try:
        r = ga4_query(client, ["screenPageViews"], dimensions=["pagePath"], days=30, limit=10)
        return [{"path": row.dimension_values[0].value, "views": int(row.metric_values[0].value)} for row in r.rows]
    except Exception:
        return None


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


# ── Main ────────────────────────────────────────────────────────────────
def main():
    if not STRIPE_KEY:
        st.error("STRIPE_API_KEY not set.")
        st.stop()
    if not SUPABASE_URL:
        st.error("SUPABASE_URL not set.")
        st.stop()

    stripe_data = fetch_stripe()
    supa_data = fetch_supabase()
    ga4_overview = fetch_ga4_overview()
    ga4_daily = fetch_ga4_daily()
    ga4_sources = fetch_ga4_sources()
    ga4_pages = fetch_ga4_pages()

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

    # ── Header ──────────────────────────────────────────────────────
    st.markdown("## DataMorph Owner Dashboard")
    st.markdown(f'<span style="color:#8b949e;font-size:0.85rem">Admin Overview &nbsp;·&nbsp; {now.strftime("%B %d, %Y")}</span>', unsafe_allow_html=True)

    # ── Landing Page KPI Row ────────────────────────────────────────
    st.markdown("### 🌐 Landing Page Analytics")
    lp1, lp2, lp3, lp4, lp5 = st.columns(5)
    if ga4_overview:
        with lp1:
            st.metric("Visitors (30d)", f"{ga4_overview['users']:,}")
        with lp2:
            st.metric("Sessions (30d)", f"{ga4_overview['sessions']:,}")
        with lp3:
            st.metric("Page Views (30d)", f"{ga4_overview['views']:,}")
        with lp4:
            st.metric("Bounce Rate", f"{ga4_overview['bounce_rate']:.1%}")
        with lp5:
            mins = ga4_overview['avg_duration'] / 60
            st.metric("Avg. Session", f"{mins:.1f}m")
    else:
        lp1.info("GA4 not connected")
    st.markdown("")

    # ── Charts Row: Traffic + Sources ───────────────────────────────
    col_traffic, col_sources = st.columns([3, 2])

    with col_traffic:
        st.markdown('<div class="section-card"><div class="section-title">Daily Traffic (30 Days)</div>', unsafe_allow_html=True)
        if ga4_daily is not None and not ga4_daily.empty:
            fig = px.bar(ga4_daily, x="date", y="sessions", color_discrete_sequence=["#58a6ff"])
            if "users" in ga4_daily.columns:
                fig.add_scatter(x=ga4_daily["date"], y=ga4_daily["users"], mode="lines", name="Users", line=dict(color="#3fb950", width=2))
            fig.update_layout(**PLOTLY_LAYOUT, height=280, xaxis_title="", yaxis_title="Sessions", showlegend=True)
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No traffic data yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_sources:
        st.markdown('<div class="section-card"><div class="section-title">Traffic Sources (30 Days)</div>', unsafe_allow_html=True)
        if ga4_sources:
            src_df = pd.DataFrame(ga4_sources)
            fig = px.bar(src_df, x="sessions", y="source", orientation="h", color_discrete_sequence=["#a371f7"])
            fig.update_layout(**PLOTLY_LAYOUT, height=280, xaxis_title="Sessions", yaxis_title="", showlegend=False)
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No source data yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Top Pages ───────────────────────────────────────────────────
    col_pages, col_empty = st.columns([3, 2])

    with col_pages:
        st.markdown('<div class="section-card"><div class="section-title">Top Pages (30 Days)</div>', unsafe_allow_html=True)
        if ga4_pages:
            pages_df = pd.DataFrame(ga4_pages)
            fig = px.bar(pages_df, x="views", y="path", orientation="h", color_discrete_sequence=["#f0883e"])
            fig.update_layout(**PLOTLY_LAYOUT, height=280, xaxis_title="Views", yaxis_title="", showlegend=False)
            fig.update_traces(marker_line_width=0)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No page data yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")

    # ── Stripe / Revenue Section ────────────────────────────────────
    st.markdown("### 💰 Revenue & Payments")

    k1, k2, k3, k4 = st.columns(4)
    rev_delta = month_revenue - prev_month_revenue
    rev_color = "up" if rev_delta >= 0 else "down"
    rev_arrow = "+" if rev_delta >= 0 else ""

    with k1:
        st.metric("Total Revenue", money(total_revenue))
    with k2:
        st.metric("Monthly Revenue", money(month_revenue), delta=f"{rev_arrow}{money(rev_delta)}")
    with k3:
        st.metric("Active Subscribers", len(active_subs))
    with k4:
        st.metric("Cancellations", len(canceled_recent), delta=f"-{len(canceled_recent)}" if canceled_recent else "0")

    st.markdown("")

    # ── Charts Row: MRR + Revenue by Day ────────────────────────────
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

    # ── License Keys Section ────────────────────────────────────────
    st.markdown("### 🔑 License Keys")

    lk1, lk2, lk3, lk4 = st.columns(4)
    with lk1:
        st.metric("Total Keys", len(keys))
    with lk2:
        st.metric("This Month", month_keys)
    with lk3:
        st.metric("One-Time", one_time_keys)
    with lk4:
        st.metric("Conversion Rate", f"{conversion_rate:.1f}%")

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
                        signup_data.append({
                            "month": dt.strftime("%b %y"),
                            "type": "One-Time" if k.get("type") == "one_time" else "Subscription",
                        })
                    except Exception:
                        pass
            if signup_data:
                signups_df = pd.DataFrame(signup_data)
                signups_grouped = signups_df.groupby(["month", "type"], as_index=False).size()
                fig = px.bar(signups_grouped, x="month", y="size", color="type",
                             color_discrete_map={"One-Time": "#58a6ff", "Subscription": "#a371f7"},
                             barmode="group")
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
            fig = go.Figure(data=[go.Pie(
                labels=list(dist_data.keys()),
                values=list(dist_data.values()),
                hole=0.55,
                marker=dict(colors=["#58a6ff", "#a371f7"]),
                textfont=dict(size=13, color="#f0f6fc"),
                textinfo="label+percent",
            )])
            fig.update_layout(**PLOTLY_LAYOUT, height=280, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No keys yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("")

    # ── Recent Activity ─────────────────────────────────────────────
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

        activities.append({
            "type": "signup",
            "label": "+ Signup",
            "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown",
            "email": email,
            "plan": "One-Time" if ktype == "one_time" else "Subscription",
            "detail": f"{'Purchased one-time key' if ktype == 'one_time' else 'Subscribed to monthly plan'}",
            "ts": ts,
        })

        if k.get("usage_count", 0) > 0:
            activities.append({
                "type": "first",
                "label": "▷ First Use",
                "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown",
                "email": email,
                "plan": "One-Time" if ktype == "one_time" else "Subscription",
                "detail": "Used license key for download",
                "ts": ts + 1,
            })

    for s in subs:
        if s.status == "active" and s.get("canceled_at"):
            ts = s["canceled_at"]
            email = get_customer_email(s.customer, customer_cache)
            activities.append({
                "type": "cancel",
                "label": "✕ Cancellation",
                "user": email.split("@")[0].replace(".", " ").title() if email else "Unknown",
                "email": email,
                "plan": "Subscription",
                "detail": "Cancelled subscription",
                "ts": ts,
            })

    activities.sort(key=lambda x: x["ts"], reverse=True)

    if activities:
        icon_map = {"signup": ("+", "badge-signup"), "first": ("▷", "badge-first"), "cancel": ("✕", "badge-cancel"), "upgrade": ("↑", "badge-upgrade")}
        for a in activities[:15]:
            icon_char, badge_cls = icon_map.get(a["type"], ("•", "badge-signup"))
            st.markdown(f"""<div class="activity-row">
                <div class="activity-icon {a['type']}">{icon_char}</div>
                <div class="activity-text">
                    <span class="activity-badge {badge_cls}">{a['label']}</span>
                    <strong>{a['user']}</strong> &nbsp;
                    <span style="color:#8b949e">· {a['plan']} · {a['detail']}</span>
                    <br><span style="color:#8b949e;font-size:0.72rem">{a['email']}</span>
                </div>
                <div class="activity-time">{relative_time(a['ts'])}</div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("No activity yet.")

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("")
    st.markdown(f'<div style="text-align:center;color:#484f58;font-size:0.75rem">● Live &nbsp;·&nbsp; Auto-refreshes every 2 min &nbsp;·&nbsp; {now.strftime("%H:%M:%S")} UTC</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()

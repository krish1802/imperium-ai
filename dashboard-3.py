"""
SEO Traffic Dashboard for imperiumai.ai

Reads the report CSVs that bypass.py writes into:
    seo_reports/imperiumai-ai/traffic_generated_YYYY-MM-DD.csv

Each CSV has the schema:
    date, site, engine, clicks

The dashboard auto-discovers ALL traffic CSVs in the report folder,
concatenates them, and renders graphs.

Run:
    streamlit run dashboard.py
"""

from __future__ import annotations

import glob
import os

import pandas as pd
import plotly.express as px
import streamlit as st

# Live Google Search Console fetcher (real clicks/impressions/CTR/position).
try:
    from gsc_fetch import fetch_gsc, default_range
    GSC_AVAILABLE = True
except Exception:  # module import shouldn't break the bot-only dashboard
    GSC_AVAILABLE = False

# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────

REPORT_DIR = os.path.join("seo_reports", "imperiumai_ai")
CSV_GLOB = os.path.join(REPORT_DIR, "traffic_generated_*.csv")

st.set_page_config(page_title="imperiumai.ai · SEO Traffic", layout="wide")


# ──────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────

COLUMNS = ["date", "site", "page", "engine", "clicks"]


@st.cache_data(show_spinner="Fetching Google Search Console…", ttl=3600)
def load_gsc(start: str, end: str, by_query: bool) -> pd.DataFrame:
    """Cached live pull from Google Search Console (1-hour TTL)."""
    return fetch_gsc(start=start, end=end, by_query=by_query, tracked_only=True)


@st.cache_data(show_spinner=False)
def load_data(pattern: str) -> pd.DataFrame:
    """Load and concatenate every traffic_generated_*.csv in the report dir."""
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame(columns=COLUMNS)

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            frames.append(df)
        except Exception as exc:  # skip malformed files but keep going
            st.warning(f"Could not read {os.path.basename(f)}: {exc}")

    if not frames:
        return pd.DataFrame(columns=COLUMNS)

    data = pd.concat(frames, ignore_index=True)
    # Backward-compat: older CSVs (before per-page tracking) have no `page` column.
    if "page" not in data.columns:
        data["page"] = "(all)"
    data["page"] = data["page"].fillna("(all)")
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["clicks"] = pd.to_numeric(data["clicks"], errors="coerce").fillna(0).astype(int)
    data = data.dropna(subset=["date"])
    # Collapse any duplicate (date, site, page, engine) rows across files
    data = (
        data.groupby(["date", "site", "page", "engine"], as_index=False)["clicks"]
        .sum()
        .sort_values("date")
    )
    return data


# ──────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────

st.title("imperiumai.ai — SEO Traffic Dashboard")

# ---- Data source selector -------------------------------------------------
source_options = ["Bot clicks (bypass.py)"]
if GSC_AVAILABLE:
    source_options.append("Google Search Console (live)")

with st.sidebar:
    st.header("Data source")
    source = st.radio("Show data from", source_options, index=0)
    if not GSC_AVAILABLE:
        st.caption("GSC live source unavailable — `gsc_fetch.py` or its "
                   "Google API dependencies are missing.")

USE_GSC = source.startswith("Google Search Console")


# ══════════════════════════════════════════════════════════════════════════
# GOOGLE SEARCH CONSOLE VIEW (live, real clicks/impressions/CTR/position)
# ══════════════════════════════════════════════════════════════════════════
if USE_GSC:
    st.caption("Source: Google Search Console API · property `https://imperiumai.ai/`")

    with st.sidebar:
        st.header("GSC options")
        lookback = st.slider("Look-back window (days)", 7, 180, 30, step=1)
        by_query = st.checkbox("Break down by search query", value=False)
        st.button("🔄 Refresh GSC", on_click=load_gsc.clear)

    g_start, g_end = default_range(lookback)
    gsc = load_gsc(g_start, g_end, by_query)

    if gsc.empty:
        err = gsc.attrs.get("error")
        if err:
            st.error(
                "Could not fetch Google Search Console data.\n\n"
                f"**{err}**\n\nCheck that `GSC_SERVICE_ACCOUNT_FILE` (or "
                "`GSC_OAUTH_CLIENT_FILE`) is set and the account has access "
                "to the property. See `gsc_fetch.py` for setup steps."
            )
        else:
            st.info("No Search Console rows for the tracked pages in this window.")
        st.stop()

    # -- GSC page filter
    with st.sidebar:
        g_pages = sorted(gsc["page"].unique())
        g_picked = st.multiselect("Pages (links)", g_pages, default=g_pages, key="gsc_pages")
    gview = gsc[gsc["page"].isin(g_picked)]
    if gview.empty:
        st.warning("No data matches the current filters.")
        st.stop()

    # -- KPI cards (weighted CTR/position, not naive means)
    g_clicks = int(gview["clicks"].sum())
    g_impr = int(gview["impressions"].sum())
    g_ctr = (g_clicks / g_impr * 100) if g_impr else 0.0
    g_pos = (
        (gview["position"] * gview["impressions"]).sum() / g_impr if g_impr else 0.0
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Clicks", f"{g_clicks:,}")
    k2.metric("Impressions", f"{g_impr:,}")
    k3.metric("CTR", f"{g_ctr:.2f}%")
    k4.metric("Avg position", f"{g_pos:.1f}")
    st.caption(f"Window: {g_start} → {g_end}")
    st.divider()

    # -- Clicks & impressions by page
    by_page = gview.groupby("page", as_index=False)[["clicks", "impressions"]].sum()
    gp = by_page.melt(id_vars="page", value_vars=["clicks", "impressions"],
                      var_name="metric", value_name="count")
    fig_gp = px.bar(gp, x="page", y="count", color="metric", barmode="group",
                    title="Clicks & Impressions by Page", text="count")
    fig_gp.update_traces(textposition="outside")
    st.plotly_chart(fig_gp, use_container_width=True)

    # -- Time series (clicks per page)
    daily = gview.groupby(["date", "page"], as_index=False)[["clicks", "impressions"]].sum()
    if daily["date"].dt.date.nunique() > 1:
        fig_gt = px.line(daily, x="date", y="clicks", color="page", markers=True,
                         title="Clicks Over Time (per Page)")
        st.plotly_chart(fig_gt, use_container_width=True)

    # -- Top queries (only when broken down by query)
    if by_query and "query" in gview.columns and gview["query"].str.len().gt(0).any():
        top_q = (gview.groupby("query", as_index=False)[["clicks", "impressions"]]
                 .sum().sort_values("clicks", ascending=False).head(20))
        fig_q = px.bar(top_q, x="clicks", y="query", orientation="h",
                       title="Top Queries by Clicks", text="clicks")
        fig_q.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_q, use_container_width=True)

    # -- Raw data
    with st.expander("View raw GSC data"):
        show = gview.assign(date=gview["date"].dt.date)
        show = show[["date", "page", "url", "query", "clicks",
                     "impressions", "ctr", "position"]]
        st.dataframe(show.sort_values(["date", "clicks"], ascending=[True, False]),
                     use_container_width=True, hide_index=True)

    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# BOT-CLICK VIEW (bypass.py CSVs) — original dashboard
# ══════════════════════════════════════════════════════════════════════════
st.caption(f"Source: `{CSV_GLOB}`")

data = load_data(CSV_GLOB)

if data.empty:
    st.info(
        "No report data found yet.\n\n"
        f"Run `bypass.py` so it writes CSVs into `{REPORT_DIR}/`, "
        "then refresh this page."
    )
    st.stop()

# ---- Sidebar filters ------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    pages = sorted(data["page"].unique())
    picked_pages = st.multiselect("Pages (links)", pages, default=pages)

    engines = sorted(data["engine"].unique())
    picked_engines = st.multiselect("Search engines", engines, default=engines)

    min_d, max_d = data["date"].min().date(), data["date"].max().date()
    if min_d == max_d:
        date_range = (min_d, max_d)
        st.write(f"Date: **{min_d}** (single day of data)")
    else:
        date_range = st.date_input(
            "Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            pass
        else:  # user picked a single date
            date_range = (date_range, date_range)

    st.button("🔄 Refresh data", on_click=load_data.clear)

# ---- Apply filters --------------------------------------------------------
mask = (
    data["engine"].isin(picked_engines)
    & data["page"].isin(picked_pages)
    & (data["date"].dt.date >= date_range[0])
    & (data["date"].dt.date <= date_range[1])
)
view = data[mask]

if view.empty:
    st.warning("No data matches the current filters.")
    st.stop()

# ---- KPI cards ------------------------------------------------------------
total_clicks = int(view["clicks"].sum())
n_days = view["date"].dt.date.nunique()
n_pages = view["page"].nunique()
top_engine_row = view.groupby("engine")["clicks"].sum().sort_values(ascending=False)
top_engine = top_engine_row.index[0] if not top_engine_row.empty else "—"
top_page_row = view.groupby("page")["clicks"].sum().sort_values(ascending=False)
top_page = top_page_row.index[0] if not top_page_row.empty else "—"
avg_per_day = total_clicks / n_days if n_days else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total clicks", f"{total_clicks:,}")
c2.metric("Pages tracked", n_pages)
c3.metric("Top page", top_page)
c4.metric("Top engine", top_engine)
c5.metric("Avg clicks / day", f"{avg_per_day:.1f}")

st.divider()

# ---- Charts ---------------------------------------------------------------
left, right = st.columns(2)

# Clicks by page (bar) — the per-link breakdown you asked for
by_page = (
    view.groupby("page", as_index=False)["clicks"].sum().sort_values("clicks", ascending=False)
)
fig_page_bar = px.bar(
    by_page,
    x="page",
    y="clicks",
    color="page",
    title="Clicks by Page (Link)",
    text="clicks",
)
fig_page_bar.update_traces(textposition="outside")
fig_page_bar.update_layout(showlegend=False)
left.plotly_chart(fig_page_bar, use_container_width=True)

# Share of clicks by page (pie)
fig_page_pie = px.pie(
    by_page,
    names="page",
    values="clicks",
    title="Traffic Share by Page",
    hole=0.4,
)
right.plotly_chart(fig_page_pie, use_container_width=True)

# Clicks by engine, split per page (grouped bar)
by_engine_page = view.groupby(["engine", "page"], as_index=False)["clicks"].sum()
fig_engine_bar = px.bar(
    by_engine_page,
    x="engine",
    y="clicks",
    color="page",
    barmode="group",
    title="Clicks by Search Engine (per Page)",
    text="clicks",
)
fig_engine_bar.update_traces(textposition="outside")
st.plotly_chart(fig_engine_bar, use_container_width=True)

# Clicks over time (line), one line per page — only meaningful with multiple days
if n_days > 1:
    over_time = view.groupby(["date", "page"], as_index=False)["clicks"].sum()
    fig_line = px.line(
        over_time,
        x="date",
        y="clicks",
        color="page",
        markers=True,
        title="Clicks Over Time (per Page)",
    )
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("Only one day of data available — the time-series chart appears once more daily reports accumulate.")

# ---- Raw data -------------------------------------------------------------
with st.expander("View raw data"):
    st.dataframe(
        view.assign(date=view["date"].dt.date).sort_values(
            ["date", "page", "clicks"], ascending=[True, True, False]
        ),
        use_container_width=True,
        hide_index=True,
    )

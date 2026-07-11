"""
SEO Traffic Dashboard for imperiumai.ai

Two data sources, switchable in the sidebar:
  1. Bot clicks (bypass.py) — self-generated search-result clicks from
     seo_reports/imperiumai_ai/traffic_generated_YYYY-MM-DD.csv
  2. Google Search Console (live) — real clicks / impressions / CTR / position
     pulled via gsc_fetch.py (auth from Streamlit secrets).

Bot CSVs handle BOTH schemas transparently:
    old:  date, site, engine, clicks
    new:  date, site, page, engine, clicks
Files missing a `page` column are treated as page = "all".

Run:
    streamlit run dashboard.py
"""

from __future__ import annotations

import glob
import os

import pandas as pd
import plotly.express as px
import streamlit as st

# Live Google Search Console fetcher (optional — degrades gracefully).
try:
    from gsc_fetch import fetch_gsc, default_range
    GSC_AVAILABLE = True
except Exception:
    GSC_AVAILABLE = False

# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────

REPORT_DIR = os.path.join("seo_reports", "imperiumai_ai")  # matches Site.slug
CSV_GLOB = os.path.join(REPORT_DIR, "traffic_generated_*.csv")

st.set_page_config(page_title="imperiumai.ai · SEO Traffic", layout="wide")


# ──────────────────────────────────────────────────────────────────────────
# GSC live loader
# ──────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Fetching Google Search Console …")
def load_gsc(start: str, end: str, by_query: bool) -> pd.DataFrame:
    """Cached GSC fetch (tracked pages only)."""
    return fetch_gsc(start=start, end=end, by_query=by_query, tracked_only=True)


# ──────────────────────────────────────────────────────────────────────────
# Data loading — schema-tolerant (bot CSVs)
# ──────────────────────────────────────────────────────────────────────────

EXPECTED_COLS = ["date", "site", "page", "engine", "clicks"]


@st.cache_data(show_spinner=False)
def load_data(pattern: str) -> pd.DataFrame:
    """Load + concatenate every traffic CSV, normalizing to a common schema.

    Both the old (no `page`) and new (`page`) layouts are supported. Missing
    `page` values become "all".
    """
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame(columns=EXPECTED_COLS)

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
        except Exception as exc:
            st.warning(f"Could not read {os.path.basename(f)}: {exc}")
            continue

        # Normalize column names to lowercase for robustness
        df.columns = [c.strip().lower() for c in df.columns]

        # Old schema has no `page` column -> add one
        if "page" not in df.columns:
            df["page"] = "all"

        # Keep only the columns we care about (in case of extras)
        keep = [c for c in EXPECTED_COLS if c in df.columns]
        frames.append(df[keep])

    if not frames:
        return pd.DataFrame(columns=EXPECTED_COLS)

    data = pd.concat(frames, ignore_index=True)

    # Fill any structural gaps so every row has all expected columns
    for col in EXPECTED_COLS:
        if col not in data.columns:
            data[col] = "all" if col == "page" else ""

    data["page"] = data["page"].fillna("all").replace("", "all")
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["clicks"] = pd.to_numeric(data["clicks"], errors="coerce").fillna(0).astype(int)
    data = data.dropna(subset=["date"])

    # Collapse duplicate rows across files
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
        st.caption("GSC live source unavailable — `gsc_fetch.py` / its Google "
                   "libraries could not be imported.")
    st.divider()

USE_GSC = source.startswith("Google Search Console")


# ══════════════════════════════════════════════════════════════════════════
# GOOGLE SEARCH CONSOLE VIEW (live)
# ══════════════════════════════════════════════════════════════════════════
if USE_GSC:
    st.caption("Source: Google Search Console API · https://imperiumai.ai/")

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
                f"Could not fetch GSC data:\n\n**{err}**\n\n"
                "Check that your service account is in Streamlit secrets under "
                "`[gsc_service_account]` and has access to the property."
            )
        else:
            st.info(f"No GSC data for {g_start} → {g_end} on the tracked pages.")
        st.stop()

    # -- Page filter
    with st.sidebar:
        g_pages = sorted(gsc["page"].unique())
        g_picked = st.multiselect("Pages", g_pages, default=g_pages, key="gsc_pages")
    gview = gsc[gsc["page"].isin(g_picked)]
    if gview.empty:
        st.warning("No GSC data matches the current filters.")
        st.stop()

    # -- KPI cards (weighted CTR/position, not naive means)
    g_clicks = int(gview["clicks"].sum())
    g_impr = int(gview["impressions"].sum())
    g_ctr = (g_clicks / g_impr * 100) if g_impr else 0.0
    # Impression-weighted average position
    g_pos = (
        (gview["position"] * gview["impressions"]).sum() / g_impr if g_impr else 0.0
    )

    st.caption(f"Window: {g_start} → {g_end} (GSC data lags ~2 days)")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Clicks", f"{g_clicks:,}")
    k2.metric("Impressions", f"{g_impr:,}")
    k3.metric("CTR", f"{g_ctr:.2f}%")
    k4.metric("Avg position", f"{g_pos:.1f}")
    st.divider()

    # -- Charts
    gl, gr = st.columns(2)

    # Clicks & impressions by page
    by_page = gview.groupby("page", as_index=False)[["clicks", "impressions"]].sum()
    fig_gp = px.bar(
        by_page.melt(id_vars="page", value_vars=["clicks", "impressions"],
                     var_name="metric", value_name="value"),
        x="page", y="value", color="metric", barmode="group",
        title="Clicks & Impressions by Page",
    )
    gl.plotly_chart(fig_gp, use_container_width=True)

    # CTR by page
    ctr_page = by_page.assign(
        ctr=lambda d: (d["clicks"] / d["impressions"].replace(0, pd.NA) * 100).fillna(0)
    )
    fig_ctr = px.bar(
        ctr_page, x="page", y="ctr", color="page", text="ctr",
        title="CTR by Page (%)",
    )
    fig_ctr.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
    fig_ctr.update_layout(showlegend=False)
    gr.plotly_chart(fig_ctr, use_container_width=True)

    # Clicks & impressions over time
    over_time = gview.groupby(["date", "page"], as_index=False)[["clicks", "impressions"]].sum()
    fig_line = px.line(
        over_time, x="date", y="clicks", color="page", markers=True,
        title="Clicks Over Time (by page)",
    )
    st.plotly_chart(fig_line, use_container_width=True)

    fig_impr = px.line(
        over_time, x="date", y="impressions", color="page", markers=True,
        title="Impressions Over Time (by page)",
    )
    st.plotly_chart(fig_impr, use_container_width=True)

    # Top queries (only when broken down by query)
    if by_query and "query" in gview.columns and gview["query"].astype(bool).any():
        top_q = (
            gview.groupby("query", as_index=False)[["clicks", "impressions"]].sum()
            .sort_values("clicks", ascending=False).head(20)
        )
        st.subheader("Top queries")
        fig_q = px.bar(top_q, x="clicks", y="query", orientation="h",
                       title="Top 20 Queries by Clicks")
        fig_q.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_q, use_container_width=True)

    with st.expander("View raw GSC data"):
        st.dataframe(
            gview.assign(date=gview["date"].dt.date).sort_values(
                ["date", "clicks"], ascending=[True, False]
            ),
            use_container_width=True, hide_index=True,
        )

    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# BOT CLICKS VIEW (bypass.py CSVs)
# ══════════════════════════════════════════════════════════════════════════
st.caption(f"Source: `{CSV_GLOB}`")

data = load_data(CSV_GLOB)

if data.empty:
    st.info(
        "No report data found yet.\n\n"
        f"Run `bypass.py` so it writes CSVs into `{REPORT_DIR}/`, then refresh."
    )
    st.stop()

# Does the loaded data actually contain per-page detail?
pages_available = sorted(data["page"].unique())
has_page_detail = not (len(pages_available) == 1 and pages_available[0] == "all")

# ---- Sidebar filters ------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    engines = sorted(data["engine"].unique())
    picked_engines = st.multiselect("Search engines", engines, default=engines)

    if has_page_detail:
        picked_pages = st.multiselect("Pages", pages_available, default=pages_available)
    else:
        picked_pages = pages_available  # only "all"

    min_d, max_d = data["date"].min().date(), data["date"].max().date()
    if min_d == max_d:
        date_range = (min_d, max_d)
        st.write(f"Date: **{min_d}** (single day of data)")
    else:
        date_range = st.date_input(
            "Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d
        )
        if not (isinstance(date_range, tuple) and len(date_range) == 2):
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
by_engine_series = view.groupby("engine")["clicks"].sum().sort_values(ascending=False)
top_engine = by_engine_series.index[0] if not by_engine_series.empty else "—"
avg_per_day = total_clicks / n_days if n_days else 0

cols = st.columns(5 if has_page_detail else 4)
cols[0].metric("Total clicks", f"{total_clicks:,}")
cols[1].metric("Days tracked", n_days)
cols[2].metric("Top engine", top_engine)
cols[3].metric("Avg clicks / day", f"{avg_per_day:.1f}")
if has_page_detail:
    by_page_series = view.groupby("page")["clicks"].sum().sort_values(ascending=False)
    top_page = by_page_series.index[0] if not by_page_series.empty else "—"
    cols[4].metric("Top page", top_page)

st.divider()

# ---- Charts ---------------------------------------------------------------
left, right = st.columns(2)

# Clicks by engine (bar)
by_engine = (
    view.groupby("engine", as_index=False)["clicks"].sum().sort_values("clicks", ascending=False)
)
fig_bar = px.bar(
    by_engine, x="engine", y="clicks", color="engine",
    title="Clicks by Search Engine", text="clicks",
)
fig_bar.update_traces(textposition="outside")
fig_bar.update_layout(showlegend=False)
left.plotly_chart(fig_bar, use_container_width=True)

# Traffic share (pie)
fig_pie = px.pie(
    by_engine, names="engine", values="clicks",
    title="Traffic Share by Engine", hole=0.4,
)
right.plotly_chart(fig_pie, use_container_width=True)

# Per-page charts — only when page detail exists
if has_page_detail:
    l2, r2 = st.columns(2)

    by_page = (
        view.groupby("page", as_index=False)["clicks"].sum().sort_values("clicks", ascending=False)
    )
    fig_page = px.bar(
        by_page, x="page", y="clicks", color="page",
        title="Clicks by Page", text="clicks",
    )
    fig_page.update_traces(textposition="outside")
    fig_page.update_layout(showlegend=False)
    l2.plotly_chart(fig_page, use_container_width=True)

    # Page × engine breakdown (grouped bar)
    page_engine = view.groupby(["page", "engine"], as_index=False)["clicks"].sum()
    fig_pe = px.bar(
        page_engine, x="page", y="clicks", color="engine",
        barmode="group", title="Clicks by Page × Engine",
    )
    r2.plotly_chart(fig_pe, use_container_width=True)

# Clicks over time (line) — meaningful with multiple days
if n_days > 1:
    color_dim = "page" if has_page_detail else "engine"
    over_time = view.groupby(["date", color_dim], as_index=False)["clicks"].sum()
    fig_line = px.line(
        over_time, x="date", y="clicks", color=color_dim, markers=True,
        title=f"Clicks Over Time (by {color_dim})",
    )
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("Only one day of data available — the time-series chart appears once more daily reports accumulate.")

# ---- Raw data -------------------------------------------------------------
with st.expander("View raw data"):
    st.dataframe(
        view.assign(date=view["date"].dt.date).sort_values(
            ["date", "clicks"], ascending=[True, False]
        ),
        use_container_width=True,
        hide_index=True,
    )

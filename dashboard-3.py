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

# LLM visibility modules (citations / referrals / crawlers) — all optional.
try:
    import llm_visibility as llmv
    import llm_referrals as llmr
    import llm_crawlers as llmc
    LLM_AVAILABLE = True
except Exception:
    LLM_AVAILABLE = False

# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────

REPORT_DIR = os.path.join("seo_reports", "imperiumai_ai")  # matches Site.slug
CSV_GLOB = os.path.join(REPORT_DIR, "traffic_generated_*.csv")
LLM_VIS_GLOB = os.path.join(REPORT_DIR, "llm_visibility_*.csv")
DEFAULT_LOG_DIR = "imperiumai.ai-ssl_log"  # where access logs live (for referrals + crawlers)

st.set_page_config(page_title="imperiumai.ai · SEO Traffic", layout="wide")


# ──────────────────────────────────────────────────────────────────────────
# GSC live loader
# ──────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Fetching Google Search Console …")
def load_gsc(start: str, end: str, by_query: bool) -> pd.DataFrame:
    """Cached GSC fetch (tracked pages only)."""
    return fetch_gsc(start=start, end=end, by_query=by_query, tracked_only=True)


# ---- LLM visibility loaders -----------------------------------------------

@st.cache_data(show_spinner=False)
def load_llm_citations(pattern: str) -> pd.DataFrame:
    """Load saved LLM citation-tracking CSVs (from llm_visibility.run_visibility)."""
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        try:
            frames.append(pd.read_csv(f))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("mentioned", "cited", "rank", "n_citations"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df.dropna(subset=["date"])


@st.cache_data(show_spinner="Querying LLM providers …")
def run_llm_citations(providers: tuple[str, ...]) -> pd.DataFrame:
    """Live citation probe across the selected providers."""
    df = llmv.run_visibility(providers=list(providers))
    if not df.empty:
        try:
            llmv.save_report(df)
        except Exception:
            pass
    return df


@st.cache_data(show_spinner="Parsing access logs …")
def load_referrals(log_path: str) -> pd.DataFrame:
    return llmr.parse_referrals(log_path)


@st.cache_data(show_spinner="Parsing access logs …")
def load_crawlers(log_path: str) -> pd.DataFrame:
    return llmc.parse_crawlers(log_path)


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
if LLM_AVAILABLE:
    source_options.append("LLM visibility (ChatGPT / Claude / Perplexity / Gemini)")

with st.sidebar:
    st.header("Data source")
    source = st.radio("Show data from", source_options, index=0)
    if not GSC_AVAILABLE:
        st.caption("GSC live source unavailable — `gsc_fetch.py` / its Google "
                   "libraries could not be imported.")
    if not LLM_AVAILABLE:
        st.caption("LLM visibility unavailable — `llm_visibility.py` / "
                   "`llm_referrals.py` / `llm_crawlers.py` could not be imported.")
    st.divider()

USE_GSC = source.startswith("Google Search Console")
USE_LLM = source.startswith("LLM visibility")


# ══════════════════════════════════════════════════════════════════════════
# LLM VISIBILITY VIEW  (citations · referrals · crawlers)
# ══════════════════════════════════════════════════════════════════════════
if USE_LLM:
    st.caption("How ChatGPT, Claude, Perplexity & Gemini see imperiumai.ai")
    tab_cite, tab_ref, tab_crawl = st.tabs(
        ["🔎 Citations (live)", "↩️ Referral traffic", "🤖 AI crawlers"]
    )

    # ---------------------------------------------------------------- Citations
    with tab_cite:
        avail = llmv.available_providers()
        prov_labels = {"openai": "OpenAI (ChatGPT)", "anthropic": "Anthropic (Claude)",
                       "perplexity": "Perplexity", "gemini": "Google (Gemini)"}
        ready = [p for p, ok in avail.items() if ok]

        with st.sidebar:
            st.header("Citation options")
            st.caption("Provider keys detected in secrets:")
            for p, ok in avail.items():
                st.write(f"{'✅' if ok else '⬜'} {prov_labels[p]}")
            picked = st.multiselect(
                "Query which providers?",
                options=list(prov_labels),
                default=ready or list(prov_labels),
                format_func=lambda p: prov_labels[p],
            )
            run_live = st.button("▶️ Run live citation check",
                                 disabled=not ready,
                                 help=None if ready else "Add at least one provider key to secrets")
            st.button("🔄 Clear cache", on_click=run_llm_citations.clear)

        st.markdown(
            "**Citation tracking** asks each model your tracked keywords as "
            "questions and checks whether **imperiumai.ai** shows up in the "
            "answer text (mentioned) or the model's cited sources (cited)."
        )

        cdf = pd.DataFrame()
        if run_live and picked:
            cdf = run_llm_citations(tuple(picked))
            if cdf.empty and cdf.attrs.get("error"):
                st.error(cdf.attrs["error"])
        else:
            cdf = load_llm_citations(LLM_VIS_GLOB)
            if cdf.empty:
                st.info("No saved citation runs yet. Add provider keys to "
                        "secrets and click **Run live citation check**, or run "
                        "`python llm_visibility.py` to generate a report CSV.")

        if not cdf.empty:
            # latest date only for the headline KPIs
            latest = cdf[cdf["date"] == cdf["date"].max()]
            n_probes = len(latest)
            mention_rate = 100 * latest["mentioned"].mean() if n_probes else 0
            cite_rate = 100 * latest["cited"].mean() if n_probes else 0
            cited_ranks = latest.loc[latest["rank"] > 0, "rank"]
            avg_rank = cited_ranks.mean() if len(cited_ranks) else 0

            st.caption(f"Latest run: {cdf['date'].max().date()} · {n_probes} probes")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Probes", f"{n_probes}")
            c2.metric("Mention rate", f"{mention_rate:.0f}%")
            c3.metric("Citation rate", f"{cite_rate:.0f}%")
            c4.metric("Avg cite rank", f"{avg_rank:.1f}" if avg_rank else "—")
            st.divider()

            left, right = st.columns(2)
            # Citation rate by provider
            by_prov = latest.groupby("provider", as_index=False).agg(
                mention_rate=("mentioned", "mean"), cite_rate=("cited", "mean"))
            by_prov[["mention_rate", "cite_rate"]] *= 100
            fig_p = px.bar(
                by_prov.melt(id_vars="provider", value_vars=["mention_rate", "cite_rate"],
                             var_name="metric", value_name="pct"),
                x="provider", y="pct", color="metric", barmode="group",
                title="Mention & Citation Rate by LLM (%)",
            )
            left.plotly_chart(fig_p, use_container_width=True)

            # Citation rate by page
            by_pg = latest.groupby("page", as_index=False)["cited"].mean()
            by_pg["cited"] *= 100
            fig_pg = px.bar(by_pg, x="page", y="cited", color="page",
                            text="cited", title="Citation Rate by Page (%)")
            fig_pg.update_traces(texttemplate="%{text:.0f}%", textposition="outside")
            fig_pg.update_layout(showlegend=False)
            right.plotly_chart(fig_pg, use_container_width=True)

            # provider × page heatmap of citation rate
            pivot = (latest.pivot_table(index="provider", columns="page",
                                        values="cited", aggfunc="mean") * 100).round(0)
            if not pivot.empty:
                fig_hm = px.imshow(pivot, text_auto=True, aspect="auto",
                                   color_continuous_scale="Blues",
                                   title="Citation Rate — Provider × Page (%)")
                st.plotly_chart(fig_hm, use_container_width=True)

            # trend over time if multiple dates
            if cdf["date"].nunique() > 1:
                trend = cdf.groupby(["date", "provider"], as_index=False)["cited"].mean()
                trend["cited"] *= 100
                fig_t = px.line(trend, x="date", y="cited", color="provider",
                                markers=True, title="Citation Rate Over Time (%)")
                st.plotly_chart(fig_t, use_container_width=True)

            with st.expander("View raw citation data"):
                show = latest.copy()
                show["date"] = show["date"].dt.date
                keep = [c for c in ["date", "page", "keyword", "provider", "model",
                                    "mentioned", "cited", "rank", "n_citations", "answer"]
                        if c in show.columns]
                st.dataframe(show[keep], use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------- Referrals
    with tab_ref:
        with st.sidebar:
            st.header("Log source")
            log_path = st.text_input("Access-log file or folder", value=DEFAULT_LOG_DIR,
                                     key="llm_log_path")
            st.button("🔄 Re-parse logs",
                      on_click=lambda: (load_referrals.clear(), load_crawlers.clear()))

        st.markdown("**Referral traffic** = real visits whose HTTP referrer is an "
                    "LLM product (chatgpt.com, perplexity.ai, claude.ai, gemini…).")
        rdf = load_referrals(log_path)
        if rdf.empty:
            st.info(rdf.attrs.get("error", "No referral data."))
        else:
            total = int(rdf["visits"].sum())
            top_src = rdf.groupby("source")["visits"].sum().idxmax()
            k1, k2, k3 = st.columns(3)
            k1.metric("LLM referral visits", f"{total:,}")
            k2.metric("Top LLM source", top_src)
            k3.metric("Sources seen", f"{rdf['source'].nunique()}")
            st.divider()

            l, r = st.columns(2)
            by_src = rdf.groupby("source", as_index=False)["visits"].sum()
            l.plotly_chart(px.bar(by_src, x="source", y="visits", color="source",
                                  title="Visits by LLM Source").update_layout(showlegend=False),
                           use_container_width=True)
            by_pg = rdf.groupby("page", as_index=False)["visits"].sum()
            r.plotly_chart(px.pie(by_pg, names="page", values="visits", hole=0.45,
                                  title="Referral Visits by Page"),
                           use_container_width=True)
            over = rdf.groupby(["date", "source"], as_index=False)["visits"].sum()
            st.plotly_chart(px.line(over, x="date", y="visits", color="source",
                                    markers=True, title="LLM Referral Visits Over Time"),
                            use_container_width=True)
            with st.expander("View raw referral data"):
                st.dataframe(rdf, use_container_width=True, hide_index=True)

    # ----------------------------------------------------------------- Crawlers
    with tab_crawl:
        log_path = st.session_state.get("llm_log_path", DEFAULT_LOG_DIR)
        st.markdown("**AI crawlers** = hits from bot user-agents like GPTBot, "
                    "OAI-SearchBot, ClaudeBot, PerplexityBot, Google-Extended. "
                    "Shows whether the models are indexing you at all.")
        wdf = load_crawlers(log_path)
        if wdf.empty:
            st.info(wdf.attrs.get("error", "No crawler data."))
        else:
            total = int(wdf["hits"].sum())
            top_bot = wdf.groupby("bot")["hits"].sum().idxmax()
            k1, k2, k3 = st.columns(3)
            k1.metric("AI-crawler hits", f"{total:,}")
            k2.metric("Most active bot", top_bot)
            k3.metric("Bots seen", f"{wdf['bot'].nunique()}")
            st.divider()

            l, r = st.columns(2)
            by_bot = wdf.groupby(["bot", "vendor"], as_index=False)["hits"].sum()
            l.plotly_chart(px.bar(by_bot, x="bot", y="hits", color="vendor",
                                  title="Crawler Hits by Bot"),
                           use_container_width=True)
            by_vendor = wdf.groupby("vendor", as_index=False)["hits"].sum()
            r.plotly_chart(px.pie(by_vendor, names="vendor", values="hits", hole=0.45,
                                  title="Crawl Share by Vendor"),
                           use_container_width=True)
            over = wdf.groupby(["date", "vendor"], as_index=False)["hits"].sum()
            st.plotly_chart(px.line(over, x="date", y="hits", color="vendor",
                                    markers=True, title="AI-Crawler Hits Over Time"),
                            use_container_width=True)
            with st.expander("View raw crawler data"):
                st.dataframe(wdf, use_container_width=True, hide_index=True)

    st.stop()


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

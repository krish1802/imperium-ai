"""
Google Search Console (GSC) fetcher for the imperiumai.ai dashboard.

Pulls REAL search performance (clicks / impressions / CTR / avg position) per
page and per query, straight from your Google Search Console property, and
buckets each URL into the same page groups the bot tracker uses
(/view-post/* and /my-profile/*) via sites_config.match_page().

This is the "live" data source for the dashboard: it queries GSC on demand and
returns a tidy pandas DataFrame that the dashboard concatenates with the bot
CSV data (each row is labelled source="gsc" vs source="bot").

────────────────────────────────────────────────────────────────────────────
AUTH (one-time setup)
────────────────────────────────────────────────────────────────────────────
The Streamlit dashboard is a normal local Python process, so it talks to
Google directly (not through any third-party connector). Two supported modes:

1. Service account (recommended for a dashboard — no interactive login):
     - In Google Cloud Console: create a project, enable "Google Search
       Console API", create a Service Account, and download its JSON key.
     - In Search Console → Settings → Users and permissions, add the service
       account's email (…@….iam.gserviceaccount.com) as a Full/Restricted user
       on the https://imperiumai.ai/ property.
     - Point the dashboard at the key file via env var:
           export GSC_SERVICE_ACCOUNT_FILE=/path/to/service_account.json

2. OAuth client (uses your own Google login):
     - Download an OAuth client_secret.json (Desktop app) from Google Cloud.
     - export GSC_OAUTH_CLIENT_FILE=/path/to/client_secret.json
     - First run opens a browser to authorise; the token is cached to
       gsc_token.json for subsequent runs.

Requirements:
    pip install google-api-python-client google-auth google-auth-oauthlib pandas
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd

from sites_config import get_site

# ── Config ──────────────────────────────────────────────────────────────

SITE_URL = "https://imperiumai.ai/"      # URL-prefix property (exact string)
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# GSC data typically lags ~2 days; never request past this.
GSC_LAG_DAYS = 2

# Tidy schema shared with the bot data so the dashboard can concat both.
COLUMNS = ["date", "site", "page", "url", "query", "source",
           "clicks", "impressions", "ctr", "position"]


# ── Auth / service construction ─────────────────────────────────────────

def _service_account_info_from_secrets():
    """Return a service-account info dict from Streamlit secrets, or None.

    Supports either layout in .streamlit/secrets.toml:

        # (a) a dedicated table
        [gsc_service_account]
        type = "service_account"
        project_id = "..."
        private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
        client_email = "...@....iam.gserviceaccount.com"
        ...

        # (b) same fields under [gcp_service_account] (Streamlit's common name)
    """
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        secrets = st.secrets
    except Exception:
        return None

    for key in ("gsc_service_account", "gcp_service_account", "service_account"):
        if key in secrets:
            info = dict(secrets[key])
            # TOML often escapes newlines in the private key; normalize them.
            pk = info.get("private_key", "")
            if "\\n" in pk and "\n" not in pk:
                info["private_key"] = pk.replace("\\n", "\n")
            return info
    return None


def _build_service():
    """Build an authenticated Search Console API client.

    Auth precedence:
      1. Streamlit secrets  (st.secrets['gsc_service_account'] / 'gcp_service_account')
      2. Service-account key file (GSC_SERVICE_ACCOUNT_FILE)
      3. OAuth client file (GSC_OAUTH_CLIENT_FILE)
    Raises a clear error if none is configured.
    """
    from googleapiclient.discovery import build

    # 1) Streamlit secrets (inline service-account JSON) -----------------
    sa_info = _service_account_info_from_secrets()
    if sa_info:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    sa_file = os.environ.get("GSC_SERVICE_ACCOUNT_FILE")
    oauth_file = os.environ.get("GSC_OAUTH_CLIENT_FILE")

    if sa_file:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=SCOPES
        )
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    if oauth_file:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        token_path = os.environ.get("GSC_TOKEN_FILE", "gsc_token.json")
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(oauth_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    raise RuntimeError(
        "No Google credentials configured. Add a [gsc_service_account] table to "
        ".streamlit/secrets.toml, or set GSC_SERVICE_ACCOUNT_FILE / "
        "GSC_OAUTH_CLIENT_FILE. See the docstring in gsc_fetch.py for setup steps."
    )


# ── Date helpers ────────────────────────────────────────────────────────

def default_range(days: int = 30) -> tuple[str, str]:
    """(start, end) ISO strings ending GSC_LAG_DAYS ago, spanning `days`."""
    end = date.today() - timedelta(days=GSC_LAG_DAYS)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# ── Core query ──────────────────────────────────────────────────────────

def _query(service, start: str, end: str, dimensions: list[str],
           row_limit: int = 25000) -> list[dict]:
    """Run one Search Analytics query, paging until all rows are collected."""
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
            "type": "web",
            "dataState": "all",
        }
        resp = (
            service.searchanalytics()
            .query(siteUrl=SITE_URL, body=body)
            .execute()
        )
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < row_limit:
            break
        start_row += row_limit
    return rows


def _to_frame(raw_rows: list[dict], dimensions: list[str]) -> pd.DataFrame:
    """Turn GSC's keyed rows into a tidy, page-bucketed DataFrame."""
    site = get_site("imperiumai.ai")
    records = []
    for r in raw_rows:
        keys = dict(zip(dimensions, r.get("keys", [])))
        url = keys.get("page", "")
        # Bucket the URL into a tracked page group (view_post / profile),
        # or "(other)" for everything else on the site.
        page_label = site.match_page(url) if url else None
        records.append({
            "date": keys.get("date"),
            "site": site.domain,
            "page": page_label or "(other)",
            "url": url,
            "query": keys.get("query", ""),
            "source": "gsc",
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0.0),
            "position": r.get("position", 0.0),
        })
    df = pd.DataFrame.from_records(records, columns=COLUMNS)
    if not df.empty and "date" in dimensions:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# ── Public API used by the dashboard ────────────────────────────────────

def fetch_gsc(start: str | None = None, end: str | None = None,
              by_query: bool = False,
              tracked_only: bool = True) -> pd.DataFrame:
    """Fetch GSC performance data as a tidy DataFrame.

    Args:
        start, end : ISO dates. Defaults to the last 30 days (minus GSC lag).
        by_query   : also break rows down by search query.
        tracked_only : keep only /view-post/* and /my-profile/* pages.

    Returns a DataFrame with COLUMNS. Empty DataFrame if credentials are
    missing or the API errors (dashboard degrades gracefully).
    """
    if start is None or end is None:
        start, end = default_range()

    dimensions = ["date", "page"] + (["query"] if by_query else [])

    try:
        service = _build_service()
        raw = _query(service, start, end, dimensions)
    except Exception as exc:  # surface as empty frame; dashboard shows a hint
        df = pd.DataFrame(columns=COLUMNS)
        df.attrs["error"] = str(exc)
        return df

    df = _to_frame(raw, dimensions)
    if tracked_only and not df.empty:
        df = df[df["page"].isin(list(get_site("imperiumai.ai").pages))]
    return df.reset_index(drop=True)


if __name__ == "__main__":
    s, e = default_range()
    print(f"Fetching GSC {s} → {e} for {SITE_URL} ...")
    frame = fetch_gsc(s, e, by_query=False, tracked_only=False)
    if frame.empty and frame.attrs.get("error"):
        print("ERROR:", frame.attrs["error"])
    else:
        print(f"{len(frame)} rows")
        print(frame.groupby("page")[["clicks", "impressions"]].sum())

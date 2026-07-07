# Google Search Console → Dashboard setup

The dashboard now has two data sources, switchable in the sidebar:

1. **Bot clicks (bypass.py)** — your existing search-result click counter.
2. **Google Search Console (live)** — real clicks, impressions, CTR, and
   average position pulled straight from Google for `https://imperiumai.ai/`.

Both use the same page buckets (`/view-post/*`, `/my-profile/*`) so the two
views line up.

---

## Why the dashboard talks to Google directly

Your Streamlit dashboard is a plain local Python process. It can't reach the
Perplexity Google Search Console connector — that connector lives inside
Perplexity. So for a self-contained dashboard, `gsc_fetch.py` calls Google's
**official Search Console API** using your own credentials. Set it up once and
the "GSC (live)" source works forever.

---

## 1. Install dependencies

```bash
pip install streamlit pandas plotly \
    google-api-python-client google-auth google-auth-oauthlib
```

## 2. Enable the API in Google Cloud

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create (or pick) a project.
3. **APIs & Services → Library → search "Google Search Console API" → Enable.**

## 3. Pick an auth method

### Option A — Service account (recommended for a dashboard)

No interactive login; ideal for an always-on dashboard.

1. **APIs & Services → Credentials → Create credentials → Service account.**
2. Create it, then under the service account **Keys → Add key → JSON**.
   Download the JSON file.
3. Copy the service account email (looks like
   `something@your-project.iam.gserviceaccount.com`).
4. In **Search Console → Settings → Users and permissions → Add user**, add
   that email with **Full** (or Restricted) permission on the
   `https://imperiumai.ai/` property.
5. Point the dashboard at the key:

   ```bash
   export GSC_SERVICE_ACCOUNT_FILE=/absolute/path/to/service_account.json
   ```

### Option B — OAuth (uses your own Google login)

1. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Desktop app.** Download `client_secret.json`.
2. Set:

   ```bash
   export GSC_OAUTH_CLIENT_FILE=/absolute/path/to/client_secret.json
   ```
3. First run opens a browser to authorise; the token is cached to
   `gsc_token.json` next to the scripts (override with `GSC_TOKEN_FILE`).

## 4. Run

```bash
export GSC_SERVICE_ACCOUNT_FILE=/path/to/service_account.json   # or OAuth var
streamlit run dashboard-3.py
```

Quick sanity check without the dashboard:

```bash
python gsc_fetch.py
```
This prints per-page click/impression totals for the last 30 days.

---

## How it works

- `gsc_fetch.py`
  - `fetch_gsc(start, end, by_query, tracked_only)` → tidy DataFrame with
    columns `date, site, page, url, query, source, clicks, impressions, ctr,
    position`.
  - Each URL is bucketed with `sites_config.match_page()` — the same logic the
    bot tracker uses — so GSC pages map to `view_post` / `profile`.
  - Pages GSC lags ~2 days behind; `default_range()` accounts for that.
- `dashboard-3.py`
  - Sidebar **Data source** radio switches between Bot and GSC.
  - GSC view shows KPI cards (Clicks, Impressions, **CTR**, **Avg position** —
    CTR/position are impression-weighted, not naive averages), per-page bars,
    a per-page time series, and an optional **Top Queries** chart when you tick
    "Break down by search query".
  - Live results are cached for 1 hour; **Refresh GSC** clears the cache.

## Notes / gotchas

- If you see "Could not fetch Google Search Console data", the credential env
  var is unset or the account lacks access to the property — re-check step 3.
- `tracked_only=True` keeps just `/view-post/*` and `/my-profile/*`. Set it to
  `False` in `gsc_fetch.py`'s call if you want every page.
- The property string must match exactly what's verified in Search Console:
  the URL-prefix `https://imperiumai.ai/` (with trailing slash). If you ever
  switch to a Domain property, change `SITE_URL` in `gsc_fetch.py` to
  `sc-domain:imperiumai.ai`.

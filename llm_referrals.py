"""
LLM Referral Traffic parser for imperiumai.ai
=============================================

Measures *real human visits* that arrived from an LLM product — i.e. requests
whose HTTP ``Referer`` is chatgpt.com, perplexity.ai, claude.ai, gemini, etc.
GSC does not expose these, so this reads your web-server access logs.

Supported log formats (auto-detected line by line):
  * Combined / common Apache-Nginx log:
        1.2.3.4 - - [10/Jul/2026:12:00:00 +0000] "GET /view-post/ HTTP/1.1"
        200 1234 "https://chatgpt.com/" "Mozilla/5.0 ..."
  * JSON-per-line logs with keys like: time/timestamp, request/path,
    referer/referrer, status.

Point it at a file or a directory (all *.log / *.log.gz / *.json inside).

Output schema (DataFrame + optional CSV
  seo_reports/imperiumai_ai/llm_referrals_YYYY-MM-DD.csv):
    date, site, page, source, referrer_host, path, visits
"""

from __future__ import annotations

import datetime as _dt
import glob
import gzip
import json
import os
import re
from typing import Iterable, Optional

import pandas as pd

from sites_config import get_site

SITE = get_site("imperiumai.ai")
DOMAIN = SITE.domain
SLUG = SITE.slug
REPORT_DIR = os.path.join("seo_reports", SLUG)

# referrer host substring -> friendly LLM source label
LLM_REFERRERS: dict[str, str] = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity",
    "pplx.ai": "Perplexity",
    "claude.ai": "Claude",
    "anthropic.com": "Claude",
    "gemini.google.com": "Gemini",
    "bard.google.com": "Gemini",
    "copilot.microsoft.com": "Copilot",
    "bing.com/chat": "Copilot",
    "you.com": "You.com",
    "poe.com": "Poe",
}

_COMBINED_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) [^"]*" '
    r'(?P<status>\d{3}) \S+ '
    r'"(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)

_APACHE_TIME = "%d/%b/%Y:%H:%M:%S %z"


# ── log source iteration ────────────────────────────────────────────────

def _iter_lines(path: str) -> Iterable[str]:
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as fh:
            for line in fh:
                yield line.rstrip("\n")
    except Exception:
        return


def _log_files(source: str) -> list[str]:
    if os.path.isdir(source):
        out = []
        for pat in ("*.log", "*.log.*", "*.gz", "*.json", "*.txt"):
            out += glob.glob(os.path.join(source, pat))
        return sorted(set(out))
    return [source] if os.path.isfile(source) else []


# ── line parsing ────────────────────────────────────────────────────────

def _parse_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    # JSON line?
    if line.startswith("{"):
        try:
            obj = json.loads(line)
        except Exception:
            return None
        ref = obj.get("referer") or obj.get("referrer") or obj.get("http_referer") or ""
        path = obj.get("path") or obj.get("request") or obj.get("uri") or ""
        ts = obj.get("time") or obj.get("timestamp") or obj.get("@timestamp") or ""
        return {"referer": str(ref), "path": str(path).split(" ")[0], "time": str(ts)}
    m = _COMBINED_RE.search(line)
    if not m:
        return None
    return {"referer": m.group("referer"), "path": m.group("path"), "time": m.group("time")}


def _classify_referrer(referer: str) -> Optional[tuple[str, str]]:
    """Return (source_label, referrer_host) if it's an LLM referrer, else None."""
    r = (referer or "").lower()
    if not r or r == "-":
        return None
    for needle, label in LLM_REFERRERS.items():
        if needle in r:
            host = re.sub(r"^https?://", "", r).split("/")[0]
            return label, host
    return None


def _parse_date(raw: str) -> str:
    if not raw:
        return _dt.date.today().isoformat()
    # Apache time
    try:
        return _dt.datetime.strptime(raw, _APACHE_TIME).date().isoformat()
    except Exception:
        pass
    # ISO-ish
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return _dt.date.today().isoformat()


# ── public API ──────────────────────────────────────────────────────────

def parse_referrals(source: str) -> pd.DataFrame:
    """Parse one or more access logs into an LLM-referral DataFrame.

    Empty frame with ``.attrs['error']`` if the path yields nothing usable.
    """
    cols = ["date", "site", "page", "source", "referrer_host", "path", "visits"]
    files = _log_files(source)
    if not files:
        df = pd.DataFrame(columns=cols)
        df.attrs["error"] = f"No log files found at: {source}"
        return df

    rows = []
    for f in files:
        for line in _iter_lines(f):
            rec = _parse_line(line)
            if not rec:
                continue
            hit = _classify_referrer(rec["referer"])
            if not hit:
                continue
            label, host = hit
            path = rec["path"] or "/"
            page = SITE.match_page(path) or SITE.match_page(f"https://{DOMAIN}{path}") or "other"
            rows.append({
                "date": _parse_date(rec["time"]),
                "site": DOMAIN, "page": page,
                "source": label, "referrer_host": host,
                "path": path, "visits": 1,
            })

    if not rows:
        df = pd.DataFrame(columns=cols)
        df.attrs["error"] = (
            f"Parsed {len(files)} file(s) but found no LLM referrals "
            "(no chatgpt.com / perplexity.ai / claude.ai / gemini referers)."
        )
        return df

    df = pd.DataFrame(rows)
    df = (df.groupby(["date", "site", "page", "source", "referrer_host", "path"],
                     as_index=False)["visits"].sum()
            .sort_values(["date", "visits"], ascending=[True, False]))
    return df


def save_report(df: pd.DataFrame, base: str = "seo_reports") -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = _dt.date.today().isoformat()
    path = os.path.join(REPORT_DIR, f"llm_referrals_{date}.csv")
    df.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join("logs", "access.log")
    frame = parse_referrals(src)
    if frame.empty:
        print("No data:", frame.attrs.get("error"))
    else:
        print(f"{frame['visits'].sum()} LLM referral visits across {len(frame)} rows")
        print(frame.to_string(index=False))

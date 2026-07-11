"""
AI Crawler Hit parser for imperiumai.ai
=======================================

Counts how often LLM/AI crawlers fetch your pages — the "are they indexing me
at all?" signal. Reads the same web-server access logs as llm_referrals.py but
classifies by the **User-Agent** instead of the referrer.

Recognised bots (substring match on UA, case-insensitive):
    GPTBot, OAI-SearchBot, ChatGPT-User      -> OpenAI
    ClaudeBot, Claude-Web, anthropic-ai      -> Anthropic
    PerplexityBot, Perplexity-User           -> Perplexity
    Google-Extended, Googlebot (Gemini)      -> Google
    Bytespider, CCBot, Amazonbot, Applebot,  -> Other AI crawlers
    Meta-ExternalAgent, cohere-ai, Diffbot,
    YouBot, Timpibot

Output schema (DataFrame + optional CSV
  seo_reports/imperiumai_ai/llm_crawlers_YYYY-MM-DD.csv):
    date, site, page, bot, vendor, path, status, hits
"""

from __future__ import annotations

import datetime as _dt
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

# UA substring -> (bot label, vendor).  Order matters: most specific first.
AI_BOTS: list[tuple[str, str, str]] = [
    ("oai-searchbot", "OAI-SearchBot", "OpenAI"),
    ("chatgpt-user", "ChatGPT-User", "OpenAI"),
    ("gptbot", "GPTBot", "OpenAI"),
    ("claudebot", "ClaudeBot", "Anthropic"),
    ("claude-web", "Claude-Web", "Anthropic"),
    ("anthropic-ai", "anthropic-ai", "Anthropic"),
    ("perplexitybot", "PerplexityBot", "Perplexity"),
    ("perplexity-user", "Perplexity-User", "Perplexity"),
    ("google-extended", "Google-Extended", "Google"),
    ("bytespider", "Bytespider", "ByteDance"),
    ("ccbot", "CCBot", "Common Crawl"),
    ("amazonbot", "Amazonbot", "Amazon"),
    ("applebot-extended", "Applebot-Extended", "Apple"),
    ("meta-externalagent", "Meta-ExternalAgent", "Meta"),
    ("cohere-ai", "cohere-ai", "Cohere"),
    ("diffbot", "Diffbot", "Diffbot"),
    ("youbot", "YouBot", "You.com"),
    ("timpibot", "Timpibot", "Timpi"),
]

_COMBINED_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) [^"]*" '
    r'(?P<status>\d{3}) \S+ '
    r'"(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)
_APACHE_TIME = "%d/%b/%Y:%H:%M:%S %z"


def _iter_lines(path: str) -> Iterable[str]:
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as fh:
            for line in fh:
                yield line.rstrip("\n")
    except Exception:
        return


def _log_files(source: str) -> list[str]:
    import glob
    if os.path.isdir(source):
        out = []
        for pat in ("*.log", "*.log.*", "*.gz", "*.json", "*.txt"):
            out += glob.glob(os.path.join(source, pat))
        return sorted(set(out))
    return [source] if os.path.isfile(source) else []


def _parse_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            obj = json.loads(line)
        except Exception:
            return None
        ua = obj.get("ua") or obj.get("user_agent") or obj.get("http_user_agent") or ""
        path = obj.get("path") or obj.get("request") or obj.get("uri") or ""
        ts = obj.get("time") or obj.get("timestamp") or obj.get("@timestamp") or ""
        status = str(obj.get("status") or obj.get("code") or "")
        return {"ua": str(ua), "path": str(path).split(" ")[0],
                "time": str(ts), "status": status}
    m = _COMBINED_RE.search(line)
    if not m:
        return None
    return {"ua": m.group("ua"), "path": m.group("path"),
            "time": m.group("time"), "status": m.group("status")}


def _classify_bot(ua: str) -> Optional[tuple[str, str]]:
    u = (ua or "").lower()
    if not u:
        return None
    for needle, label, vendor in AI_BOTS:
        if needle in u:
            return label, vendor
    return None


def _parse_date(raw: str) -> str:
    if not raw:
        return _dt.date.today().isoformat()
    try:
        return _dt.datetime.strptime(raw, _APACHE_TIME).date().isoformat()
    except Exception:
        pass
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return _dt.date.today().isoformat()


def parse_crawlers(source: str) -> pd.DataFrame:
    cols = ["date", "site", "page", "bot", "vendor", "path", "status", "hits"]
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
            hit = _classify_bot(rec["ua"])
            if not hit:
                continue
            label, vendor = hit
            path = rec["path"] or "/"
            page = SITE.match_page(path) or SITE.match_page(f"https://{DOMAIN}{path}") or "other"
            rows.append({
                "date": _parse_date(rec["time"]),
                "site": DOMAIN, "page": page,
                "bot": label, "vendor": vendor,
                "path": path, "status": rec.get("status") or "",
                "hits": 1,
            })

    if not rows:
        df = pd.DataFrame(columns=cols)
        df.attrs["error"] = (
            f"Parsed {len(files)} file(s) but found no AI-crawler hits "
            "(no GPTBot / ClaudeBot / PerplexityBot / Google-Extended user-agents)."
        )
        return df

    df = pd.DataFrame(rows)
    df = (df.groupby(["date", "site", "page", "bot", "vendor", "path", "status"],
                     as_index=False)["hits"].sum()
            .sort_values(["date", "hits"], ascending=[True, False]))
    return df


def save_report(df: pd.DataFrame, base: str = "seo_reports") -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = _dt.date.today().isoformat()
    path = os.path.join(REPORT_DIR, f"llm_crawlers_{date}.csv")
    df.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join("logs", "access.log")
    frame = parse_crawlers(src)
    if frame.empty:
        print("No data:", frame.attrs.get("error"))
    else:
        print(f"{frame['hits'].sum()} AI-crawler hits across {len(frame)} rows")
        print(frame.to_string(index=False))

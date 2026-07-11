"""
LLM Visibility / Citation Tracker for imperiumai.ai
====================================================

The "search engine" analog for LLMs. Traditional engines (Google/Yahoo/Bing)
expose a clickable SERP that bypass.py can bot. LLMs do NOT — so instead we
*ask each model your tracked keywords as questions* and detect whether
imperiumai.ai shows up in the answer text or the model's cited sources.

For each (keyword, provider) pair we record:
    mentioned  — did the domain/brand appear anywhere in the answer text?
    cited      — did imperiumai.ai appear as a source/citation URL?
    rank       — 1-based position of the first imperiumai.ai citation (0 = none)
    n_citations— how many sources the model returned
    answer     — the raw answer text (truncated) for auditing

Providers (all optional — each degrades gracefully if its key/lib is missing):
    OpenAI      (ChatGPT)        — OPENAI_API_KEY
    Anthropic   (Claude)         — ANTHROPIC_API_KEY
    Perplexity  (sonar)          — PPLX_API_KEY / PERPLEXITY_API_KEY
    Google      (Gemini)         — GEMINI_API_KEY / GOOGLE_API_KEY

Keys are read from Streamlit secrets first (st.secrets), then environment
variables. Nothing here needs the site to be a WordPress site.

Output CSV schema  (seo_reports/imperiumai_ai/llm_visibility_YYYY-MM-DD.csv):
    date, site, page, keyword, provider, model,
    mentioned, cited, rank, n_citations, answer
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from sites_config import get_site

# ── Config ──────────────────────────────────────────────────────────────
SITE = get_site("imperiumai.ai")
DOMAIN = SITE.domain                       # imperiumai.ai
BRAND = SITE.brand_name or "Imperium AI"   # Imperium AI
SLUG = SITE.slug                           # imperiumai_ai
REPORT_DIR = os.path.join("seo_reports", SLUG)

# Default models per provider (override via secrets/env if you like).
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini-search-preview",   # search-enabled -> returns citations
    "anthropic": "claude-3-5-sonnet-latest",
    "perplexity": "sonar",                     # returns real citations
    "gemini": "gemini-2.0-flash",
}

# A neutral question wrapper so the model treats the keyword as a real query.
PROMPT_TEMPLATE = (
    "Answer the following as if a user typed it into a search assistant. "
    "Be specific and cite the websites you rely on.\n\nQuery: {keyword}"
)


# ── Secrets / key resolution ────────────────────────────────────────────

def _secret(*names: str) -> Optional[str]:
    """Return the first non-empty value found in st.secrets or os.environ."""
    # Streamlit secrets (may be absent outside a Streamlit run).
    try:
        import streamlit as st
        try:
            sec = st.secrets
            # allow a nested [llm] table too, e.g. st.secrets["llm"]["OPENAI_API_KEY"]
            for name in names:
                if name in sec:
                    val = sec[name]
                    if val:
                        return str(val)
                if "llm" in sec and name in sec["llm"]:
                    val = sec["llm"][name]
                    if val:
                        return str(val)
        except Exception:
            pass
    except Exception:
        pass
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def available_providers() -> dict[str, bool]:
    """Which providers have a key configured right now."""
    return {
        "openai": bool(_secret("OPENAI_API_KEY")),
        "anthropic": bool(_secret("ANTHROPIC_API_KEY")),
        "perplexity": bool(_secret("PPLX_API_KEY", "PERPLEXITY_API_KEY")),
        "gemini": bool(_secret("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    }


# ── Result container ────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ── Per-provider probes ─────────────────────────────────────────────────
# Each returns a ProbeResult. They must never raise — errors go in .error.

def _probe_openai(prompt: str, model: str) -> ProbeResult:
    key = _secret("OPENAI_API_KEY")
    if not key:
        return ProbeResult(error="no OPENAI_API_KEY")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        cites: list[str] = []
        # Search-preview models attach url_citation annotations.
        ann = getattr(msg, "annotations", None) or []
        for a in ann:
            url = None
            if isinstance(a, dict):
                url = (a.get("url_citation") or {}).get("url") or a.get("url")
            else:
                uc = getattr(a, "url_citation", None)
                url = getattr(uc, "url", None) if uc else getattr(a, "url", None)
            if url:
                cites.append(url)
        cites += _urls_in_text(text)
        return ProbeResult(answer=text, citations=_dedupe(cites))
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(error=f"openai: {exc}")


def _probe_anthropic(prompt: str, model: str) -> ProbeResult:
    key = _secret("ANTHROPIC_API_KEY")
    if not key:
        return ProbeResult(error="no ANTHROPIC_API_KEY")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        # Enable web search tool so Claude can actually cite live sources.
        kwargs = dict(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            kwargs["tools"] = [{"type": "web_search_20250305",
                                "name": "web_search", "max_uses": 3}]
            resp = client.messages.create(**kwargs)
        except Exception:
            # Retry without the tool if this account/model can't use it.
            kwargs.pop("tools", None)
            resp = client.messages.create(**kwargs)

        text_parts, cites = [], []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
                for c in (getattr(block, "citations", None) or []):
                    url = getattr(c, "url", None) or (c.get("url") if isinstance(c, dict) else None)
                    if url:
                        cites.append(url)
        text = "\n".join(text_parts)
        cites += _urls_in_text(text)
        return ProbeResult(answer=text, citations=_dedupe(cites))
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(error=f"anthropic: {exc}")


def _probe_perplexity(prompt: str, model: str) -> ProbeResult:
    key = _secret("PPLX_API_KEY", "PERPLEXITY_API_KEY")
    if not key:
        return ProbeResult(error="no PPLX_API_KEY")
    try:
        # Perplexity is OpenAI-API-compatible.
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.perplexity.ai")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        # citations live on the response object for sonar models.
        cites = list(getattr(resp, "citations", None) or [])
        if not cites:
            sr = getattr(resp, "search_results", None) or []
            cites = [s.get("url") for s in sr if isinstance(s, dict) and s.get("url")]
        cites += _urls_in_text(text)
        return ProbeResult(answer=text, citations=_dedupe(cites))
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(error=f"perplexity: {exc}")


def _probe_gemini(prompt: str, model: str) -> ProbeResult:
    key = _secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return ProbeResult(error="no GEMINI_API_KEY")
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
        except Exception:
            resp = client.models.generate_content(model=model, contents=prompt)
        text = getattr(resp, "text", "") or ""
        cites: list[str] = []
        for cand in (getattr(resp, "candidates", None) or []):
            gm = getattr(cand, "grounding_metadata", None)
            for chunk in (getattr(gm, "grounding_chunks", None) or []) if gm else []:
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None) if web else None
                if uri:
                    cites.append(uri)
        cites += _urls_in_text(text)
        return ProbeResult(answer=text, citations=_dedupe(cites))
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(error=f"gemini: {exc}")


_PROBES = {
    "openai": _probe_openai,
    "anthropic": _probe_anthropic,
    "perplexity": _probe_perplexity,
    "gemini": _probe_gemini,
}


# ── Detection helpers ───────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s\)\]\"'>]+", re.I)


def _urls_in_text(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _domain_in(url_or_text: str) -> bool:
    return DOMAIN.lower() in (url_or_text or "").lower()


def _brand_mentioned(text: str) -> bool:
    t = (text or "").lower()
    return DOMAIN.lower() in t or BRAND.lower() in t or "imperiumai" in t


def _first_citation_rank(citations: list[str]) -> int:
    for i, url in enumerate(citations, start=1):
        if _domain_in(url):
            return i
    return 0


# ── Public API ──────────────────────────────────────────────────────────

def probe_keyword(keyword: str, provider: str, model: Optional[str] = None) -> dict:
    """Run one keyword against one provider, return a normalized row dict."""
    model = model or DEFAULT_MODELS.get(provider, "")
    probe = _PROBES.get(provider)
    if probe is None:
        return {"error": f"unknown provider {provider}"}
    prompt = PROMPT_TEMPLATE.format(keyword=keyword)
    res = probe(prompt, model)
    cited_rank = _first_citation_rank(res.citations)
    return {
        "provider": provider,
        "model": model,
        "keyword": keyword,
        "mentioned": int(_brand_mentioned(res.answer)) if not res.error else 0,
        "cited": int(cited_rank > 0),
        "rank": cited_rank,
        "n_citations": len(res.citations),
        "answer": (res.answer or "")[:1200],
        "error": res.error or "",
    }


def run_visibility(
    providers: Optional[list[str]] = None,
    pages: Optional[list[str]] = None,
    per_page_keywords: bool = True,
) -> pd.DataFrame:
    """Probe every (page-keyword × provider) combination.

    Returns a DataFrame with the CSV schema. On total failure the frame is
    empty and carries an ``.attrs['error']`` message.
    """
    avail = available_providers()
    providers = providers or [p for p, ok in avail.items() if ok]
    if not providers:
        empty = pd.DataFrame(
            columns=["date", "site", "page", "keyword", "provider", "model",
                     "mentioned", "cited", "rank", "n_citations", "answer"]
        )
        empty.attrs["error"] = (
            "No LLM provider keys configured. Add OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY, PPLX_API_KEY, and/or GEMINI_API_KEY to "
            "Streamlit secrets."
        )
        return empty

    pages = pages or list(SITE.pages)         # ['my_profile', 'view_post']
    today = _dt.date.today().isoformat()
    rows = []
    for page in pages:
        kws = SITE.keywords_for(page) if per_page_keywords else SITE.tracked_keywords
        for kw in kws:
            for prov in providers:
                row = probe_keyword(kw, prov)
                row.update({"date": today, "site": DOMAIN, "page": page})
                rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Column order.
    cols = ["date", "site", "page", "keyword", "provider", "model",
            "mentioned", "cited", "rank", "n_citations", "answer"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols + [c for c in df.columns if c == "error"]]


def save_report(df: pd.DataFrame, base: str = "seo_reports") -> str:
    """Persist a visibility run to seo_reports/<slug>/llm_visibility_<date>.csv."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = df["date"].iloc[0] if not df.empty else _dt.date.today().isoformat()
    path = os.path.join(REPORT_DIR, f"llm_visibility_{date}.csv")
    df.to_csv(path, index=False)
    return path


# ── CLI ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Configured providers:", available_providers())
    frame = run_visibility()
    if frame.empty:
        print("No data:", frame.attrs.get("error", "empty"))
    else:
        out = save_report(frame)
        print(f"Wrote {len(frame)} rows -> {out}")
        print(frame[["page", "keyword", "provider", "mentioned", "cited", "rank"]].to_string(index=False))

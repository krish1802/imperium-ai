#!/usr/bin/env python3
"""
Click-farm / search-bot tester — multi-site (keyword-driven).

For every registered site, each PAGE has its own keyword list. The bot runs
each page's keywords as a plain search on Google / Yahoo / Bing (NO site:
operator), then clicks only the result links whose URL belongs to that page
(e.g. /my-profile/* for the my_profile keywords, /view-post/* for view_post).
Matching is enforced via Site.match_page().

Saves per-site daily totals to:
    seo_reports/<slug>/traffic_generated_YYYY-MM-DD.csv
CSV schema: date, site, page, engine, clicks

CLI:
    python bypass.py                     # all sites, all engines
    python bypass.py --site imperiumai.ai
    python bypass.py --engines google.com,bing.com
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from seleniumbase import SB

from sites_config import Site, SITES, get_site


DEFAULT_ENGINES = ["google.com", "yahoo.com", "bing.com"]

# Headless when running in CI (GitHub Actions has no display). Set SB_HEADLESS=1
# or HEADLESS=1 to force it; defaults to headful for local/interactive runs.
HEADLESS = os.environ.get("SB_HEADLESS", os.environ.get("HEADLESS", "")).lower() in (
    "1", "true", "yes"
)


# ── PER-ENGINE FLOW ─────────────────────────────────────────────────────

def _run_query(page, engine: str, query: str):
    """Type `query` into `engine` and return a locator for its result links."""
    if "google.com" in engine:
        page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=20000)
        try:
            page.locator(
                "button:has-text('I agree'), button:has-text('Accept all')"
            ).first.click(timeout=5000)
        except Exception:
            pass
        page.fill("textarea[name='q'], input[name='q']", query)
        page.keyboard.press("Enter")
        page.wait_for_selector("a h3", timeout=20000)
        return page.locator("a:has(h3)")

    if "yahoo.com" in engine:
        page.goto("https://search.yahoo.com", wait_until="domcontentloaded", timeout=20000)
        page.fill("input[name='p']", query)
        page.keyboard.press("Enter")
        page.wait_for_selector("a.ac-algo, a[ref*='result']", timeout=20000)
        return page.locator("a.ac-algo, a[ref*='result']")

    if "bing.com" in engine:
        page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=20000)
        page.fill("input[name='q']", query)
        page.keyboard.press("Enter")
        page.wait_for_selector("li.b_algo h2 a", timeout=20000)
        return page.locator("li.b_algo h2 a")

    return None


def click_matching_links(page, link_locator, site: Site, label: str) -> int:
    """Scan result links; click only those belonging to page `label`.

    A link is clicked only if Site.match_page(href) == label (i.e. the URL is
    on this domain AND under THIS page's path prefix). Returns the click count.
    """
    clicks = 0
    if link_locator is None:
        return clicks

    for i in range(link_locator.count()):
        href = link_locator.nth(i).get_attribute("href")
        if not href or site.domain not in href:
            continue
        if site.match_page(href) != label:      # must belong to THIS page
            continue
        clicks += 1
        new_page = page.context.new_page()
        try:
            new_page.goto(href, wait_until="domcontentloaded")
            new_page.wait_for_timeout(3000)
        except Exception:
            pass
        finally:
            new_page.close()
    return clicks


def run_for_engine(page, engine: str, site: Site) -> dict[str, int]:
    """For each page, search ITS OWN keywords on `engine` and count page clicks.

    Returns {page_label: clicks}.
    """
    totals: dict[str, int] = {label: 0 for label in site.pages}

    for label in site.pages:
        keywords = site.keywords_for(label)
        for kw in keywords:
            try:
                link_locator = _run_query(page, engine, kw)
                hits = click_matching_links(page, link_locator, site, label)
                totals[label] += hits
                print(f"      [{label}] kw '{kw}': {hits} click(s)")
            except Exception as e:
                print(f"      [{label}] kw '{kw}': error ({e}), skipped")
    return totals


# ── PERSISTENCE ─────────────────────────────────────────────────────────

def save_daily_clicks(site: Site, results: dict, base_output: str = "seo_reports") -> Path:
    """Append today's per-page, per-engine totals to the per-site CSV.

    `results` is nested: {page_label: {engine: clicks}}.
    CSV schema: date, site, page, engine, clicks
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    out_dir = Path(site.output_dir(base_output))
    path = out_dir / f"traffic_generated_{today}.csv"
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "site", "page", "engine", "clicks"])
        for page_label, per_engine in results.items():
            for engine, clicks in per_engine.items():
                writer.writerow([today, site.domain, page_label, engine, clicks])
    return path


# ── PER-SITE FLOW ───────────────────────────────────────────────────────

def run_site(site: Site, page, engines: list[str]) -> dict:
    """Run all keywords across every engine. Returns {page: {engine: clicks}}."""
    daily: dict = {label: {e: 0 for e in engines} for label in site.pages}
    for engine in engines:
        kw_total = sum(len(site.keywords_for(l)) for l in site.pages)
        print(f"  ▶ [{site.domain}] running {engine} across {kw_total} page-keyword(s) ...")
        try:
            per_page = run_for_engine(page, engine, site)
            for label, clicks in per_page.items():
                daily[label][engine] = clicks
            summary = ", ".join(f"{l}={c}" for l, c in per_page.items())
            print(f"    {engine}: {summary}")
        except Exception as e:
            for label in site.pages:
                daily[label][engine] = 0
            print(f"    {engine}: error ({e}), recorded 0")
    out = save_daily_clicks(site, daily)
    print(f"  💾 [{site.domain}] saved → {out}")
    return daily


# ── ENTRY ───────────────────────────────────────────────────────────────

def run_all(engines: list[str] = DEFAULT_ENGINES, only_site: str | None = None) -> dict[str, dict]:
    sites = [get_site(only_site)] if only_site else SITES
    overall: dict[str, dict] = {}

    with SB(uc=True, headless=HEADLESS) as sb:
        sb.activate_cdp_mode()
        endpoint_url = sb.cdp.get_endpoint_url()

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(endpoint_url)
            context = browser.contexts[0]
            page = context.pages[0]

            for site in sites:
                print(f"\n=== {site.domain} ===")
                try:
                    overall[site.domain] = run_site(site, page, engines)
                except Exception as e:
                    print(f"❌ [{site.domain}] failed: {e}")
                    overall[site.domain] = {
                        label: {e_: 0 for e_ in engines} for label in site.pages
                    }

            browser.close()

    return overall


def _main() -> None:
    ap = argparse.ArgumentParser(description="Multi-site keyword-driven click farm")
    ap.add_argument("--site", help="Run for one domain only")
    ap.add_argument("--engines", default=",".join(DEFAULT_ENGINES),
                    help="Comma-separated engines (default: google.com,yahoo.com,bing.com)")
    args = ap.parse_args()

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    summary = run_all(engines=engines, only_site=args.site)

    print("\n──────── CLICK-FARM SUMMARY ────────")
    for domain, per_page in summary.items():
        site_total = sum(sum(pe.values()) for pe in per_page.values())
        print(f"  {domain}  (total={site_total})")
        for page_label, per_engine in per_page.items():
            page_total = sum(per_engine.values())
            per_str = ", ".join(f"{e}={c}" for e, c in per_engine.items())
            print(f"    {page_label:12s}  total={page_total:3d}  ({per_str})")


if __name__ == "__main__":
    _main()

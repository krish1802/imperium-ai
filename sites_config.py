"""
Minimal site registry + auto-fetcher for a single (non-WordPress) website.

You only specify the website name — everything else (URL, profile page,
view-post page, filesystem-safe slug, output dir) is derived automatically.

No WordPress. No credentials. No GA4. Just the site name and a fetcher.

Usage:
    python imperiumai_site.py                       # fetch all default pages
    python imperiumai_site.py imperiumai.ai         # fetch a given site's pages
    python imperiumai_site.py imperiumai.ai /about  # fetch a single custom path
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # graceful fallback if requests isn't installed
    requests = None


# ──────────────────────────────────────────────────────────────────────────
# The ONLY thing you need to set: the website name + pages to auto-search.
# ──────────────────────────────────────────────────────────────────────────

WEBSITE = "imperiumai.ai"

# Pages used for "automatic searching" — label -> path prefix.
# Trailing slash marks these as PREFIXES: everything under the path is matched
# (i.e. /view-post/* and /my-profile/*), not just the exact page.
PAGES: dict[str, str] = {
    "profile": "/my-profile/",
    "view_post": "/view-post/",
}


# ──────────────────────────────────────────────────────────────────────────
# Site model (credential-free)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Site:
    """One website — defined purely by its name."""
    domain: str                                    # e.g. "imperiumai.ai"
    pages: dict[str, str] = field(default_factory=lambda: dict(PAGES))
    tracked_keywords: list[str] = field(default_factory=list)

    @property
    def site_url(self) -> str:
        return f"https://{self.domain.rstrip('/')}"

    def page_url(self, label: str) -> str:
        """Full URL for a named page, e.g. page_url('view_post')."""
        path = self.pages[label]
        return urljoin(self.site_url + "/", path.lstrip("/"))

    @property
    def page_urls(self) -> dict[str, str]:
        """All page labels mapped to their absolute URLs."""
        return {label: self.page_url(label) for label in self.pages}

    # Backwards-compatible convenience accessors
    @property
    def profile_url(self) -> str:
        return self.page_url("profile")

    @property
    def view_post_url(self) -> str:
        return self.page_url("view_post")

    @property
    def brand_name(self) -> str:
        """imperiumai.ai -> 'Imperiumai' (best-effort display name)."""
        root = self.domain.split(".")[0]
        return root.replace("-", " ").replace("_", " ").title()

    def page_path(self, label: str) -> str:
        """The normalized path prefix for a page, e.g. '/view-post/'."""
        path = self.pages[label]
        if not path.startswith("/"):
            path = "/" + path
        return path

    def match_page(self, url_or_path: str) -> str | None:
        """Return the page label whose path-prefix matches this URL, else None.

        Matches /view-post/* and /my-profile/* (any sub-page under the prefix).
        Longest prefix wins so overlapping paths resolve deterministically.
        """
        # Reduce a full URL down to just its path for comparison.
        m = re.search(r"https?://[^/]+(/[^?#]*)", url_or_path)
        path = m.group(1) if m else url_or_path
        path = path.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith("/"):
            path = "/" + path

        best_label: str | None = None
        best_len = -1
        for label in self.pages:
            prefix = self.page_path(label).rstrip("/")
            # Match the page itself (/view-post) or anything under it (/view-post/...).
            if path == prefix or path.startswith(prefix + "/"):
                if len(prefix) > best_len:
                    best_label, best_len = label, len(prefix)
        return best_label

    @property
    def slug(self) -> str:
        """Filesystem-safe key derived from domain — used for output dirs."""
        return re.sub(r"[^a-z0-9]+", "_", self.domain.lower()).strip("_")

    def output_dir(self, base: str = "reports") -> str:
        """Per-site output directory: reports/<slug>/"""
        path = os.path.join(base, self.slug)
        os.makedirs(path, exist_ok=True)
        return path

    def as_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────
# Registry — built from just the website name(s)
# ──────────────────────────────────────────────────────────────────────────

def _clean_domain(domain: str) -> str:
    domain = re.sub(r"^https?://", "", domain.strip().lower()).rstrip("/")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split("/", 1)[0]


def make_site(domain: str, pages: dict[str, str] | None = None) -> Site:
    """Build a Site from only its name (URLs/slug are derived)."""
    return Site(domain=_clean_domain(domain), pages=dict(pages or PAGES))


SITES: list[Site] = [make_site(WEBSITE)]
SITES_BY_DOMAIN: dict[str, Site] = {s.domain: s for s in SITES}


def get_site(domain_or_url: str) -> Site:
    """Look up a registered site, or build one on the fly from its name."""
    key = _clean_domain(domain_or_url)
    return SITES_BY_DOMAIN.get(key, make_site(key))


# ──────────────────────────────────────────────────────────────────────────
# Automatic searching / fetching
# ──────────────────────────────────────────────────────────────────────────

def fetch_url(url: str, timeout: int = 20) -> dict:
    """Fetch a single URL (public, no auth) and return status + text."""
    if requests is None:
        raise RuntimeError("The 'requests' package is required: pip install requests")

    headers = {"User-Agent": "Mozilla/5.0 (compatible; SiteFetcher/1.0)"}
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)

    return {
        "requested_url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "ok": resp.ok,
        "content_type": resp.headers.get("Content-Type", ""),
        "length": len(resp.text),
        "text": resp.text,
    }


def fetch_page(site: Site, label: str, timeout: int = 20) -> dict:
    """Fetch one named page for a site (e.g. 'profile' or 'view_post')."""
    result = fetch_url(site.page_url(label), timeout=timeout)
    result["domain"] = site.domain
    result["page"] = label
    return result


def fetch_all(site: Site, timeout: int = 20) -> dict[str, dict]:
    """Fetch every configured page for the site. Returns {label: result}."""
    results: dict[str, dict] = {}
    for label in site.pages:
        try:
            results[label] = fetch_page(site, label, timeout=timeout)
        except Exception as exc:  # keep going even if one page fails
            results[label] = {"page": label, "error": str(exc)}
    return results


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> None:
    domain = argv[1] if len(argv) > 1 else WEBSITE

    # If a custom path is passed, fetch just that one page.
    if len(argv) > 2:
        custom_path = argv[2]
        site = make_site(domain, pages={"custom": custom_path})
        labels = ["custom"]
    else:
        site = make_site(domain)
        labels = list(site.pages)

    print(f"Site:       {site.brand_name}")
    print(f"Domain:     {site.domain}")
    print(f"Output dir: {site.output_dir()}")
    for label in labels:
        print(f"  {label:10s} -> {site.page_url(label)}")
    print("-" * 60)

    for label in labels:
        try:
            result = fetch_page(site, label)
            print(f"[{label}] HTTP {result['status_code']}  ->  {result['final_url']}")
            print(f"        Content-Type: {result['content_type']}  Bytes: {result['length']}")

            out_file = os.path.join(site.output_dir(), f"{label}.html")
            with open(out_file, "w", encoding="utf-8") as fh:
                fh.write(result["text"])
            print(f"        Saved: {out_file}")
        except Exception as exc:  # keep CLI resilient
            print(f"[{label}] Fetch failed: {exc}")


if __name__ == "__main__":
    _main(sys.argv)

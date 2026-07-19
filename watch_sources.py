#!/usr/bin/env python3
"""
Supply Intel Watcher v3

Monitors structured feeds, PubMed, and selected web pages; deduplicates items;
scores relevance; writes Markdown and HTML reports; and preserves state between runs.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

CONFIG_FILE = Path("sources.yml")
STATE_FILE = Path("watch_state_v3.json")
REPORT_MD = Path("reports/latest.md")
REPORT_HTML = Path("reports/latest.html")
ARCHIVE_DIR = Path("reports/archive")
TIMEOUT = 45

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SupplyIntelWatcher/3.0; "
        "+https://github.com/supplyintel/supply-intel-watcher)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


@dataclass
class Item:
    source: str
    section: str
    title: str
    url: str
    published: str
    summary: str
    matched_keywords: list[str]
    score: int
    presentation_worthy: bool
    item_id: str


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sources.yml must contain a YAML mapping.")
    return data


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"seen": {}, "page_hashes": {}, "failures": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("seen", {})
        data.setdefault("page_hashes", {})
        data.setdefault("failures", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"seen": {}, "page_hashes": {}, "failures": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return " ".join(soup.get_text(" ").split())


def stable_id(source: str, title: str, url: str) -> str:
    raw = f"{source}\n{title}\n{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def keyword_matches(text: str, keyword_groups: dict[str, list[str]]) -> tuple[list[str], int]:
    lowered = text.lower()
    matches: list[str] = []
    score = 0
    weights = {
        "high_priority": 5,
        "massachusetts": 4,
        "drug_classes": 3,
        "surveillance_terms": 2,
    }
    for group, terms in keyword_groups.items():
        for term in terms:
            if term.lower() in lowered:
                matches.append(term)
                score += weights.get(group, 1)
    return sorted(set(matches), key=str.lower), score


def is_relevant(text: str, matches: list[str], source: dict[str, Any]) -> bool:
    if source.get("include_all", False):
        return True
    return bool(matches)


def get_session(config: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    contact = config.get("contact_email")
    if contact:
        session.headers["From"] = contact
    return session


def fetch_rss(
    source: dict[str, Any],
    session: requests.Session,
    keyword_groups: dict[str, list[str]],
) -> list[Item]:
    response = session.get(source["url"], timeout=TIMEOUT)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS parse failed: {getattr(feed, 'bozo_exception', 'unknown error')}")

    items: list[Item] = []
    for entry in feed.entries[: source.get("max_items", 40)]:
        title = clean_text(entry.get("title", "Untitled"))
        url = entry.get("link", source["url"])
        summary = clean_text(entry.get("summary") or entry.get("description"))
        published = clean_text(entry.get("published") or entry.get("updated"))
        combined = f"{title} {summary}"
        matches, score = keyword_matches(combined, keyword_groups)
        if not is_relevant(combined, matches, source):
            continue
        item_id = stable_id(source["name"], title, url)
        items.append(
            Item(
                source=source["name"],
                section=source.get("section", "Other"),
                title=title,
                url=url,
                published=published,
                summary=summary[:900],
                matched_keywords=matches,
                score=score + int(source.get("base_score", 0)),
                presentation_worthy=score >= int(source.get("presentation_threshold", 7)),
                item_id=item_id,
            )
        )
    return items


def fetch_pubmed(
    source: dict[str, Any],
    session: requests.Session,
    keyword_groups: dict[str, list[str]],
    contact_email: str,
) -> list[Item]:
    query = source["query"]
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": source.get("max_items", 30),
        "sort": "pub date",
        "datetype": "edat",
        "reldate": source.get("lookback_days", 14),
        "tool": "supply_intel_watcher",
        "email": contact_email,
    }
    api_key = os.getenv("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    search = session.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=params,
        timeout=TIMEOUT,
    )
    search.raise_for_status()
    ids = search.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    summary_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
        "tool": "supply_intel_watcher",
        "email": contact_email,
    }
    if api_key:
        summary_params["api_key"] = api_key

    details = session.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        params=summary_params,
        timeout=TIMEOUT,
    )
    details.raise_for_status()
    payload = details.json().get("result", {})

    items: list[Item] = []
    for pmid in ids:
        record = payload.get(pmid, {})
        title = clean_text(record.get("title", "Untitled PubMed record"))
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        published = clean_text(record.get("pubdate") or record.get("sortpubdate"))
        authors = ", ".join(a.get("name", "") for a in record.get("authors", [])[:4])
        journal = clean_text(record.get("fulljournalname"))
        summary = " | ".join(part for part in [authors, journal] if part)
        matches, score = keyword_matches(title, keyword_groups)
        item_id = stable_id(source["name"], title, url)
        items.append(
            Item(
                source=source["name"],
                section=source.get("section", "Research"),
                title=title,
                url=url,
                published=published,
                summary=summary,
                matched_keywords=matches,
                score=score + int(source.get("base_score", 1)),
                presentation_worthy=score >= int(source.get("presentation_threshold", 7)),
                item_id=item_id,
            )
        )
    return items


def fetch_html_links(
    source: dict[str, Any],
    session: requests.Session,
    keyword_groups: dict[str, list[str]],
) -> list[Item]:
    response = session.get(source["url"], timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    selector = source.get("selector", "main")
    region = soup.select_one(selector) if selector else soup
    if region is None:
        raise RuntimeError(f"Selector did not match: {selector}")

    items: list[Item] = []
    seen_links: set[str] = set()
    for link in region.select(source.get("link_selector", "a[href]")):
        title = clean_text(link.get_text(" "))
        href = link.get("href")
        if not title or not href:
            continue
        url = urljoin(source["url"], href)
        if url in seen_links:
            continue
        seen_links.add(url)
        context = clean_text(link.parent.get_text(" ") if link.parent else title)
        matches, score = keyword_matches(f"{title} {context}", keyword_groups)
        if not is_relevant(f"{title} {context}", matches, source):
            continue
        item_id = stable_id(source["name"], title, url)
        items.append(
            Item(
                source=source["name"],
                section=source.get("section", "Other"),
                title=title,
                url=url,
                published="",
                summary=context[:900],
                matched_keywords=matches,
                score=score + int(source.get("base_score", 0)),
                presentation_worthy=score >= int(source.get("presentation_threshold", 7)),
                item_id=item_id,
            )
        )
        if len(items) >= source.get("max_items", 40):
            break
    return items


def fetch_page_hash(
    source: dict[str, Any],
    session: requests.Session,
    keyword_groups: dict[str, list[str]],
    previous_hash: str | None,
) -> tuple[list[Item], str]:
    response = session.get(source["url"], timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    selector = source.get("selector", "main")
    region = soup.select_one(selector) if selector else (soup.body or soup)
    if region is None:
        raise RuntimeError(f"Selector did not match: {selector}")
    text = clean_text(region.get_text(" "))
    if len(text) < 150:
        raise RuntimeError(f"Only {len(text)} readable characters returned.")
    page_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if previous_hash is None or previous_hash == page_hash:
        return [], page_hash

    matches, score = keyword_matches(text[:6000], keyword_groups)
    item = Item(
        source=source["name"],
        section=source.get("section", "Other"),
        title=f"Page content changed: {source['name']}",
        url=source["url"],
        published=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        summary="The watched page changed. Review the source directly before using the information.",
        matched_keywords=matches,
        score=score + int(source.get("base_score", 0)),
        presentation_worthy=False,
        item_id=stable_id(source["name"], "page changed", page_hash),
    )
    return [item], page_hash


def run_source(
    source: dict[str, Any],
    session: requests.Session,
    config: dict[str, Any],
    state: dict[str, Any],
) -> list[Item]:
    source_type = source["type"]
    keyword_groups = config["keywords"]
    if source_type == "rss":
        return fetch_rss(source, session, keyword_groups)
    if source_type == "pubmed":
        return fetch_pubmed(
            source,
            session,
            keyword_groups,
            config.get("contact_email", "example@example.com"),
        )
    if source_type == "html_links":
        return fetch_html_links(source, session, keyword_groups)
    if source_type == "page_hash":
        old_hash = state["page_hashes"].get(source["name"])
        items, new_hash = fetch_page_hash(source, session, keyword_groups, old_hash)
        state["page_hashes"][source["name"]] = new_hash
        return items
    raise ValueError(f"Unsupported source type: {source_type}")


def render_markdown(
    new_items: list[Item],
    failures: list[dict[str, str]],
    checked_count: int,
) -> str:
    run_time = datetime.now(timezone.utc)
    lines = [
        "# Weekly drug intelligence report",
        "",
        f"**Run time:** {run_time.strftime('%B %d, %Y at %H:%M UTC')}",
        f"**Sources checked:** {checked_count}",
        f"**New relevant items:** {len(new_items)}",
        f"**Source failures:** {len(failures)}",
        "",
    ]

    if not new_items:
        lines.extend(["## New items", "", "No new relevant items were detected.", ""])
    else:
        sections: dict[str, list[Item]] = {}
        for item in sorted(new_items, key=lambda x: (-x.score, x.source, x.title.lower())):
            sections.setdefault(item.section, []).append(item)
        for section in ["Massachusetts", "United States", "International", "Research", "Other"]:
            if section not in sections:
                continue
            lines.extend([f"## {section}", ""])
            for item in sections[section]:
                flag = " **[Presentation-worthy]**" if item.presentation_worthy else ""
                lines.append(f"### [{item.title}]({item.url}){flag}")
                lines.append(f"- Source: {item.source}")
                if item.published:
                    lines.append(f"- Published/updated: {item.published}")
                lines.append(f"- Relevance score: {item.score}")
                if item.matched_keywords:
                    lines.append(f"- Terms matched: {', '.join(item.matched_keywords)}")
                if item.summary:
                    lines.append(f"- Note: {item.summary}")
                lines.append("")

    lines.extend(["## Source status", ""])
    if failures:
        for failure in failures:
            lines.append(
                f"- **FAILED — {failure['source']}**: {failure['error']} "
                f"([open source]({failure['url']}))"
            )
    else:
        lines.append("All enabled sources completed without an error.")
    lines.extend(
        [
            "",
            "## Use limits",
            "",
            "Automated flags require review against the linked source before use in a "
            "presentation, advisory, court document, or clinical communication.",
            "",
        ]
    )
    return "\n".join(lines)


def render_html_report(markdown_items: list[Item], failures: list[dict[str, str]], checked_count: int) -> str:
    run_time = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    grouped: dict[str, list[Item]] = {}
    for item in sorted(markdown_items, key=lambda x: (-x.score, x.source, x.title.lower())):
        grouped.setdefault(item.section, []).append(item)

    blocks = []
    for section in ["Massachusetts", "United States", "International", "Research", "Other"]:
        if section not in grouped:
            continue
        cards = []
        for item in grouped[section]:
            matched = ", ".join(item.matched_keywords) or "No configured term"
            badge = '<span class="badge">Presentation-worthy</span>' if item.presentation_worthy else ""
            cards.append(
                f"""
                <div class="card">
                  <h3><a href="{html.escape(item.url)}">{html.escape(item.title)}</a> {badge}</h3>
                  <p><b>Source:</b> {html.escape(item.source)}</p>
                  <p><b>Date:</b> {html.escape(item.published or "Not supplied")}</p>
                  <p><b>Score:</b> {item.score} &nbsp; <b>Terms:</b> {html.escape(matched)}</p>
                  <p>{html.escape(item.summary)}</p>
                </div>
                """
            )
        blocks.append(f"<h2>{html.escape(section)}</h2>{''.join(cards)}")

    if not blocks:
        blocks.append("<h2>New items</h2><p>No new relevant items were detected.</p>")

    failure_html = (
        "".join(
            f'<li><b>{html.escape(f["source"])}</b>: {html.escape(f["error"])} '
            f'(<a href="{html.escape(f["url"])}">open source</a>)</li>'
            for f in failures
        )
        if failures
        else "<li>All enabled sources completed without an error.</li>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly drug intelligence report</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 24px; color: #202124; }}
.header {{ border-bottom: 3px solid #314b66; padding-bottom: 14px; }}
.metrics {{ background: #f3f5f7; padding: 12px 16px; margin: 18px 0; }}
.card {{ border: 1px solid #d7dce1; border-radius: 6px; padding: 14px; margin: 10px 0; }}
.card h3 {{ margin-top: 0; }}
.badge {{ font-size: 12px; background: #efe7c5; padding: 3px 6px; border-radius: 4px; }}
a {{ color: #174ea6; }}
.note {{ font-size: 13px; color: #5f6368; margin-top: 24px; }}
</style>
</head>
<body>
<div class="header">
<h1>Weekly drug intelligence report</h1>
<p>{html.escape(run_time)}</p>
</div>
<div class="metrics">
<b>Sources checked:</b> {checked_count}<br>
<b>New relevant items:</b> {len(markdown_items)}<br>
<b>Source failures:</b> {len(failures)}
</div>
{''.join(blocks)}
<h2>Source status</h2>
<ul>{failure_html}</ul>
<p class="note">Automated flags require review against the linked source before use in a presentation, advisory, court document, or clinical communication.</p>
</body>
</html>
"""


def main() -> int:
    config = load_yaml(CONFIG_FILE)
    state = load_state()
    session = get_session(config)
    seen: dict[str, str] = state["seen"]
    all_candidates: list[Item] = []
    failures: list[dict[str, str]] = []
    enabled_sources = [s for s in config.get("sources", []) if s.get("enabled", True)]

    for source in enabled_sources:
        try:
            candidates = run_source(source, session, config, state)
            all_candidates.extend(candidates)
            state["failures"].pop(source["name"], None)
            print(f"ok      {source['name']}: {len(candidates)} candidate(s)")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            failures.append({"source": source["name"], "url": source["url"], "error": message})
            previous = state["failures"].get(source["name"], {"count": 0})
            state["failures"][source["name"]] = {
                "count": int(previous.get("count", 0)) + 1,
                "last_error": message,
                "last_failed": datetime.now(timezone.utc).isoformat(),
            }
            print(f"FAILED  {source['name']}: {message}", file=sys.stderr)

    new_items: list[Item] = []
    for item in all_candidates:
        if item.item_id not in seen:
            new_items.append(item)
        seen[item.item_id] = datetime.now(timezone.utc).isoformat()

    max_seen = int(config.get("state", {}).get("max_seen_items", 5000))
    if len(seen) > max_seen:
        trimmed = sorted(seen.items(), key=lambda pair: pair[1], reverse=True)[:max_seen]
        state["seen"] = dict(trimmed)

    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    markdown = render_markdown(new_items, failures, len(enabled_sources))
    html_report = render_html_report(new_items, failures, len(enabled_sources))
    REPORT_MD.write_text(markdown, encoding="utf-8")
    REPORT_HTML.write_text(html_report, encoding="utf-8")

    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (ARCHIVE_DIR / f"{date_stamp}.md").write_text(markdown, encoding="utf-8")
    (ARCHIVE_DIR / f"{date_stamp}.html").write_text(html_report, encoding="utf-8")

    save_state(state)
    print(f"\nWrote {REPORT_MD} and {REPORT_HTML}")
    print(f"New items: {len(new_items)} | Failures: {len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

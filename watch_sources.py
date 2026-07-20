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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG_FILE = Path("sources.yml")
STATE_FILE = Path("watch_state_v3.json")
REPORT_MD = Path("reports/latest.md")
REPORT_HTML = Path("reports/latest.html")
EMAIL_REPORT_MD = Path("reports/email.md")
EMAIL_REPORT_HTML = Path("reports/email.html")
BRIEFING_REPORT_MD = Path("reports/briefing.md")
ONE_PAGER_REPORT_MD = Path("reports/one_pager.md")
EDITORIAL_QUEUE_MD = Path("reports/editorial_queue.md")
TRENDS_REPORT_MD = Path("reports/trends.md")
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
        return {"seen": {}, "page_hashes": {}, "failures": {}, "initialized_sources": {}, "topic_history": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("seen", {})
        data.setdefault("page_hashes", {})
        data.setdefault("failures", {})
        data.setdefault("initialized_sources", {})
        data.setdefault("topic_history", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"seen": {}, "page_hashes": {}, "failures": {}, "initialized_sources": {}}


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


def is_excluded(text: str, source: dict[str, Any]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in source.get("exclude_terms", []))


def get_session(config: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
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

    time.sleep(float(source.get("request_delay_seconds", 0.4)))
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

    time.sleep(float(source.get("request_delay_seconds", 0.4)))
    details = session.post(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        data=summary_params,
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
        if is_excluded(f"{title} {summary}", source):
            continue
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
        fallback = source.get("selector_fallback")
        region = soup.select_one(fallback) if fallback else None
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
        url_includes = [value.lower() for value in source.get("url_includes", [])]
        if url_includes and not any(value in url.lower() for value in url_includes):
            continue
        context = clean_text(link.parent.get_text(" ") if link.parent else title)
        if is_excluded(f"{title} {context}", source):
            continue
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


EMERGING_SUBSTANCES = {
    "Nitazenes": ("nitazene", "isotonitazene", "metonitazene", "protonitazene", "etonitazene"),
    "Medetomidine": ("medetomidine", "dexmedetomidine"),
    "Xylazine": ("xylazine",),
    "Novel benzodiazepines": ("bromazolam", "etizolam", "clonazolam", "novel benzodiazepine"),
    "Novel stimulants": ("cathinone", "novel stimulant", "synthetic stimulant"),
    "Synthetic cannabinoids": ("synthetic cannabinoid", "spice", "k2"),
}

AUDIENCE_RULES = {
    "Court staff": (
        ("overdose", "mortality", "death", "toxicology", "scheduling", "drug checking"),
        "May inform court education, case context, and referral conversations; do not treat surveillance as evidence about an individual case.",
    ),
    "Treatment providers": (
        ("overdose", "withdrawal", "treatment", "xylazine", "medetomidine", "nitazene", "benzodiazepine"),
        "May inform screening, clinical vigilance, and staff education; verify clinical guidance with the linked source.",
    ),
    "Harm reduction": (
        ("alert", "warning", "drug checking", "overdose", "naloxone", "xylazine", "medetomidine", "nitazene"),
        "May inform outreach, supply education, and alert monitoring; confirm local relevance before distribution.",
    ),
    "Law enforcement": (
        ("scheduling", "forensic", "toxicology", "novel psychoactive", "drug supply", "seizure"),
        "May inform situational awareness and training; surveillance signals alone do not establish identity, impairment, or culpability.",
    ),
}


def item_text(item: Item) -> str:
    return " ".join(
        [item.source, item.section, item.title, item.summary, *item.matched_keywords]
    ).lower()


def classify_item(item: Item) -> dict[str, list[str]]:
    """Return deterministic Phase 3 categories; an item may support several sections."""
    text = item_text(item)
    categories: dict[str, list[str]] = {
        "Massachusetts": [],
        "Emerging substances": [],
        "Research": [],
        "Implications": [],
    }

    if item.section == "Massachusetts" or "massachusetts" in text:
        if "dashboard" in text or "data" in text or "surveillance" in text:
            categories["Massachusetts"].append("Dashboard updates")
        elif "alert" in text or "warning" in text or "drug checking" in text:
            categories["Massachusetts"].append("Drug-checking alerts")
        else:
            categories["Massachusetts"].append("New reports")

    for label, terms in EMERGING_SUBSTANCES.items():
        if any(term in text for term in terms):
            categories["Emerging substances"].append(label)

    if item.section == "Research" or "pubmed" in text or "journal" in text:
        if "case report" in text or "forensic toxicology" in text:
            categories["Research"].append("Toxicology case reports")
        elif "mortality" in text or "death" in text or "fatal" in text:
            categories["Research"].append("Mortality studies")
        else:
            categories["Research"].append("New PubMed papers")

    for audience, (terms, _note) in AUDIENCE_RULES.items():
        if any(term in text for term in terms):
            categories["Implications"].append(audience)
    return categories


def usefulness_flags(item: Item) -> list[str]:
    flags: list[str] = []
    if item.presentation_worthy:
        flags.append("Presentation")
    if item.score >= 7 and (
        item.section == "Massachusetts"
        or any(classify_item(item)[key] for key in ("Emerging substances", "Research"))
    ):
        flags.append("One-pager")
    return flags



def priority_score(item: Item) -> int:
    """Rank briefing candidates without changing the underlying relevance score."""
    categories = classify_item(item)
    score = item.score
    if item.section == "Massachusetts" or categories["Massachusetts"]:
        score += 4
    score += min(4, 2 * len(categories["Emerging substances"]))
    if "Drug-checking alerts" in categories["Massachusetts"] or "alert" in item_text(item):
        score += 2
    if item.presentation_worthy:
        score += 2
    return score



def signal_topics(item: Item) -> set[str]:
    categories = classify_item(item)
    return set(categories["Massachusetts"] + categories["Emerging substances"])


def corroborating_sources(item: Item, items: list[Item]) -> list[str]:
    topics = signal_topics(item)
    if not topics:
        return [item.source]
    sources = {
        candidate.source
        for candidate in items
        if topics.intersection(signal_topics(candidate))
    }
    return sorted(sources, key=str.lower)


def evidence_label(item: Item, items: list[Item]) -> str:
    if item.title.lower().startswith("page content changed:"):
        return "Change detected — source review required"
    source_count = len(corroborating_sources(item, items))
    if source_count >= 2:
        return f"Cross-source signal ({source_count} sources)"
    return "Single-source signal"


def priority_tier(item: Item, items: list[Item]) -> str:
    text = item_text(item)
    urgent_signal = (
        "alert" in text
        or "warning" in text
        or "Drug-checking alerts" in classify_item(item)["Massachusetts"]
    )
    cross_source = len(corroborating_sources(item, items)) >= 2
    if priority_score(item) >= 14 and (urgent_signal or cross_source):
        return "Review now"
    if priority_score(item) >= 9 or usefulness_flags(item):
        return "Monitor"
    return "Background"


def render_editorial_queue(items: list[Item], checked_count: int) -> str:
    tiers = {"Review now": [], "Monitor": [], "Background": []}
    for item in sorted(
        items,
        key=lambda candidate: (
            -priority_score(candidate),
            -candidate.score,
            candidate.title.lower(),
        ),
    ):
        tiers[priority_tier(item, items)].append(item)

    lines = [
        "# Editorial review queue",
        "",
        f"**Sources checked:** {checked_count}",
        f"**New items triaged:** {len(items)}",
        "",
    ]
    for tier, description in (
        ("Review now", "High-priority alert or corroborated signal requiring prompt human review."),
        ("Monitor", "Relevant signal to track or consider for a briefing."),
        ("Background", "Lower-priority material retained for research and archive use."),
    ):
        lines.extend([f"## {tier}", "", description, ""])
        if not tiers[tier]:
            lines.extend(["No items in this tier.", ""])
            continue
        for item in tiers[tier]:
            sources = corroborating_sources(item, items)
            lines.extend(
                [
                    f"### [{item.title}]({item.url})",
                    f"- Priority score: {priority_score(item)}",
                    f"- Evidence: {evidence_label(item, items)}",
                    f"- Related sources: {', '.join(sources)}",
                    f"- Suggested products: {', '.join(usefulness_flags(item)) or 'Archive/research'}",
                    f"- Audiences: {', '.join(classify_item(item)['Implications']) or 'General'}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Editorial safeguard",
            "",
            "Priority and corroboration describe automated matching across source items; "
            "they do not verify that separate sources report the same event or establish "
            "causation. Review every linked source before publication or operational use.",
            "",
        ]
    )
    return "\n".join(lines)


def trend_topics(item: Item) -> list[str]:
    categories = classify_item(item)
    topics = (
        categories["Massachusetts"]
        + categories["Emerging substances"]
        + categories["Research"]
    )
    return list(dict.fromkeys(topics))


def record_topic_history(
    state: dict[str, Any],
    items: list[Item],
    now: datetime | None = None,
) -> None:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    history = state.setdefault("topic_history", [])
    for item in items:
        topics = trend_topics(item)
        if topics:
            history.append(
                {
                    "item_id": item.item_id,
                    "recorded": timestamp.isoformat(),
                    "source": item.source,
                    "topics": topics,
                }
            )
    cutoff = timestamp - timedelta(days=90)
    retained = []
    for entry in history:
        try:
            recorded = datetime.fromisoformat(entry["recorded"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if recorded >= cutoff:
            retained.append(entry)
    state["topic_history"] = retained


def trend_snapshot(
    history: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, dict[str, int | str]]:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_start = timestamp - timedelta(days=7)
    prior_start = timestamp - timedelta(days=14)
    counts: dict[str, dict[str, int]] = {}
    for entry in history:
        try:
            recorded = datetime.fromisoformat(entry["recorded"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        bucket = "current" if recorded >= current_start else "prior" if recorded >= prior_start else ""
        if not bucket:
            continue
        for topic in entry.get("topics", []):
            counts.setdefault(topic, {"current": 0, "prior": 0})[bucket] += 1

    snapshot: dict[str, dict[str, int | str]] = {}
    for topic, values in counts.items():
        current, prior = values["current"], values["prior"]
        if current > 0 and prior == 0:
            status = "New"
        elif current >= prior + 2:
            status = "Rising"
        elif current > 0 and current >= prior:
            status = "Steady"
        elif prior > current:
            status = "Cooling"
        else:
            status = "Quiet"
        snapshot[topic] = {"current": current, "prior": prior, "status": status}
    return snapshot


def render_trends(
    history: list[dict[str, Any]],
    checked_count: int,
    now: datetime | None = None,
) -> str:
    snapshot = trend_snapshot(history, now)
    order = {"Rising": 0, "New": 1, "Steady": 2, "Cooling": 3, "Quiet": 4}
    ranked = sorted(
        snapshot.items(),
        key=lambda pair: (
            order[pair[1]["status"]],
            -int(pair[1]["current"]),
            pair[0].lower(),
        ),
    )
    lines = [
        "# Topic trend watch",
        "",
        f"**Sources checked:** {checked_count}",
        "**Comparison:** most recent 7 days versus the preceding 7 days",
        "",
    ]
    if not ranked:
        lines.extend(["No categorized topic history is available yet.", ""])
    else:
        for status in ("Rising", "New", "Steady", "Cooling"):
            rows = [(topic, values) for topic, values in ranked if values["status"] == status]
            if not rows:
                continue
            lines.extend([f"## {status}", ""])
            for topic, values in rows:
                lines.append(
                    f"- **{topic}:** {values['current']} current signal(s); "
                    f"{values['prior']} in the prior window."
                )
            lines.append("")
    lines.extend(
        [
            "## Interpretation note",
            "",
            "Counts represent newly detected source items categorized by the watcher. "
            "They are not prevalence estimates, event counts, or proof of a real-world "
            "increase. Review source coverage and linked reports before interpreting a trend.",
            "",
        ]
    )
    return "\n".join(lines)

def briefing_candidates(items: list[Item], limit: int = 5) -> list[Item]:
    eligible = [item for item in items if usefulness_flags(item)]
    return sorted(
        eligible,
        key=lambda item: (-priority_score(item), -item.score, item.title.lower()),
    )[:limit]


def concise_signal(item: Item) -> str:
    categories = classify_item(item)
    labels = (
        categories["Massachusetts"]
        + categories["Emerging substances"]
        + categories["Research"]
    )
    focus = ", ".join(labels[:3]) if labels else item.section
    date = f" ({item.published})" if item.published else ""
    return f"{item.title}{date} — {focus}."


def render_briefing(items: list[Item], checked_count: int) -> str:
    selected = briefing_candidates(items)
    lines = [
        "# Weekly presentation briefing",
        "",
        f"**Sources checked:** {checked_count}",
        f"**Priority items selected:** {len(selected)}",
        "",
        "## Opening summary",
        "",
    ]
    if not selected:
        lines.extend(["No new presentation or one-pager candidates were detected.", ""])
    else:
        for item in selected[:3]:
            lines.append(f"- {concise_signal(item)} ([source]({item.url}))")
        lines.extend(["", "## Suggested slides", ""])
        for number, item in enumerate(selected, 1):
            audiences = classify_item(item)["Implications"]
            audience_text = ", ".join(audiences) or "General surveillance"
            lines.extend(
                [
                    f"### Slide {number}: {item.title}",
                    f"- **Key signal:** {concise_signal(item)}",
                    f"- **Why it matters:** Relevant to {audience_text}.",
                    f"- **Evidence:** [{item.source}]({item.url})"
                    + (f", {item.published}" if item.published else ""),
                    f"- **Priority score:** {priority_score(item)}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Presenter note",
            "",
            "These are source-linked briefing prompts, not finished factual claims. "
            "Open and review each source before presenting it.",
            "",
        ]
    )
    return "\n".join(lines)


def render_one_pager(items: list[Item], checked_count: int) -> str:
    selected = [
        item for item in briefing_candidates(items, limit=10)
        if "One-pager" in usefulness_flags(item)
    ]
    lines = [
        "# One-pager source brief",
        "",
        f"**Sources checked:** {checked_count}",
        f"**Items selected:** {len(selected)}",
        "",
        "## What is new",
        "",
    ]
    if not selected:
        lines.extend(["No new one-pager candidates were detected.", ""])
    else:
        for item in selected[:5]:
            lines.append(f"- **[{item.title}]({item.url})** — {concise_signal(item)}")
        groups = structured_groups(selected)
        emerging = [(name, values) for name, values in groups["Emerging substances"].items() if values]
        if emerging:
            lines.extend(["", "## Substances to watch", ""])
            for name, values in emerging:
                sources = ", ".join(dict.fromkeys(item.source for item in values[:3]))
                lines.append(f"- **{name}:** {len(values)} new item(s); sources: {sources}.")
        lines.extend(["", "## Practical relevance", ""])
        for audience, (_terms, note) in AUDIENCE_RULES.items():
            count = len(groups["Implications"][audience])
            if count:
                lines.append(f"- **{audience} ({count}):** {note}")
    lines.extend(
        [
            "",
            "## Use note",
            "",
            "This is an automated source brief. Verify the linked material and add "
            "local context, dates, and caveats before publication.",
            "",
        ]
    )
    return "\n".join(lines)

def append_markdown_item(lines: list[str], item: Item, implication: str = "") -> None:
    flags = usefulness_flags(item)
    suffix = f" **[Useful for: {', '.join(flags)}]**" if flags else ""
    lines.append(f"#### [{item.title}]({item.url}){suffix}")
    lines.append(f"- Source: {item.source}")
    if item.published:
        lines.append(f"- Published/updated: {item.published}")
    lines.append(f"- Relevance score: {item.score}")
    if item.matched_keywords:
        lines.append(f"- Terms matched: {', '.join(item.matched_keywords)}")
    if implication:
        lines.append(f"- Why it may matter: {implication}")
    if item.summary:
        lines.append(f"- Note: {item.summary}")
    lines.append("")


def structured_groups(items: list[Item]) -> dict[str, dict[str, list[Item]]]:
    order = {
        "Massachusetts": ["New reports", "Dashboard updates", "Drug-checking alerts"],
        "Emerging substances": list(EMERGING_SUBSTANCES),
        "Research": ["New PubMed papers", "Toxicology case reports", "Mortality studies"],
        "Implications": list(AUDIENCE_RULES),
    }
    groups = {section: {sub: [] for sub in subs} for section, subs in order.items()}
    for item in sorted(items, key=lambda x: (-x.score, x.source, x.title.lower())):
        for section, subcategories in classify_item(item).items():
            for subcategory in subcategories:
                groups[section][subcategory].append(item)
    return groups


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
    groups = structured_groups(new_items)
    rendered_ids: set[str] = set()

    for section, subgroups in groups.items():
        populated = [(name, items) for name, items in subgroups.items() if items]
        if not populated:
            continue
        lines.extend([f"## {section}", ""])
        for subgroup, items in populated:
            lines.extend([f"### {subgroup}", ""])
            implication = AUDIENCE_RULES[subgroup][1] if section == "Implications" else ""
            for item in items:
                append_markdown_item(lines, item, implication)
                rendered_ids.add(item.item_id)

    other_items = [item for item in new_items if item.item_id not in rendered_ids]
    if other_items:
        lines.extend(["## Other new intelligence", ""])
        for item in sorted(other_items, key=lambda x: (-x.score, x.source, x.title.lower())):
            append_markdown_item(lines, item)
    elif not new_items:
        lines.extend(["## New items", "", "No new relevant items were detected.", ""])

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
            "Categories, implications, and usefulness flags are automated triage aids. "
            "Review the linked source before use in a presentation, one-pager, advisory, "
            "court document, or clinical communication.",
            "",
        ]
    )
    return "\n".join(lines)


def render_html_report(markdown_items: list[Item], failures: list[dict[str, str]], checked_count: int) -> str:
    run_time = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    groups = structured_groups(markdown_items)
    blocks: list[str] = []
    rendered_ids: set[str] = set()

    def card(item: Item, implication: str = "") -> str:
        matched = ", ".join(item.matched_keywords) or "No configured term"
        badges = "".join(
            f'<span class="badge">{html.escape(flag)}</span>'
            for flag in usefulness_flags(item)
        )
        why = (
            f"<p><b>Why it may matter:</b> {html.escape(implication)}</p>"
            if implication else ""
        )
        return f"""
        <article class="card">
          <h4><a href="{html.escape(item.url)}">{html.escape(item.title)}</a> {badges}</h4>
          <p><b>Source:</b> {html.escape(item.source)}</p>
          <p><b>Date:</b> {html.escape(item.published or "Not supplied")}</p>
          <p><b>Score:</b> {item.score} &nbsp; <b>Terms:</b> {html.escape(matched)}</p>
          {why}<p>{html.escape(item.summary)}</p>
        </article>"""

    for section, subgroups in groups.items():
        section_parts: list[str] = []
        for subgroup, items in subgroups.items():
            if not items:
                continue
            implication = AUDIENCE_RULES[subgroup][1] if section == "Implications" else ""
            section_parts.append(f"<h3>{html.escape(subgroup)}</h3>")
            for item in items:
                section_parts.append(card(item, implication))
                rendered_ids.add(item.item_id)
        if section_parts:
            blocks.append(f"<section><h2>{html.escape(section)}</h2>{''.join(section_parts)}</section>")

    other_items = [item for item in markdown_items if item.item_id not in rendered_ids]
    if other_items:
        blocks.append("<section><h2>Other new intelligence</h2>" +
                      "".join(card(item) for item in other_items) + "</section>")
    elif not markdown_items:
        blocks.append("<section><h2>New items</h2><p>No new relevant items were detected.</p></section>")

    failure_html = (
        "".join(
            f'<li><b>{html.escape(f["source"])}</b>: {html.escape(f["error"])} '
            f'(<a href="{html.escape(f["url"])}">open source</a>)</li>'
            for f in failures
        ) if failures else "<li>All enabled sources completed without an error.</li>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly drug intelligence report</title>
<style>
body {{ font-family: Arial,sans-serif; max-width: 960px; margin: 0 auto; padding: 24px; color: #202124; }}
.header {{ border-bottom: 3px solid #314b66; padding-bottom: 14px; }}
.metrics {{ background: #f3f5f7; padding: 12px 16px; margin: 18px 0; }}
.card {{ border: 1px solid #d7dce1; border-radius: 6px; padding: 14px; margin: 10px 0; }}
.card h4 {{ margin-top: 0; }}
.badge {{ font-size: 12px; background: #efe7c5; padding: 3px 6px; border-radius: 4px; margin-left: 4px; }}
a {{ color: #174ea6; }}
.note {{ font-size: 13px; color: #5f6368; margin-top: 24px; }}
</style></head><body>
<div class="header"><h1>Weekly drug intelligence report</h1><p>{html.escape(run_time)}</p></div>
<div class="metrics"><b>Sources checked:</b> {checked_count}<br>
<b>New relevant items:</b> {len(markdown_items)}<br>
<b>Source failures:</b> {len(failures)}</div>
{''.join(blocks)}
<h2>Source status</h2><ul>{failure_html}</ul>
<p class="note">Categories, implications, and usefulness flags are automated triage aids. Review the linked source before use.</p>
</body></html>
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
            if (
                source.get("baseline_on_first_run", False)
                and source["name"] not in state["initialized_sources"]
            ):
                baseline_time = datetime.now(timezone.utc).isoformat()
                for candidate in candidates:
                    seen[candidate.item_id] = baseline_time
                state["initialized_sources"][source["name"]] = baseline_time
                print(
                    f"baseline {source['name']}: "
                    f"{len(candidates)} existing item(s) recorded"
                )
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

    record_topic_history(state, new_items)

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

    email_min_score = int(config.get("email", {}).get("min_score", 7))
    email_items = [item for item in new_items if item.score >= email_min_score]
    email_markdown = render_markdown(email_items, failures, len(enabled_sources))
    email_html = render_html_report(email_items, failures, len(enabled_sources))
    EMAIL_REPORT_MD.write_text(email_markdown, encoding="utf-8")
    EMAIL_REPORT_HTML.write_text(email_html, encoding="utf-8")

    briefing = render_briefing(new_items, len(enabled_sources))
    one_pager = render_one_pager(new_items, len(enabled_sources))
    BRIEFING_REPORT_MD.write_text(briefing, encoding="utf-8")
    ONE_PAGER_REPORT_MD.write_text(one_pager, encoding="utf-8")
    editorial_queue = render_editorial_queue(new_items, len(enabled_sources))
    EDITORIAL_QUEUE_MD.write_text(editorial_queue, encoding="utf-8")
    trends = render_trends(state["topic_history"], len(enabled_sources))
    TRENDS_REPORT_MD.write_text(trends, encoding="utf-8")

    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (ARCHIVE_DIR / f"{date_stamp}.md").write_text(markdown, encoding="utf-8")
    (ARCHIVE_DIR / f"{date_stamp}.html").write_text(html_report, encoding="utf-8")

    save_state(state)
    print(f"\nWrote full, email, briefing, one-pager, editorial queue, and trend reports")
    print(
        f"New items: {len(new_items)} | "
        f"Email items: {len(email_items)} | Failures: {len(failures)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

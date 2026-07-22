#!/usr/bin/env python3
"""
Supply Intel Watcher — PubMed history backfill

Fetches historical PubMed items (published on or after BACKFILL_START) for
every PubMed source configured in sources.yml, scores and tags them using the
same functions the live watcher uses (watch_sources.classify_item,
priority_tier, evidence_label, keyword_matches), and appends any not already
present into data/history.json.

Only PubMed is fetched here. RSS / html_links / page_hash sources are left
for a future backfill script.

This script does not read or write watch_state_v3.json or any reports/
file — it only appends to data/history.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from watch_sources import (
    CONFIG_FILE,
    Item,
    classify_item,
    clean_text,
    evidence_label,
    get_session,
    is_excluded,
    keyword_matches,
    load_yaml,
    priority_tier,
)

HISTORY_FILE = Path("data/history.json")
BACKFILL_START = "2026/01/01"
TIMEOUT = 45
PAGE_SIZE = 200


def make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def save_history(records: list[dict[str, Any]]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def fetch_pubmed_since(
    source: dict[str, Any],
    session,
    keyword_groups: dict[str, list[str]],
    contact_email: str,
    since: str,
    until: str,
) -> list[Item]:
    """Historical counterpart to watch_sources.fetch_pubmed.

    Same query, exclude_terms, base_score, and keyword scoring as the live
    watcher's PubMed fetch. The only difference is the date filter: the live
    watcher uses a rolling `reldate` lookback window (days before "now"),
    which can't express a fixed start date, so this uses an absolute
    mindate/maxdate publication-date window instead. Paginates via
    retstart since a multi-month backfill can exceed one page of results.
    """
    delay = float(source.get("request_delay_seconds", 0.4))
    api_key = os.getenv("NCBI_API_KEY")
    items: list[Item] = []
    retstart = 0

    while True:
        params = {
            "db": "pubmed",
            "term": source["query"],
            "retmode": "json",
            "retmax": PAGE_SIZE,
            "retstart": retstart,
            "sort": "pub date",
            "datetype": "pdat",
            "mindate": since,
            "maxdate": until,
            "tool": "supply_intel_watcher_backfill",
            "email": contact_email,
        }
        if api_key:
            params["api_key"] = api_key

        time.sleep(delay)
        search = session.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
            timeout=TIMEOUT,
        )
        search.raise_for_status()
        result = search.json().get("esearchresult", {})
        ids = result.get("idlist", [])
        count = int(result.get("count", 0))
        if not ids:
            break

        summary_params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
            "tool": "supply_intel_watcher_backfill",
            "email": contact_email,
        }
        if api_key:
            summary_params["api_key"] = api_key

        time.sleep(delay)
        details = session.post(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            data=summary_params,
            timeout=TIMEOUT,
        )
        details.raise_for_status()
        payload = details.json().get("result", {})

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
                    item_id=make_id(url),
                )
            )

        retstart += len(ids)
        if retstart >= count or len(ids) < PAGE_SIZE:
            break

    return items


def history_record(item: Item, context_items: list[Item]) -> dict[str, Any]:
    """Same field derivation as watch_sources.dashboard_records, plus a
    stable id (hash of the URL) for long-term dedup."""
    categories = classify_item(item)
    return {
        "id": make_id(item.url),
        "title": item.title,
        "url": item.url,
        "source": item.source,
        "published": item.published,
        "summary": item.summary,
        "section": item.section,
        "score": item.score,
        "priority": priority_tier(item, context_items),
        "evidence": evidence_label(item, context_items),
        "substances": categories["Emerging substances"],
        "audiences": categories["Implications"],
    }


def main() -> int:
    config = load_yaml(CONFIG_FILE)
    keyword_groups = config["keywords"]
    contact_email = config.get("contact_email", "example@example.com")
    session = get_session(config)

    pubmed_sources = [
        source
        for source in config.get("sources", [])
        if source.get("type") == "pubmed" and source.get("enabled", True)
    ]

    until = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    all_items: list[Item] = []
    for source in pubmed_sources:
        fetched = fetch_pubmed_since(
            source, session, keyword_groups, contact_email, BACKFILL_START, until
        )
        print(f"{source['name']}: {len(fetched)} item(s) since {BACKFILL_START}")
        all_items.extend(fetched)

    history = load_history()
    known_ids = {record["id"] for record in history}

    added = 0
    for item in all_items:
        record = history_record(item, all_items)
        if record["id"] in known_ids:
            continue
        history.append(record)
        known_ids.add(record["id"])
        added += 1

    save_history(history)
    print(f"\nFetched {len(all_items)} PubMed item(s) total.")
    print(f"Added {added} new record(s); data/history.json now has {len(history)} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

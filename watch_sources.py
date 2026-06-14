"""
Supply Intel Source Watcher — v2
================================
Checks drug-surveillance sources weekly and reports anything new.

What's new in v2 (compared to the original):
  1. TIER 2 TARGETS ADDED — BSAS Dashboard, MDPH overdose data pages,
     and NFLIS, all verified live as of June 2026.
  2. SECTION WATCHING — for pages without issue numbers or feeds, the
     watcher now reads just the meaningful part of the page (using a
     "selector" — think of it as pointing at one paragraph instead of
     photographing the whole newspaper) and notices when that text changes.
  3. FAILURE ALERTS — if a source can't be reached or a page was
     restructured, you get told. The old version could go silently blind
     on a broken source; this one raises its hand instead.

You never run this by hand. GitHub Actions runs it on schedule.
To add/remove/fix a source, you ONLY edit the SOURCES list below.
"""

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# THE SOURCE LIST — this is the only part you should ever need to edit.
#
# Each source has:
#   name      - what you'll see in the alert email
#   url       - the page the robot visits
#   tier      - 1 = has clear "new issue" signals, 2 = plain page watching
#   selector  - (optional) CSS selector pointing at the part of the page
#               that matters. If blank, the robot reads the whole page.
#   note      - reminder to future-you about why this source is here
# ---------------------------------------------------------------------------

SOURCES = [
    # ----- TIER 1: structured / signal-rich sources (the original six) -----
    {
        "name": "NDEWS Weekly Briefings",
        "url": "https://ndews.umd.edu/resources/ndews-weekly-briefing",
        "tier": 1,
        "selector": "main",
        "note": "Weekly. Issue numbers in page text are the strongest signal.",
    },
    {
        "name": "CFSRE / NPS Discovery — Public Alerts",
        "url": "https://www.npsdiscovery.org/reports/public-alerts/",
        "tier": 1,
        "selector": "main",
        "note": "Ad-hoc alerts. New alert titles appear at top of list.",
    },
    {
        "name": "CFSRE / NPS Discovery — Trend Reports",
        "url": "https://www.npsdiscovery.org/reports/trend-reports/",
        "tier": 1,
        "selector": "main",
        "note": "Quarterly, four substance classes.",
    },
    {
        "name": "MADDS (Brandeis OPRC)",
        "url": "https://heller.brandeis.edu/opioid-policy/community-resources/madds/index.html",
        "tier": 1,
        "selector": "main",
        "note": "MA drug checking. New bulletins/alerts appear on this page.",
    },
    {
        "name": "NIST RaDAR",
        "url": "https://www.nist.gov/programs-projects/nist-rapid-drug-analysis-and-research-radar-program",
        "tier": 1,
        "selector": "main",
        "note": "Monthly newsletter announcements.",
    },
    {
        "name": "DEA News Releases",
        "url": "https://www.dea.gov/what-we-do/news/press-releases",
        "tier": 1,
        "selector": "main",
        "note": "Scheduling actions and enforcement news.",
    },

    # ----- TIER 2: plain pages watched for text changes (new in v2) -----
    {
        "name": "MA BSAS Dashboard (data refresh dates)",
        "url": "https://www.mass.gov/info-details/bureau-of-substance-addiction-services-bsas-dashboard",
        "tier": 2,
        "selector": "main",
        "note": ("The page itself states 'data updated through [date]' for deaths, "
                 "EMS/hospital events, and services. When those dates change, the "
                 "dashboard refreshed — your cue to update court-staff slides."),
    },
    {
        "name": "MDPH Current Overdose Data (reports list)",
        "url": "https://www.mass.gov/lists/current-overdose-data",
        "tier": 2,
        "selector": "main",
        "note": ("Hub page where MDPH posts the annual Opioid-Involved Overdose "
                 "Report, data briefs, and OTP access addenda. New documents "
                 "appear here first."),
    },
    {
        "name": "MDPH Substance Use & Overdose Data (hub)",
        "url": "https://www.mass.gov/lists/substance-use-and-overdose-data",
        "tier": 2,
        "selector": "main",
        "note": "Broader hub incl. the MADDS drug-checking dashboard link and special reports.",
    },
    {
        "name": "NFLIS Home (Snapshots & announcements)",
        "url": "https://www.nflis.deadiversion.usdoj.gov/",
        "tier": 2,
        "selector": None,  # read whole page; site structure is unusual
        "note": ("New quarterly Snapshots are announced on the homepage. "
                 "The publications page itself uses session software that can "
                 "block simple scripts, so the homepage is the reliable watch point."),
    },
    {
        "name": "NFLIS Publications (best-effort)",
        "url": "https://www.nflis.deadiversion.usdoj.gov/publicationsRedesign.xhtml",
        "tier": 2,
        "selector": None,
        "note": ("May intermittently fail due to session handling — that's expected. "
                 "A failure here alone is not an emergency; the homepage watch covers it."),
    },
]

# ---------------------------------------------------------------------------
# Everything below is the robot's machinery. You shouldn't need to touch it.
# ---------------------------------------------------------------------------

STATE_FILE = Path("watch_state.json")
CHANGES_FILE = Path("changes.md")

HEADERS = {
    "User-Agent": ("SupplyIntelWatcher/2.0 (public-health surveillance monitor; "
                   "contact: joe@supplyintel.org)"),
    "Accept": "text/html,application/xhtml+xml",
}

# Signals we try to pull out of the page text. If any of these change,
# we can say WHAT changed, not just THAT something changed.
SIGNAL_PATTERNS = [
    # "Issue 281", "Issue No. 281"
    re.compile(r"\bIssue\s*(?:No\.?\s*)?(\d{2,4})\b", re.IGNORECASE),
    # "updated through December 31, 2024" (BSAS dashboard refresh dates)
    re.compile(r"updated\s+through\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE),
    # "Posted: May 2026", "Published May 2026", "revised April 2026"
    re.compile(r"(?:posted|published|revised|released)[:\s]+([A-Z][a-z]+\s+\d{4})", re.IGNORECASE),
    # Bare "Month DD, YYYY" dates (kept last; noisiest)
    re.compile(r"\b((?:January|February|March|April|May|June|July|August|"
               r"September|October|November|December)\s+\d{1,2},\s+\d{4})\b"),
]

MAX_SIGNALS = 12  # only keep the first dozen signals; enough to fingerprint a page


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def fetch_page_text(source: dict) -> str:
    """Visit the page and return the readable text of the part we care about."""
    resp = requests.get(source["url"], headers=HEADERS, timeout=45)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Throw away scripts/styles/navigation — we only want the words a human reads.
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()

    region = None
    if source.get("selector"):
        region = soup.select_one(source["selector"])
    if region is None:
        region = soup.body or soup

    # Normalize whitespace so cosmetic spacing changes don't trigger false alarms.
    text = " ".join(region.get_text(" ").split())
    return text


def extract_signals(text: str) -> list[str]:
    """Pull issue numbers / data dates out of the page text."""
    found: list[str] = []
    for pattern in SIGNAL_PATTERNS:
        for match in pattern.findall(text):
            item = match if isinstance(match, str) else match[0]
            item = item.strip()
            if item and item not in found:
                found.append(item)
            if len(found) >= MAX_SIGNALS:
                return found
    return found


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_source(source: dict, prev: dict) -> tuple[dict, str | None]:
    """
    Returns (new_state_entry, change_message_or_None).
    Raises on network/parse failure — caller turns that into a problem report.
    """
    text = fetch_page_text(source)
    if len(text) < 200:
        # A nearly-empty page usually means a block page or a broken selector.
        raise RuntimeError(f"Page returned almost no readable text ({len(text)} chars) — "
                           "possible block page or site redesign.")

    signals = extract_signals(text)
    page_hash = digest(text)
    new_entry = {"signals": signals, "hash": page_hash, "last_checked": str(date.today())}

    if not prev:
        return new_entry, None  # first run: just remember, don't alarm

    old_signals = prev.get("signals", [])
    new_items = [s for s in signals if s not in old_signals]

    if new_items:
        shown = ", ".join(new_items[:5])
        return new_entry, f"New signal(s) detected: **{shown}**"
    if page_hash != prev.get("hash"):
        return new_entry, ("Page content changed (no clear issue number or date found — "
                           "worth a quick look).")
    return new_entry, None


def main() -> None:
    state = load_state()
    changes: list[str] = []
    problems: list[str] = []

    for source in SOURCES:
        name = source["name"]
        try:
            new_entry, message = check_source(source, state.get(name, {}))
            state[name] = new_entry
            if message:
                changes.append(f"### {name}\n{message}\n\n[Open the source]({source['url']})\n")
                print(f"CHANGE  {name}")
            else:
                print(f"ok      {name}")
        except Exception as exc:  # noqa: BLE001 — any failure becomes a report, never a crash
            problems.append(f"### ⚠ {name}\nThe watcher could not read this source: `{exc}`\n\n"
                            f"[Check it by hand]({source['url']})\n")
            print(f"FAILED  {name}: {exc}", file=sys.stderr)

    STATE_FILE.write_text(json.dumps(state, indent=2))

    if changes or problems:
        parts = [f"# Source watch — {date.today()}\n"]
        if changes:
            parts.append("## 🟢 New content detected\n")
            parts.extend(changes)
        if problems:
            parts.append("## ⚠ Watcher problems (source may be blind)\n")
            parts.append("_A failed check means no news from that source is reaching you. "
                         "If the same source fails two weeks in a row, the page probably "
                         "moved and its URL in `watch_sources.py` needs updating._\n")
            parts.extend(problems)
        parts.append("\n---\n*Items flagged here go in the newsletter inbox. "
                     "Dashboard numbers still require your review before publication.*\n")
        CHANGES_FILE.write_text("\n".join(parts))
        print(f"\nWrote {CHANGES_FILE} with {len(changes)} change(s), {len(problems)} problem(s).")
    else:
        # Make sure no stale changes.md from a previous run lingers
        if CHANGES_FILE.exists():
            CHANGES_FILE.unlink()
        print("\nNothing new this week. Staying quiet.")


if __name__ == "__main__":
    main()

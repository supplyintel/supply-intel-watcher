import unittest
from datetime import datetime, timedelta, timezone

from watch_sources import (
    Item,
    briefing_candidates,
    classify_item,
    corroborating_sources,
    evidence_label,
    priority_score,
    priority_tier,
    render_briefing,
    render_editorial_queue,
    render_markdown,
    render_one_pager,
    render_trends,
    record_topic_history,
    structured_groups,
    trend_snapshot,
    usefulness_flags,
)


def make_item(**overrides):
    values = {
        "source": "PubMed — forensic toxicology case reports",
        "section": "Research",
        "title": "Nitazene forensic toxicology case report",
        "url": "https://example.org/item",
        "published": "2026-07-20",
        "summary": "A fatal overdose involving protonitazene and xylazine.",
        "matched_keywords": ["nitazene", "xylazine", "overdose"],
        "score": 12,
        "presentation_worthy": True,
        "item_id": "item-1",
    }
    values.update(overrides)
    return Item(**values)


class Phase3ClassificationTests(unittest.TestCase):
    def test_emerging_research_and_audience_categories(self):
        categories = classify_item(make_item())
        self.assertIn("Nitazenes", categories["Emerging substances"])
        self.assertIn("Xylazine", categories["Emerging substances"])
        self.assertIn("Toxicology case reports", categories["Research"])
        self.assertIn("Court staff", categories["Implications"])
        self.assertIn("Treatment providers", categories["Implications"])
        self.assertIn("Harm reduction", categories["Implications"])
        self.assertIn("Law enforcement", categories["Implications"])

    def test_massachusetts_dashboard(self):
        item = make_item(
            source="Massachusetts BSAS dashboard",
            section="Massachusetts",
            title="Dashboard updated",
            summary="New surveillance data",
            item_id="item-2",
        )
        self.assertEqual(
            classify_item(item)["Massachusetts"],
            ["Dashboard updates"],
        )

    def test_usefulness_flags(self):
        self.assertEqual(usefulness_flags(make_item()), ["Presentation", "One-pager"])

    def test_structured_report_has_phase3_sections_and_disclaimer(self):
        report = render_markdown([make_item()], [], 50)
        self.assertIn("## Emerging substances", report)
        self.assertIn("### Nitazenes", report)
        self.assertIn("## Research", report)
        self.assertIn("## Implications", report)
        self.assertIn("### Court staff", report)
        self.assertIn("Useful for: Presentation, One-pager", report)
        self.assertIn("automated triage aids", report)
        self.assertIn("Sources checked:** 50", report)

    def test_item_can_appear_in_multiple_structured_groups(self):
        groups = structured_groups([make_item()])
        self.assertEqual(len(groups["Emerging substances"]["Nitazenes"]), 1)
        self.assertEqual(len(groups["Research"]["Toxicology case reports"]), 1)
        self.assertEqual(len(groups["Implications"]["Court staff"]), 1)


    def test_priority_prefers_massachusetts_and_emerging_signals(self):
        general = make_item(
            title="General research update",
            summary="A research update",
            matched_keywords=[],
            score=12,
            presentation_worthy=True,
            item_id="general",
        )
        local = make_item(
            source="Massachusetts surveillance",
            section="Massachusetts",
            title="Nitazene alert",
            summary="Drug checking alert",
            score=9,
            item_id="local",
        )
        self.assertGreater(priority_score(local), priority_score(general))
        self.assertEqual(briefing_candidates([general, local], limit=1), [local])

    def test_presentation_brief_is_concise_and_source_linked(self):
        report = render_briefing([make_item()], 50)
        self.assertIn("# Weekly presentation briefing", report)
        self.assertIn("## Opening summary", report)
        self.assertIn("## Suggested slides", report)
        self.assertIn("[PubMed — forensic toxicology case reports]", report)
        self.assertIn("not finished factual claims", report)

    def test_one_pager_groups_substances_and_audiences(self):
        report = render_one_pager([make_item()], 50)
        self.assertIn("# One-pager source brief", report)
        self.assertIn("## Substances to watch", report)
        self.assertIn("**Nitazenes:**", report)
        self.assertIn("## Practical relevance", report)
        self.assertIn("**Court staff (1):**", report)
        self.assertIn("automated source brief", report)


    def test_cross_source_signal_and_review_tier(self):
        first = make_item(source="Lab A", item_id="a")
        second = make_item(
            source="Lab B",
            title="Xylazine and nitazene alert",
            item_id="b",
        )
        items = [first, second]
        self.assertEqual(corroborating_sources(first, items), ["Lab A", "Lab B"])
        self.assertEqual(evidence_label(first, items), "Cross-source signal (2 sources)")
        self.assertEqual(priority_tier(second, items), "Review now")

    def test_page_change_requires_source_review(self):
        item = make_item(
            title="Page content changed: State dashboard",
            section="United States",
            summary="The watched page changed.",
            matched_keywords=[],
            score=5,
            presentation_worthy=False,
        )
        self.assertEqual(
            evidence_label(item, [item]),
            "Change detected — source review required",
        )

    def test_editorial_queue_has_tiers_evidence_and_safeguard(self):
        first = make_item(source="Lab A", item_id="a")
        second = make_item(source="Lab B", item_id="b")
        report = render_editorial_queue([first, second], 50)
        self.assertIn("# Editorial review queue", report)
        self.assertIn("## Review now", report)
        self.assertIn("## Monitor", report)
        self.assertIn("## Background", report)
        self.assertIn("Cross-source signal (2 sources)", report)
        self.assertIn("do not verify that separate sources report the same event", report)


    def test_topic_history_records_and_trims_old_entries(self):
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        state = {
            "topic_history": [
                {
                    "item_id": "old",
                    "recorded": (now - timedelta(days=91)).isoformat(),
                    "source": "Old source",
                    "topics": ["Nitazenes"],
                }
            ]
        }
        record_topic_history(state, [make_item()], now)
        self.assertEqual(len(state["topic_history"]), 1)
        self.assertEqual(
            state["topic_history"][0]["topics"],
            ["Nitazenes", "Xylazine", "Toxicology case reports"],
        )

    def test_trend_snapshot_compares_seven_day_windows(self):
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        history = [
            {
                "recorded": (now - timedelta(days=2)).isoformat(),
                "topics": ["Nitazenes", "Xylazine"],
            },
            {
                "recorded": (now - timedelta(days=3)).isoformat(),
                "topics": ["Nitazenes"],
            },
            {
                "recorded": (now - timedelta(days=10)).isoformat(),
                "topics": ["Xylazine", "Novel stimulants"],
            },
        ]
        snapshot = trend_snapshot(history, now)
        self.assertEqual(snapshot["Nitazenes"]["status"], "New")
        self.assertEqual(snapshot["Xylazine"]["status"], "Steady")
        self.assertEqual(snapshot["Novel stimulants"]["status"], "Cooling")

    def test_trend_report_explains_limits(self):
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        history = [
            {
                "recorded": (now - timedelta(days=1)).isoformat(),
                "topics": ["Medetomidine"],
            }
        ]
        report = render_trends(history, 50, now)
        self.assertIn("# Topic trend watch", report)
        self.assertIn("## New", report)
        self.assertIn("**Medetomidine:** 1 current signal(s)", report)
        self.assertIn("not prevalence estimates", report)


if __name__ == "__main__":
    unittest.main()

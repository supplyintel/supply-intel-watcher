import unittest

from watch_sources import (
    Item,
    classify_item,
    render_markdown,
    structured_groups,
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


if __name__ == "__main__":
    unittest.main()

# Supply Intel Watcher

A private, source-linked drug intelligence watcher. It checks 50 enabled official and specialist sources, deduplicates new material, scores relevance, archives complete reports, and creates a shorter high-priority email report.

## Phase 3 report structure

Reports now organize the same evidence into decision-friendly sections:

- **Massachusetts:** new reports, dashboard updates, and drug-checking alerts
- **Emerging substances:** nitazenes, medetomidine, xylazine, novel benzodiazepines, novel stimulants, and synthetic cannabinoids
- **Research:** new PubMed papers, toxicology case reports, and mortality studies
- **Implications:** court staff, treatment providers, harm reduction, and law enforcement
- **Usefulness flags:** presentation and one-pager candidates

An item can appear in more than one section because a single source may be relevant to several substances or audiences. Categories, priority tiers, corroboration labels, and implications are deterministic triage aids—not generated facts or individualized legal/clinical conclusions. Review the linked source before reuse.

## Outputs

- `reports/latest.md` and `reports/latest.html`: all new relevant items
- `reports/email.md` and `reports/email.html`: items meeting the configured email score threshold, plus source failures
- `reports/briefing.md`: five prioritized, source-linked presentation prompts
- `reports/one_pager.md`: concise substance and audience summary ready for editorial review
- `reports/editorial_queue.md`: review-now, monitor, and background tiers with cross-source signal labels
- `reports/archive/`: dated full reports
- `watch_state_v3.json`: deduplication, page hashes, initialization state, and source failure history

## Run locally

```bash
python -m pip install -r requirements.txt
python -m unittest discover -v
python watch_sources.py
```

Source definitions and keyword scoring live in `sources.yml`. GitHub Actions also validates pull requests without saving state or sending email.

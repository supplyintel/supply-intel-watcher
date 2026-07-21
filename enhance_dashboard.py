#!/usr/bin/env python3
"""Rebuild the generated dashboard with richer run-level analysis and a clearer UI."""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD = Path("reports/dashboard.html")


def extract_records(page: str) -> list[dict]:
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        page,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("Dashboard data block was not found.")
    return json.loads(match.group(1).replace("<\\/", "</"))


def extract_metric(page: str, label: str, default: int = 0) -> int:
    match = re.search(rf"<strong>(\d+)</strong>{re.escape(label)}", page)
    return int(match.group(1)) if match else default


def top_rows(counter: Counter, limit: int = 6) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda pair: (-pair[1], pair[0].lower()))[:limit]


def render(records: list[dict], checked_count: int, failure_count: int) -> str:
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    priority_counts = Counter(record.get("priority") or "Unclassified" for record in records)
    source_counts = Counter(record.get("source") or "Unknown source" for record in records)
    substance_counts = Counter(
        substance
        for record in records
        for substance in record.get("substances", [])
    )
    audience_counts = Counter(
        audience
        for record in records
        for audience in record.get("audiences", [])
    )
    massachusetts_count = sum(
        record.get("section") == "Massachusetts"
        or "massachusetts" in json.dumps(record).lower()
        for record in records
    )
    cross_source_count = sum(
        str(record.get("evidence", "")).startswith("Cross-source")
        for record in records
    )
    generated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    source_rows = "".join(
        f'<li><span>{html.escape(name)}</span><strong>{count}</strong></li>'
        for name, count in top_rows(source_counts)
    ) or '<li class="muted">No new source items</li>'
    substance_rows = "".join(
        f'<li><span>{html.escape(name)}</span><strong>{count}</strong></li>'
        for name, count in top_rows(substance_counts)
    ) or '<li class="muted">No emerging substances matched</li>'
    audience_rows = "".join(
        f'<li><span>{html.escape(name)}</span><strong>{count}</strong></li>'
        for name, count in top_rows(audience_counts)
    ) or '<li class="muted">No audience matches</li>'

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supply Intelligence Dashboard</title>
<style>
:root{{--navy:#132f46;--navy2:#1d4965;--blue:#1f6f9e;--teal:#137d79;--red:#a43b36;--amber:#9a6500;--green:#30734d;--ink:#17242d;--muted:#5b6b76;--line:#d9e2e7;--paper:#fff;--bg:#f3f6f8;--soft:#edf4f7}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,Arial,sans-serif;line-height:1.45}}
a{{color:#155f8c}} header{{background:linear-gradient(120deg,var(--navy),var(--navy2));color:#fff;padding:30px max(20px,calc((100% - 1240px)/2)) 46px}}
.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#bcd6e5;font-weight:700}} header h1{{margin:5px 0 7px;font-size:clamp(28px,4vw,44px);line-height:1.08}} header p{{margin:0;color:#d8e7ef}}
main{{max-width:1240px;margin:-24px auto 0;padding:0 20px 28px}} .metrics{{display:grid;grid-template-columns:repeat(6,minmax(125px,1fr));gap:10px}}
.metric,.panel,.card,.controls{{background:var(--paper);border:1px solid var(--line);box-shadow:0 5px 18px rgba(18,46,66,.06)}} .metric{{border-radius:12px;padding:15px;min-height:96px}}
.metric strong{{display:block;font-size:29px;line-height:1.1;color:var(--blue)}} .metric span{{display:block;margin-top:6px;color:var(--muted);font-size:13px}}
.section-title{{margin:25px 0 10px;font-size:19px}} .overview{{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:12px}}
.panel{{border-radius:12px;padding:16px}} .panel h2{{font-size:15px;margin:0 0 11px}} .bar-row{{display:grid;grid-template-columns:100px 1fr 30px;gap:8px;align-items:center;margin:10px 0;font-size:13px}}
.bar{{height:9px;background:#e8eef1;border-radius:999px;overflow:hidden}} .bar>span{{display:block;height:100%;background:var(--blue);border-radius:999px}}
.rank{{list-style:none;margin:0;padding:0}} .rank li{{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #edf1f3;font-size:13px}} .rank li:last-child{{border-bottom:0}}
.controls{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:2fr repeat(5,1fr) auto;gap:8px;padding:12px;margin:18px 0 12px;border-radius:12px}}
.controls input,.controls select,.controls button{{width:100%;min-height:42px;border:1px solid #b8c6ce;border-radius:8px;background:#fff;padding:9px;font:inherit}} .controls button{{cursor:pointer;background:#eef4f7;color:#244858;font-weight:700}}
.status-row{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:8px 2px 12px;color:var(--muted);font-size:13px}} .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.card{{border-radius:12px;padding:17px;border-left:5px solid var(--blue)}} .card.review-now{{border-left-color:var(--red)}} .card.monitor{{border-left-color:var(--amber)}} .card.background{{border-left-color:#7b8c96}}
.card h2{{font-size:18px;line-height:1.3;margin:0 0 8px}} .meta{{color:var(--muted);font-size:12px;margin:7px 0}} .summary{{font-size:14px;margin:10px 0}}
.tags{{display:flex;flex-wrap:wrap;gap:5px}} .tag{{font-size:11px;border-radius:999px;padding:4px 8px;background:var(--soft);color:#284956}} .tag.priority{{font-weight:700}} .tag.review-now{{background:#f8e7e5;color:#812b27}} .tag.monitor{{background:#fff1d6;color:#775000}}
.card-actions{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:11px;padding-top:10px;border-top:1px solid #edf1f3}} .source-link{{font-weight:700;font-size:13px}} .empty{{grid-column:1/-1;padding:28px;background:#fff;border:1px solid var(--line);border-radius:12px}}
.muted{{color:var(--muted)}} footer{{max-width:1240px;margin:auto;padding:10px 20px 30px;color:var(--muted);font-size:12px}}
@media(max-width:1000px){{.metrics{{grid-template-columns:repeat(3,1fr)}}.overview{{grid-template-columns:1fr 1fr}}.controls{{grid-template-columns:1fr 1fr 1fr}}}}
@media(max-width:680px){{header{{padding-bottom:38px}}main{{padding:0 12px 22px}}.metrics{{grid-template-columns:1fr 1fr}}.overview,.grid{{grid-template-columns:1fr}}.controls{{position:static;grid-template-columns:1fr}}.status-row{{align-items:flex-start;flex-direction:column}}}}
</style></head><body>
<header><div class="eyebrow">Pharmacist-led surveillance triage</div><h1>Supply Intelligence Dashboard</h1><p>Last rebuilt {html.escape(generated)} · Signals require review of the linked source</p></header>
<main>
<section class="metrics" aria-label="Current run metrics">
<div class="metric"><strong>{checked_count}</strong><span>sources checked</span></div>
<div class="metric"><strong>{len(records)}</strong><span>new relevant items</span></div>
<div class="metric"><strong>{priority_counts.get('Review now',0)}</strong><span>review now</span></div>
<div class="metric"><strong>{massachusetts_count}</strong><span>Massachusetts signals</span></div>
<div class="metric"><strong>{cross_source_count}</strong><span>cross-source signals</span></div>
<div class="metric"><strong>{failure_count}</strong><span>source failures</span></div>
</section>
<h2 class="section-title">What this run contains</h2>
<section class="overview">
<div class="panel"><h2>Priority mix</h2><div id="priority-bars"></div></div>
<div class="panel"><h2>Leading substances</h2><ul class="rank">{substance_rows}</ul></div>
<div class="panel"><h2>Leading sources</h2><ul class="rank">{source_rows}</ul></div>
<div class="panel"><h2>Audience matches</h2><ul class="rank">{audience_rows}</ul></div>
</section>
<section class="controls" aria-label="Dashboard filters">
<input id="search" type="search" placeholder="Search title, source, summary, substance…" aria-label="Search intelligence">
<select id="priority"><option value="">All priorities</option><option>Review now</option><option>Monitor</option><option>Background</option></select>
<select id="section"><option value="">All sections</option></select>
<select id="substance"><option value="">All substances</option></select>
<select id="audience"><option value="">All audiences</option></select>
<select id="source"><option value="">All sources</option></select>
<button id="reset" type="button">Clear</button>
</section>
<div class="status-row"><span id="status" aria-live="polite"></span><span>Sorted by priority score</span></div>
<section id="results" class="grid"></section>
</main>
<footer>Counts are newly detected source items, not prevalence estimates or proof of a change in the drug supply. Automated labels support triage only.</footer>
<script id="dashboard-data" type="application/json">{data_json}</script>
<script>
const records=JSON.parse(document.getElementById('dashboard-data').textContent);
const ids=['search','priority','section','substance','audience','source']; const controls=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));
const results=document.getElementById('results'),status=document.getElementById('status');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const fill=(el,values)=>[...new Set(values.flat().filter(Boolean))].sort((a,b)=>a.localeCompare(b)).forEach(v=>el.insertAdjacentHTML('beforeend','<option>'+esc(v)+'</option>'));
fill(controls.section,records.map(r=>[r.section])); fill(controls.substance,records.map(r=>r.substances||[])); fill(controls.audience,records.map(r=>r.audiences||[])); fill(controls.source,records.map(r=>[r.source]));
const counts=records.reduce((a,r)=>(a[r.priority]=(a[r.priority]||0)+1,a),{{}}); const max=Math.max(1,...Object.values(counts));
document.getElementById('priority-bars').innerHTML=['Review now','Monitor','Background'].map(k=>'<div class="bar-row"><span>'+k+'</span><div class="bar"><span style="width:'+((counts[k]||0)/max*100)+'%"></span></div><strong>'+(counts[k]||0)+'</strong></div>').join('');
function render(){{
 const q=controls.search.value.trim().toLowerCase();
 const shown=records.filter(r=>{{const hay=JSON.stringify(r).toLowerCase();return(!q||hay.includes(q))&&(!controls.priority.value||r.priority===controls.priority.value)&&(!controls.section.value||r.section===controls.section.value)&&(!controls.substance.value||(r.substances||[]).includes(controls.substance.value))&&(!controls.audience.value||(r.audiences||[]).includes(controls.audience.value))&&(!controls.source.value||r.source===controls.source.value)}});
 status.textContent='Showing '+shown.length+' of '+records.length+' items';
 results.innerHTML=shown.length?shown.map(r=>{{const cls=(r.priority||'background').toLowerCase().replaceAll(' ','-');const tags=[...(r.substances||[]),...(r.audiences||[])];return '<article class="card '+cls+'"><h2><a href="'+esc(r.url)+'" target="_blank" rel="noopener">'+esc(r.title)+'</a></h2><div class="tags"><span class="tag priority '+cls+'">'+esc(r.priority)+'</span><span class="tag">'+esc(r.section)+'</span>'+tags.map(v=>'<span class="tag">'+esc(v)+'</span>').join('')+'</div><p class="meta">'+esc(r.source)+' · '+esc(r.published||'Date not supplied')+' · Relevance score '+esc(r.score)+'</p><p class="summary">'+esc(r.summary||'No source summary was supplied.')+'</p><div class="card-actions"><span class="meta">'+esc(r.evidence)+'</span><a class="source-link" href="'+esc(r.url)+'" target="_blank" rel="noopener">Open source</a></div></article>'}}).join(''):'<div class="empty">No items match the selected filters.</div>';
}}
Object.values(controls).forEach(el=>el.addEventListener('input',render)); document.getElementById('reset').addEventListener('click',()=>{{Object.values(controls).forEach(el=>el.value='');render()}}); render();
</script></body></html>'''


def main() -> int:
    if not DASHBOARD.exists():
        raise FileNotFoundError(f"Missing generated dashboard: {DASHBOARD}")
    current = DASHBOARD.read_text(encoding="utf-8")
    records = extract_records(current)
    checked = extract_metric(current, "sources checked", 0)
    failures = extract_metric(current, "source failures", 0)
    DASHBOARD.write_text(render(records, checked, failures), encoding="utf-8")
    print(f"Enhanced dashboard with {len(records)} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

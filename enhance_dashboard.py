#!/usr/bin/env python3
"""Render the surveillance feed as a Massachusetts-first intelligence dashboard."""
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
    for pattern in (
        rf'<strong[^>]*>(\d+)</strong>\s*<span[^>]*>{re.escape(label)}</span>',
        rf'<strong>(\d+)</strong>{re.escape(label)}',
    ):
        match = re.search(pattern, page, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return default


def top_rows(counter: Counter, limit: int = 6) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda pair: (-pair[1], pair[0].lower()))[:limit]


def includes_massachusetts(record: dict) -> bool:
    return record.get("section") == "Massachusetts" or "massachusetts" in json.dumps(record).lower()


def why_it_matters(record: dict) -> str:
    audiences = record.get("audiences") or []
    substances = record.get("substances") or []
    priority = record.get("priority") or "Background"
    if priority == "Review now":
        return "Review promptly and confirm the source details before operational use."
    if substances:
        return f"Potentially relevant to surveillance of {', '.join(substances[:2])}."
    if audiences:
        return f"Potentially useful for {', '.join(audiences[:2])}."
    return "Useful background for situational awareness; confirm geography and methods before citing."


def render(records: list[dict], checked_count: int, failure_count: int) -> str:
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    priority_counts = Counter(record.get("priority") or "Unclassified" for record in records)
    source_counts = Counter(record.get("source") or "Unknown source" for record in records)
    substance_counts = Counter(s for record in records for s in record.get("substances", []))
    audience_counts = Counter(a for record in records for a in record.get("audiences", []))
    ma_count = sum(includes_massachusetts(record) for record in records)
    cross_source_count = sum(str(record.get("evidence", "")).startswith("Cross-source") for record in records)
    generated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    urgent = [r for r in records if r.get("priority") == "Review now"]
    top_signal = urgent[0] if urgent else (records[0] if records else None)
    top_signal_html = (
        f'<a href="{html.escape(top_signal.get("url", "#"))}" target="_blank" rel="noopener">'
        f'{html.escape(top_signal.get("title", "Open source"))}</a>'
        if top_signal else "No new intelligence items were detected in this run."
    )

    source_rows = "".join(
        f'<li><span>{html.escape(name)}</span><strong>{count}</strong></li>'
        for name, count in top_rows(source_counts)
    ) or '<li class="muted">No new source items</li>'
    substance_rows = "".join(
        f'<li><span>{html.escape(name)}</span><strong>{count}</strong></li>'
        for name, count in top_rows(substance_counts)
    ) or '<li class="muted">No emerging substances matched</li>'
    audience_rows = "".join(
        f'<button class="audience-chip" data-audience="{html.escape(name)}">'
        f'<span>{html.escape(name)}</span><strong>{count}</strong></button>'
        for name, count in top_rows(audience_counts, 8)
    ) or '<span class="muted">No audience matches</span>'

    for record in records:
        record["why_it_matters"] = why_it_matters(record)
        record["is_massachusetts"] = includes_massachusetts(record)
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Massachusetts Drug Supply Intelligence</title>
<style>
:root{{--navy:#123047;--navy2:#1a4b68;--blue:#1f6f96;--teal:#167a77;--red:#a63b35;--amber:#9a6505;--ink:#17242d;--muted:#60717d;--line:#d8e2e7;--paper:#fff;--bg:#f3f6f8;--soft:#edf3f6;--shadow:0 10px 28px rgba(18,48,71,.08)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}}a{{color:#125f8d}}button,input,select{{font:inherit}}
.topbar{{background:linear-gradient(115deg,var(--navy),var(--navy2));color:white;padding:28px max(18px,calc((100% - 1320px)/2)) 78px}}.brand{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}}.eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#bfd7e3;font-weight:800}}h1{{margin:7px 0 8px;font-size:clamp(32px,4.5vw,52px);line-height:1.02;letter-spacing:-.04em}}.subtitle{{max-width:800px;margin:0;color:#dce9ef}}.stamp{{border:1px solid rgba(255,255,255,.25);border-radius:10px;padding:10px 12px;font-size:12px;color:#dce9ef;white-space:nowrap}}
main{{max-width:1320px;margin:-50px auto 0;padding:0 18px 34px}}.surface{{background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}}
.brief{{padding:18px}}.brief-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}.brief-head h2{{margin:0;font-size:18px}}.brief-head p{{margin:3px 0 0;color:var(--muted);font-size:12px}}.signal{{margin:14px 0;padding:14px;border-radius:12px;background:#eef6f8;border-left:5px solid var(--teal)}}.signal strong{{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#35606f;margin-bottom:4px}}.signal a{{font-weight:800;text-decoration:none;color:#153f58}}
.metrics{{display:grid;grid-template-columns:repeat(6,minmax(115px,1fr));gap:9px}}.metric{{padding:14px;border:1px solid var(--line);border-radius:12px;background:#fbfcfd;min-height:88px}}.metric strong{{display:block;font-size:29px;line-height:1;color:var(--blue)}}.metric span{{display:block;margin-top:8px;color:var(--muted);font-size:12px}}.metric.alert strong{{color:var(--red)}}.metric.warn strong{{color:var(--amber)}}
.audience-strip{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}}.audience-chip{{display:flex;gap:8px;align-items:center;border:1px solid #c8d7dd;border-radius:999px;background:#fff;padding:7px 11px;cursor:pointer;color:#294756}}.audience-chip.active,.audience-chip:hover{{background:#e8f2f5;border-color:#8db4c4}}.audience-chip strong{{font-size:11px;background:#dcebee;border-radius:999px;padding:2px 6px}}
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px;margin-top:16px}}.sidebar{{display:flex;flex-direction:column;gap:14px}}.panel{{padding:16px}}.panel h2{{font-size:15px;margin:0 0 12px}}.panel h3{{font-size:13px;margin:18px 0 8px;color:#365464}}.rank{{list-style:none;margin:0;padding:0}}.rank li{{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #edf1f3;font-size:13px}}.rank li:last-child{{border:0}}.notice{{border-left:4px solid var(--amber);background:#fff8e8;padding:12px;border-radius:8px;font-size:12px;color:#5f4a18}}
.controls{{position:sticky;top:0;z-index:10;padding:12px;margin-bottom:12px;display:grid;grid-template-columns:2fr repeat(4,1fr) auto;gap:8px}}.controls input,.controls select,.controls button{{min-height:42px;border:1px solid #b9c8d0;border-radius:9px;background:white;padding:9px 10px;color:#203744}}.controls button{{cursor:pointer;background:#edf4f7;font-weight:750}}
.results-head{{display:flex;justify-content:space-between;align-items:end;gap:12px;margin:18px 2px 10px}}.results-head h2{{font-size:20px;margin:0}}.status{{font-size:12px;color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.card{{background:white;border:1px solid var(--line);border-left:5px solid var(--blue);border-radius:13px;padding:17px;box-shadow:0 4px 15px rgba(18,48,71,.045)}}.card.review-now{{border-left-color:var(--red)}}.card.monitor{{border-left-color:var(--amber)}}.card h3{{font-size:17px;line-height:1.28;margin:0 0 8px}}.card h3 a{{color:#173d56;text-decoration:none}}.card h3 a:hover{{text-decoration:underline}}.tags{{display:flex;flex-wrap:wrap;gap:5px}}.tag{{font-size:10px;border-radius:999px;padding:4px 8px;background:var(--soft);color:#2d4d5a}}.tag.priority{{font-weight:800}}.tag.review-now{{background:#f8e7e5;color:#812b27}}.tag.monitor{{background:#fff1d6;color:#775000}}.tag.ma{{background:#e7f2ee;color:#28604d;font-weight:800}}.meta{{font-size:11px;color:var(--muted);margin:8px 0}}.summary{{font-size:13px;color:#263842;margin:10px 0}}.why{{background:#f6f8f9;border-radius:9px;padding:10px 11px;font-size:12px;color:#425863}}.why strong{{color:#203d4d}}.card-actions{{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-top:12px;padding-top:10px;border-top:1px solid #edf1f3}}.source-link{{font-size:12px;font-weight:800}}.empty{{grid-column:1/-1;padding:30px;text-align:center;background:white;border:1px solid var(--line);border-radius:12px;color:var(--muted)}}
.bar-row{{display:grid;grid-template-columns:84px 1fr 28px;gap:8px;align-items:center;margin:10px 0;font-size:12px}}.bar{{height:8px;background:#e7edf0;border-radius:99px;overflow:hidden}}.bar span{{display:block;height:100%;background:var(--blue)}}.timeline{{display:flex;align-items:flex-end;gap:5px;height:110px;padding-top:8px}}.timeline-col{{flex:1;min-width:7px;height:100%;display:flex;align-items:flex-end}}.timeline-bar{{width:100%;min-height:2px;border-radius:4px 4px 1px 1px;background:var(--teal)}}.timeline-labels{{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:4px}}footer{{max-width:1320px;margin:auto;padding:0 18px 28px;color:var(--muted);font-size:11px}}
@media(max-width:1050px){{.metrics{{grid-template-columns:repeat(3,1fr)}}.layout{{grid-template-columns:1fr}}.sidebar{{display:grid;grid-template-columns:repeat(2,1fr)}}.controls{{grid-template-columns:2fr repeat(2,1fr)}}}}@media(max-width:700px){{.brand{{display:block}}.stamp{{display:inline-block;margin-top:14px;white-space:normal}}main{{padding:0 11px 24px}}.metrics{{grid-template-columns:1fr 1fr}}.grid,.sidebar{{grid-template-columns:1fr}}.controls{{position:static;grid-template-columns:1fr}}}}
</style></head><body>
<header class="topbar"><div class="brand"><div><div class="eyebrow">Pharmacist-led surveillance triage</div><h1>Massachusetts drug supply intelligence</h1><p class="subtitle">A Massachusetts-first briefing for newly detected reports, alerts, publications, and surveillance updates. Open the linked source before operational use.</p></div><div class="stamp">Last rebuilt<br><strong>{html.escape(generated)}</strong></div></div></header>
<main><section class="surface brief"><div class="brief-head"><div><h2>Executive intelligence brief</h2><p>Counts describe newly detected source items, not prevalence or confirmed changes in the street supply.</p></div></div><div class="signal"><strong>Leading signal in this run</strong>{top_signal_html}</div>
<div class="metrics"><div class="metric"><strong>{checked_count}</strong><span>sources checked</span></div><div class="metric"><strong>{len(records)}</strong><span>new relevant items</span></div><div class="metric alert"><strong>{priority_counts.get('Review now',0)}</strong><span>review now</span></div><div class="metric"><strong>{ma_count}</strong><span>Massachusetts signals</span></div><div class="metric"><strong>{cross_source_count}</strong><span>cross-source signals</span></div><div class="metric warn"><strong>{failure_count}</strong><span>source failures</span></div></div><div class="audience-strip">{audience_rows}</div></section>
<div class="layout"><div><section class="surface controls"><input id="search" type="search" placeholder="Search title, source, summary, substance"><select id="priority"><option value="">All priorities</option><option>Review now</option><option>Monitor</option><option>Background</option></select><select id="section"><option value="">All sections</option></select><select id="substance"><option value="">All substances</option></select><select id="source"><option value="">All sources</option></select><button id="reset" type="button">Clear filters</button></section><div class="results-head"><div><h2>Intelligence items</h2><div id="status" class="status"></div></div></div><section id="results" class="grid"></section></div>
<aside class="sidebar"><section class="surface panel"><h2>Priority mix</h2><div id="priority-bars"></div><h3>Publication timeline</h3><div id="timeline" class="timeline"></div><div id="timeline-labels" class="timeline-labels"></div></section><section class="surface panel"><h2>Leading substances</h2><ul class="rank">{substance_rows}</ul><h3>Leading sources</h3><ul class="rank">{source_rows}</ul></section><section class="surface panel"><h2>Interpretation note</h2><div class="notice">Automated labels support triage. A signal may reflect a new publication, revised webpage, or repeated topic rather than a new drug-supply event.</div></section></aside></div></main>
<footer>For informational surveillance use. Review dates, methods, geography, denominators, and source limitations before citing an item.</footer>
<script id="dashboard-data" type="application/json">{data_json}</script><script>
const records=JSON.parse(document.getElementById('dashboard-data').textContent);const ids=['search','priority','section','substance','source'];const controls=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));const results=document.getElementById('results'),status=document.getElementById('status');let selectedAudience='';const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));const fill=(el,values)=>[...new Set(values.flat().filter(Boolean))].sort((a,b)=>a.localeCompare(b)).forEach(v=>el.insertAdjacentHTML('beforeend','<option>'+esc(v)+'</option>'));fill(controls.section,records.map(r=>[r.section]));fill(controls.substance,records.map(r=>r.substances||[]));fill(controls.source,records.map(r=>[r.source]));
const counts=records.reduce((a,r)=>(a[r.priority]=(a[r.priority]||0)+1,a),{{}}),max=Math.max(1,...Object.values(counts));document.getElementById('priority-bars').innerHTML=['Review now','Monitor','Background'].map(k=>'<div class="bar-row"><span>'+k+'</span><div class="bar"><span style="width:'+((counts[k]||0)/max*100)+'%"></span></div><strong>'+(counts[k]||0)+'</strong></div>').join('');
function parseDate(v){{const d=new Date(v);return Number.isNaN(d.getTime())?null:d}}function drawTimeline(){{const dated=records.map(r=>parseDate(r.published)).filter(Boolean),end=dated.length?new Date(Math.max(...dated.map(d=>d.getTime()))):new Date(),start=new Date(end);start.setDate(start.getDate()-27);const buckets=Array(28).fill(0);records.forEach(r=>{{const d=parseDate(r.published);if(!d)return;const i=Math.floor((d-start)/86400000);if(i>=0&&i<28)buckets[i]++}});const m=Math.max(1,...buckets);document.getElementById('timeline').innerHTML=buckets.map(v=>'<div class="timeline-col"><div class="timeline-bar" style="height:'+Math.max(2,v/m*100)+'%"></div></div>').join('');document.getElementById('timeline-labels').innerHTML='<span>'+start.toLocaleDateString(undefined,{{month:'short',day:'numeric'}})+'</span><span>'+end.toLocaleDateString(undefined,{{month:'short',day:'numeric'}})+'</span>'}}
function render(){{const q=controls.search.value.trim().toLowerCase();const shown=records.filter(r=>{{const hay=JSON.stringify(r).toLowerCase();return(!q||hay.includes(q))&&(!controls.priority.value||r.priority===controls.priority.value)&&(!controls.section.value||r.section===controls.section.value)&&(!controls.substance.value||(r.substances||[]).includes(controls.substance.value))&&(!controls.source.value||r.source===controls.source.value)&&(!selectedAudience||(r.audiences||[]).includes(selectedAudience))}});status.textContent='Showing '+shown.length+' of '+records.length+' items'+(selectedAudience?' for '+selectedAudience:'');results.innerHTML=shown.length?shown.map(r=>{{const cls=(r.priority||'background').toLowerCase().replaceAll(' ','-');const tags=[...(r.substances||[]),...(r.audiences||[])];return '<article class="card '+cls+'"><h3><a href="'+esc(r.url)+'" target="_blank" rel="noopener">'+esc(r.title)+'</a></h3><div class="tags"><span class="tag priority '+cls+'">'+esc(r.priority)+'</span><span class="tag">'+esc(r.section)+'</span>'+(r.is_massachusetts?'<span class="tag ma">Massachusetts</span>':'')+tags.map(v=>'<span class="tag">'+esc(v)+'</span>').join('')+'</div><p class="meta">'+esc(r.source)+' · '+esc(r.published||'Date not supplied')+' · relevance '+esc(r.score)+'</p><p class="summary">'+esc(r.summary||'No source summary was supplied.')+'</p><div class="why"><strong>Why this matters:</strong> '+esc(r.why_it_matters)+'</div><div class="card-actions"><span class="meta">'+esc(r.evidence)+'</span><a class="source-link" href="'+esc(r.url)+'" target="_blank" rel="noopener">Open source</a></div></article>'}}).join(''):'<div class="empty">No items match the selected filters.</div>'}}
Object.values(controls).forEach(el=>el.addEventListener('input',render));document.getElementById('reset').addEventListener('click',()=>{{Object.values(controls).forEach(el=>el.value='');selectedAudience='';document.querySelectorAll('.audience-chip').forEach(b=>b.classList.remove('active'));render()}});document.querySelectorAll('.audience-chip').forEach(btn=>btn.addEventListener('click',()=>{{selectedAudience=selectedAudience===btn.dataset.audience?'':btn.dataset.audience;document.querySelectorAll('.audience-chip').forEach(b=>b.classList.toggle('active',b.dataset.audience===selectedAudience));render()}}));drawTimeline();render();
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

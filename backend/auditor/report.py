"""Render a stored change report as one self-contained HTML page (inline SVG map, no external assets)."""
from __future__ import annotations

from datetime import datetime
from html import escape

from jinja2 import Environment

COLORS = {"moved": "#2563eb", "missing": "#dc2626", "new": "#16a34a"}
LABELS = {"moved": "Moved", "missing": "Missing", "new": "New"}


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _points(changes):
    for c in changes:
        for k in ("before", "after"):
            if c[k]:
                yield c[k]["x"], c[k]["y"]


def build_map_svg(changes: list[dict], width: int = 820, max_height: int = 520) -> str:
    pts = list(_points(changes))
    if not pts:
        return ('<svg viewBox="0 0 820 120" xmlns="http://www.w3.org/2000/svg" role="img" '
                'aria-label="No changes"><text x="410" y="65" text-anchor="middle" '
                'class="maptext">No changes detected</text></svg>')
    pad = 1.0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    w_m, h_m = max(x1 - x0, 2.0), max(y1 - y0, 2.0)
    scale = min((width - 20) / w_m, (max_height - 20) / h_m)
    W, H = w_m * scale + 20, h_m * scale + 20

    def sx(x): return 10 + (x - x0) * scale
    def sy(y): return H - 10 - (y - y0) * scale          # map y points up, SVG y points down

    out = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="Map of changes">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#2563eb"/></marker></defs>']
    # 1 m grid
    gx = int(x0)
    while gx <= x1:
        out.append(f'<line x1="{sx(gx):.1f}" y1="10" x2="{sx(gx):.1f}" y2="{H-10:.1f}" class="grid"/>')
        gx += 1
    gy = int(y0)
    while gy <= y1:
        out.append(f'<line x1="10" y1="{sy(gy):.1f}" x2="{W-10:.1f}" y2="{sy(gy):.1f}" class="grid"/>')
        gy += 1

    placed: list[tuple[float, float]] = []      # label anchor points already used

    def label_pos(px: float, py: float) -> tuple[float, float]:
        lx, ly = px + 9, py - 8
        while any(abs(lx - ox) < 80 and abs(ly - oy) < 13 for ox, oy in placed):
            ly += 13                              # stack downwards until clear
        placed.append((lx, ly))
        return lx, ly

    for i, c in enumerate(changes, 1):
        col = COLORS[c["type"]]
        b, a = c["before"], c["after"]
        if c["type"] == "moved":
            bx, by, ax, ay = sx(b["x"]), sy(b["y"]), sx(a["x"]), sy(a["y"])
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="6" fill="none" stroke="{col}" '
                       f'stroke-width="2" stroke-dasharray="3 2"/>')
            out.append(f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" stroke="{col}" '
                       f'stroke-width="2" marker-end="url(#arr)"/>')
            out.append(f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="6" fill="{col}"/>')
            lx, ly = ax, ay
        elif c["type"] == "missing":
            lx, ly = sx(b["x"]), sy(b["y"])
            out.append(f'<path d="M{lx-6:.1f},{ly-6:.1f} L{lx+6:.1f},{ly+6:.1f} M{lx-6:.1f},{ly+6:.1f} '
                       f'L{lx+6:.1f},{ly-6:.1f}" stroke="{col}" stroke-width="3" fill="none"/>')
        else:
            lx, ly = sx(a["x"]), sy(a["y"])
            out.append(f'<path d="M{lx-7:.1f},{ly:.1f} L{lx+7:.1f},{ly:.1f} M{lx:.1f},{ly-7:.1f} '
                       f'L{lx:.1f},{ly+7:.1f}" stroke="{col}" stroke-width="3" fill="none"/>')
        tx, ty = label_pos(lx, ly)
        out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" class="maptext">{i} {escape(c["cls"])}</text>')
    out.append("</svg>")
    return "".join(out)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Change report #{{ r.id }} - {{ area }}</title>
<style>
:root { --bg:#fff; --fg:#1f2937; --muted:#6b7280; --card:#f9fafb; --line:#e5e7eb; }
@media (prefers-color-scheme: dark) { :root { --bg:#111827; --fg:#f3f4f6; --muted:#9ca3af; --card:#1f2937; --line:#374151; } }
body { font-family: system-ui, sans-serif; background:var(--bg); color:var(--fg); max-width: 900px; margin: 0 auto; padding: 24px 16px; }
h1 { margin: 0 0 4px; font-size: 1.5rem; } .sub { color: var(--muted); margin-bottom: 20px; }
.cards { display:flex; gap:12px; flex-wrap:wrap; margin-bottom: 20px; }
.card { flex:1 1 120px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.card b { display:block; font-size:1.7rem; } .card span { color:var(--muted); font-size:.85rem; }
.mapbox { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px; overflow-x:auto; }
.mapbox svg { width:100%; height:auto; min-width:480px; }
.grid { stroke: var(--line); stroke-width:1; } .maptext { fill: var(--fg); font-size: 12px; }
table { width:100%; border-collapse: collapse; margin-top: 20px; font-size:.92rem; }
th, td { text-align:left; padding:8px; border-bottom:1px solid var(--line); } th { color:var(--muted); font-weight:600; }
.badge { color:#fff; border-radius:999px; padding:2px 10px; font-size:.8rem; }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:.85rem; margin-bottom:8px; align-items:center; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:3px; vertical-align:middle; }
.legend i.dot { margin-right:5px; } .muted { color: var(--muted); }
.empty { padding: 24px; text-align:center; color: var(--muted); }
.wrap { overflow-x:auto; }
</style></head><body>
<h1>Change report #{{ r.id }}</h1>
<div class="sub">Area <b>{{ area }}</b> &middot; baseline <b>{{ baseline.name }}</b> ({{ base_time }}) &rarr; current <b>{{ current.name }}</b> ({{ curr_time }})</div>
<div class="cards">
  {% for key in ['moved','missing','new'] %}
  <div class="card"><b style="color:{{ colors[key] }}">{{ r.summary[key] }}</b><span>{{ labels[key] }}</span></div>
  {% endfor %}
  <div class="card"><b>{{ r.summary.unchanged }}</b><span>Unchanged</span></div>
</div>
<div class="legend">
  <span><i style="border:2px dashed {{ colors.moved }}"></i><i class="dot" style="background:{{ colors.moved }}"></i> Moved (hollow = was, solid = now)</span>
  <span style="color:{{ colors.missing }}">&#10005; Missing</span>
  <span style="color:{{ colors.new }}">&#10010; New</span>
  <span class="muted">Grid: 1 m. Only changed items are drawn.</span>
</div>
<div class="mapbox">{{ map_svg|safe }}</div>
{% if r.changes %}
<div class="wrap"><table>
<tr><th>#</th><th>Change</th><th>Item</th><th>Was at (m)</th><th>Now at (m)</th><th>Moved (m)</th></tr>
{% for c in r.changes %}
<tr><td>{{ loop.index }}</td>
<td><span class="badge" style="background:{{ colors[c.type] }}">{{ labels[c.type] }}</span></td>
<td>{{ c.cls }}</td>
<td>{% if c.before %}{{ '%.1f'|format(c.before.x) }}, {{ '%.1f'|format(c.before.y) }}{% else %}&ndash;{% endif %}</td>
<td>{% if c.after %}{{ '%.1f'|format(c.after.x) }}, {{ '%.1f'|format(c.after.y) }}{% else %}&ndash;{% endif %}</td>
<td>{% if c.distance is not none %}{{ '%.1f'|format(c.distance) }}{% else %}&ndash;{% endif %}</td></tr>
{% endfor %}</table></div>
{% else %}<div class="empty">No changes between these patrols.</div>{% endif %}
</body></html>"""

_env = Environment(autoescape=True)
_tpl = _env.from_string(TEMPLATE)


def render_report_html(report: dict, baseline: dict, current: dict) -> str:
    return _tpl.render(
        r=report, baseline=baseline, current=current, area=current["area"],
        base_time=_fmt_time(baseline["created_at"]), curr_time=_fmt_time(current["created_at"]),
        colors=COLORS, labels=LABELS, map_svg=build_map_svg(report["changes"]),
    )

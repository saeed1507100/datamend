"""Read-only web view: lineage graph, current state, audit log. Standard library only."""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

from datamend import audit, lineage
from datamend.catalog import Catalog

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>datamend</title>
<style>
  :root {{ --bg: #fff; --fg: #1f2328; --muted: #656d76; --line: #d0d7de; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0d1117; --fg: #e6edf3; --muted: #8d96a0; --line: #30363d; }} }}
  body {{ background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 32px 0 8px; }}
  p.sub {{ color: var(--muted); margin: 0; }}
  .scroll {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 600; }}
  code {{ font-family: ui-monospace, monospace; }}
  .pill {{ padding: 1px 8px; border-radius: 10px; font-size: 12px; color: #1f2328; }}
  .auto_fix {{ background: #d3f9d8; }} .needs_approval {{ background: #ffe3e3; }}
  .blocked {{ background: #fff3bf; }} .suppressed {{ background: #e9ecef; }}
</style></head><body>
<h1>datamend</h1>
<p class="sub">{summary}</p>
<h2>Lineage</h2>
<pre class="mermaid">{graph}</pre>
<h2>Audit log</h2>
<div class="scroll"><table><thead><tr>
<th>At (UTC)</th><th>Incident</th><th>Decision</th><th>Action</th><th>Reason</th><th>Result</th>
</tr></thead><tbody>{rows}</tbody></table></div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({{ startOnLoad: true, theme: dark ? "dark" : "default" }});
</script>
</body></html>"""


def render(catalog: Catalog, db: Path) -> str:
    g = lineage.build(catalog)
    entries, states = [], {}
    if db.exists():
        with duckdb.connect(str(db), read_only=True) as con:
            if con.execute("SELECT count(*) FROM information_schema.tables "
                           "WHERE table_name = 'audit'").fetchone()[0]:
                entries, states = audit.entries(con), audit.latest_states(con)
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(str(x['at']))[:19]}</td><td><code>{e(x['incident'])}</code></td>"
        f"<td><span class='pill {e(x['decision'])}'>{e(x['decision'])}</span></td>"
        f"<td><code>{e(x['action'] or '')}</code></td><td>{e(x['reason'] or '')}</td>"
        f"<td>{e(x['result'] or '')}</td></tr>"
        for x in reversed(entries)
    ) or "<tr><td colspan='6'>No cycles run yet.</td></tr>"
    counts = {s: list(states.values()).count(s) for s in lineage.STYLES}
    summary = (f"{len(catalog.datasets)} datasets · {counts['auto_fix']} auto-fixed · "
               f"{counts['needs_approval']} need approval · {counts['blocked']} blocked")
    return PAGE.format(summary=e(summary), graph=e(lineage.mermaid(g, states)), rows=rows)


def serve(catalog: Catalog, db: Path, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = render(catalog, db).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

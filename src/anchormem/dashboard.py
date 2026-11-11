"""`anchormem dashboard`: one self-contained HTML page showing the repo's real memory state.

The page is rendered here in Python and works without JavaScript. Small inline scripts add
copy buttons, search and filters, and (with --serve) a live refresh when the repo changes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import Counter, OrderedDict
from html import escape
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, core, gitutil, handoff, store
from .advice import EXPLAIN, Snapshot, Step, snapshot, stale_step
from .confidence import band, counts, score

_LOG = re.compile(r"^## \[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\] (\S+) \| ([^|]*?)(?: \| (.*))?$")
STATUS_ORDER = [core.OK, core.CHANGED, core.MOVED, core.MISSING, core.UNVERIFIED, core.FLAGGED]
COLOR = {core.OK: "var(--ok)", core.CHANGED: "var(--stale)", core.MOVED: "var(--amber)",
         core.MISSING: "var(--stale)", core.UNVERIFIED: "var(--dim)", core.FLAGGED: "var(--amber)"}
STAMP, VERSION = "@@AM_STAMP@@", "@@AM_VERSION@@"


def read_log(root: Path) -> List[Tuple[str, str, str, str, str]]:
    f = root / store.DIR / "log.md"
    rows = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = _LOG.match(line.strip())
            if m:
                rows.append((m.group(1), m.group(2), m.group(3), m.group(4).strip(), (m.group(5) or "").strip()))
    return rows


def default_path(root: Path) -> Path:
    return root / store.STATE_DIR / "dashboard.html"


def _e(x) -> str:
    return escape(str(x), quote=True)


def _cmd(command: str) -> str:
    return (f'<div class="cmd"><code>{_e(command)}</code>'
            f'<button type="button" data-copy="{_e(command)}" aria-label="Copy command">copy</button></div>')


def _panel(n: int, title: str, tag: str, body: str) -> str:
    return (f'<section class="frame panel"><div class="ph"><span class="n">{n:02d}</span>'
            f'<span class="t">{_e(title)}</span><span class="tag">{_e(tag)}</span></div>'
            f'<div class="pb">{body}</div></section>')


def _empty(text: str, command: str = "") -> str:
    return f'<div class="empty"><p class="dim">{_e(text)}</p>{_cmd(command) if command else ""}</div>'


def _step(s: Step) -> str:
    label = {"fix": "check", "todo": "to do", "tip": "tip"}[s.level]
    return (f'<li class="step s-{s.level}"><span class="lv">{label}</span>'
            f'<div><p>{_e(s.text)}</p>{_cmd(s.command)}</div></li>')


def build(root: Path, live: bool = False, snap: Optional[Snapshot] = None) -> str:
    """The page as HTML. data-version is a hash of the content without the timestamp, for live refresh."""
    page = _page(root, snap or snapshot(root), live)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return page.replace(VERSION, _hash(page)).replace(STAMP, stamp)


def version(root: Path) -> str:
    return _hash(_page(root, snapshot(root), True))


def _hash(page: str) -> str:
    return hashlib.sha1(page.encode("utf-8")).hexdigest()[:12]


def _page(root: Path, s: Snapshot, live: bool) -> str:
    today = dt.date.today()
    retired = store.load_retired(root)
    handoffs = handoff.load_all(root)
    log = read_log(root)
    commit = gitutil.head(root) or "no git"
    mems, results, stale = s.mems, s.results, s.stale
    n_anchors = sum(len(r.anchors) for r in results)
    confirmed = len(mems) - len(s.unconfirmed)

    # ---- headline in plain words
    if not mems:
        verdict, vcls = "No memories yet. Start with the first step below.", "dim"
    elif stale:
        verdict, vcls = f"{len(stale)} of {len(mems)} memories need a look.", "stale"
    else:
        verdict, vcls = f"All {len(mems)} memories match the code.", "ok"

    stats = [("memories", len(mems), ""), ("need a look", len(stale), "s" if stale else "f"),
             ("approved", f"{confirmed}/{len(mems)}", ""), ("anchors", n_anchors, ""),
             ("handoffs", len(s.pending), ""), ("notes", len(s.notes), "")]
    head = "".join(f'<div class="stat"><span>{k}</span><b class="{c}">{_e(v)}</b></div>' for k, v, c in stats)

    # ---- do this next
    if s.steps:
        steps = f'<ol class="steps">{"".join(_step(x) for x in s.steps)}</ol>'
    else:
        steps = '<p class="ok big">Nothing to do. Every memory matches the code and has been approved.</p>'

    # ---- status strip with explanations
    sc = Counter(r.status for r in results)
    segs = "".join(f'<i style="flex:{sc[st]};background:{COLOR[st]}" title="{st}: {sc[st]}"></i>'
                   for st in STATUS_ORDER if sc[st]) or '<i style="flex:1;background:var(--line)"></i>'
    shown = [st for st in STATUS_ORDER if sc[st]] or [core.OK]
    legend = "".join(f'<div class="lg"><span class="pill" style="--c:{COLOR[st]}">{st}</span>'
                     f'<b>{sc[st]}</b><span class="dim">{_e(EXPLAIN[st])}</span></div>' for st in shown)
    strip = f'<div class="strip">{segs}</div><div class="legend">{legend}</div>'

    # ---- memory table
    rows = []
    for r in results:
        m = r.memory
        sc_ = score(m)
        applied, violated = counts(m)
        conf = (f'<span class="conf c-{band(sc_)}">{band(sc_)}</span><small class="dim">{applied} held · {violated} broke</small>'
                if applied + violated else '<span class="dim">no reports yet</span>')
        if m.origin != "agent":
            who = "a person"
        elif m.confirmed:
            who = f'an agent<div class="ok">approved {_e(m.confirmed)}</div>'
        else:
            who = 'an agent<div class="amb">not approved yet</div>'
        anchors = "".join(f'<li><i style="background:{COLOR[a.status]}"></i><code>{_e(a.anchor)}</code>'
                          + ("" if a.ok else f' <span class="st">{_e(a.status)}</span>') + '</li>' for a in r.anchors)
        extra = ""
        if r.flagged:
            extra += f'<p class="amb">reported out of date: {_e(r.flagged)}</p>'
        for a in r.anchors:
            if not a.ok and a.detail:
                extra += f'<p class="stale">{_e(a.detail)}</p>'
        if not r.ok:
            extra += _cmd(stale_step(r).command)
        body = f'<p class="body">{_e(m.body)}</p>' if m.body else ""
        tags = " ".join(t for t, on in (("attention", not r.ok), ("unconfirmed", m.unconfirmed_agent)) if on)
        rows.append(
            f'<tr class="{"bad" if not r.ok else ""}" data-tags="{tags}">'
            f'<td><span class="pill" style="--c:{COLOR[r.status]}" title="{_e(EXPLAIN[r.status])}">{_e(r.status)}</span></td>'
            f'<td><b>{_e(m.title)}</b><div class="dim">{_e(m.id)}</div>{body}{extra}</td>'
            f'<td><ul class="anc">{anchors}</ul></td><td>{who}</td><td>{conf}</td>'
            f'<td class="dim nw">{_e(m.verified or "—")}<div>{_e(m.commit)}</div></td></tr>')
    if rows:
        tools = (f'<div class="tools"><input id="q" type="search" placeholder="Search memories, files, ids…" aria-label="Search memories">'
                 f'<div class="chips" role="group" aria-label="Filter">'
                 f'<button type="button" data-mode="all" class="on">All {len(mems)}</button>'
                 f'<button type="button" data-mode="attention">Need a look {len(stale)}</button>'
                 f'<button type="button" data-mode="unconfirmed">Not approved {len(s.unconfirmed)}</button></div>'
                 f'<span id="shown" class="dim"></span></div>')
        table = (tools + '<div class="tw"><table><thead><tr><th>status</th><th>memory</th><th>about this code</th>'
                 '<th>written by</th><th>confidence</th><th>last checked</th></tr></thead><tbody>'
                 + "".join(rows) + '</tbody></table><p id="none" class="dim" hidden>No memories match.</p></div>')
    else:
        table = ('<div class="pad">' + _empty("No memories yet. Save one with:",
                 'anchormem add "<what is true>" --anchor <file>#<function> --body "<why>"') + '</div>')

    # ---- files map
    files: "OrderedDict[str, List[str]]" = OrderedDict()
    for r in results:
        for a in r.anchors:
            files.setdefault(a.anchor.path, []).append(a.status)
    most = max((len(v) for v in files.values()), default=1)
    fmap = "".join(
        f'<div class="bar"><code title="{_e(p)}">{_e(p)}</code><div class="tr">'
        + "".join(f'<i style="width:{100 / most:.2f}%;background:{COLOR[st]}"></i>' for st in sts)
        + f'</div><b>{len(sts)}</b></div>'
        for p, sts in sorted(files.items(), key=lambda kv: (-len(kv[1]), kv[0])))

    # ---- activity
    days = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
    per_day = Counter(d for d, *_ in log)
    peak = max([per_day[d.isoformat()] for d in days] + [1])
    cols = "".join(
        f'<div class="col" title="{d.isoformat()}: {per_day[d.isoformat()]} events">'
        f'<i style="height:{100 * per_day[d.isoformat()] / peak:.0f}%"></i><span>{d.day}</span></div>' for d in days)
    ops = Counter(op for _, _, op, _, _ in log)
    opline = " · ".join(f"{op} {n}" for op, n in ops.most_common())
    recent = "".join(
        f'<div><span class="dim">{_e(d[5:])} {_e(t)}</span> <b class="op op-{_e(op)}">{_e(op)}</b> {_e(mid)}'
        + (f' <span class="dim">· {_e(det)}</span>' if det else "") + '</div>'
        for d, t, op, mid, det in reversed(log[-40:]))

    # ---- handoffs
    ordered = sorted(sorted(handoffs, key=lambda h: h.created, reverse=True), key=lambda h: h.status != "pending")
    label = {"pending": "waiting", "accepted": "taken"}
    hrows = "".join(
        f'<div class="ho"><div><span class="pill" style="--c:{"var(--amber)" if h.status == "pending" else "var(--dim)"}">'
        f'{_e(label.get(h.status, h.status))}</span>'
        f' <span class="dim">{_e(h.created)} · {_e(h.sender)} → {_e(h.to)}</span></div>'
        f'<div>{_e(h.summary.splitlines()[0] if h.summary else h.id)}</div>'
        + (f'<div class="dim">next: {_e("; ".join(h.next_steps))}</div>' if h.next_steps else "")
        + (f'<div class="dim">taken by {_e(h.accepted_by)}</div>' if h.accepted_by else "")
        + (_cmd("anchormem handoff take") if h.status == "pending" else "")
        + '</div>' for h in ordered[:8])

    # ---- inbox, lint, retired
    ibody = (f'<p>{len(s.notes)} notes waiting · <b class="amb">{len(s.groups)}</b> ready to become memories</p>'
             + "".join(f'<div class="ho"><code>{_e(g.anchor)}</code><div>{_e(g.title)}</div>'
                       f'<div class="dim">noted {len(g.notes)} times</div></div>' for g in s.groups)
             + (_cmd("anchormem promote --apply") if s.groups else "")
             + "".join(f'<div class="dim">· <code>{_e(n["anchor"])}</code> {_e(n["text"])}</div>' for n in s.notes[-6:]))
    lbody = "".join(f'<div class="lint l-{_e(f.level)}"><b>{_e(f.level)}</b> {_e(f.code)} · {_e(f.memory)}'
                    f'<div class="dim">{_e(f.message)}</div></div>' for f in s.findings)
    retired_rows = "".join(f'<div><b>{_e(m.id)}</b> <span class="dim">{_e(m.extra.get("retired", ""))} · '
                           f'{_e(m.extra.get("retired_reason", ""))}</span></div>' for m in retired)

    panels = "".join([
        _panel(4, "Files with memories", f"{len(files)} files", fmap or _empty("No memories point at code yet.")),
        _panel(5, "Activity, last 14 days", f"{len(log)} events",
               f'<div class="cols">{cols}</div><p class="dim">{_e(opline) or "nothing logged yet"}</p>'),
        _panel(6, "Handoffs", f"{len(s.pending)} waiting",
               hrows or _empty("No handoffs. When a session stops mid-task, it can leave one:",
                               'anchormem handoff new "<where things stand>" -n "<next step>"')),
        _panel(7, "Notes inbox", f"{len(s.groups)} ready",
               ibody if s.notes else _empty("No notes. Agents jot small observations with:",
                                            'anchormem note <file>#<function> "<what you noticed>"')),
        _panel(8, "Lint", f"{len(s.findings)} findings", lbody or '<p class="ok">No problems found in the memories.</p>'),
        _panel(9, "Retired", f"{len(retired)} retired", retired_rows or _empty("Nothing retired.")),
    ])

    if live:
        refresh = '<span class="live"><i id="livedot"></i> Live: this page updates by itself when memories or code change.</span>'
    else:
        refresh = (f'<span>Snapshot from {STAMP}. Refresh it with</span>{_cmd("anchormem dashboard")}'
                   f'<span>or keep it live with</span>{_cmd("anchormem dashboard --serve")}')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>anchormem · {_e(root.name)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&display=swap">
<style>{CSS}</style></head>
<body data-version="{VERSION}"{' data-live="1"' if live else ''}><div class="wrap">
<header><div class="brand"><b>AM</b><small>anchormem</small></div>
<div class="headline"><h1>{_e(root.name)}</h1>
<p class="verdict {vcls}">{_e(verdict)}</p>
<p class="meta">commit {_e(commit)} · updated {STAMP} · anchormem {__version__}</p></div>
<div class="stats">{head}</div></header>

<details class="frame intro"{' open' if not mems else ''}><summary>How this works <span class="dim">(30 seconds)</span></summary>
<div class="pad"><p>Each memory is a short note about your code, like “the pool is capped at 10 because of our database plan”.
It's attached to the exact function or file it describes.</p>
<p>anchormem remembers what that code looked like. When the code changes, the memory is marked <b class="stale">CHANGED</b> so you
(or your AI agent) know to re-read it before trusting it. If it's still true, verify it and it turns <b class="ok">OK</b> again.</p>
<p>Memories an agent writes stay “not approved” until a person reads and approves them. Every command on this page has a copy button:
paste it into a terminal inside this repo.</p></div></details>

<section class="frame"><div class="ph"><span class="n">01</span><span class="t">Do this next</span>
<span class="tag">{len(s.steps)} {"step" if len(s.steps) == 1 else "steps"}</span></div><div class="pad">{steps}</div></section>

<section class="frame"><div class="ph"><span class="n">02</span><span class="t">Status of every memory</span>
<span class="tag">{len(mems)} memories · {n_anchors} anchors</span></div><div class="pad">{strip}</div></section>

<section class="frame"><div class="ph"><span class="n">03</span><span class="t">Memories</span>
<span class="tag">the ones that need a look come first</span></div>{table}</section>

<div class="grid">{panels}</div>

<section class="frame"><div class="ph"><span class="n">10</span><span class="t">Log</span>
<span class="tag">.anchormem/log.md · newest first</span></div><div class="log">{recent or _empty("No log entries yet.")}</div></section>
<footer class="dim">{refresh}</footer>
</div><script>{JS}</script></body></html>
"""


# ---------------------------------------------------------------- live server

def make_server(root: Path, port: int = 8765):
    """An HTTP server on 127.0.0.1 that rebuilds the page on every request. Tries the next ports if busy."""
    import http.server
    import socketserver

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/version":
                    body, ctype = version(root).encode(), "text/plain"
                elif path in ("/", "/index.html"):
                    body, ctype = build(root, live=True).encode("utf-8"), "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
            except Exception as e:  # show the problem instead of a dead page
                body, ctype = f"anchormem could not build the dashboard: {e}".encode(), "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    for p in range(port, port + 20):
        try:
            return Server(("127.0.0.1", p), Handler)
        except OSError:
            continue
    raise OSError(f"no free port between {port} and {port + 19}")


def serve(root: Path, port: int = 8765, open_browser: bool = True) -> int:
    import webbrowser
    httpd = make_server(root, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"live dashboard for {root.name}: {url}")
    print("  it updates by itself when memories or code change. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


JS = r"""
(function(){
var q=document.getElementById('q'),rows=[].slice.call(document.querySelectorAll('tr[data-tags]')),mode='all';
function apply(){if(!q)return;var s=q.value.trim().toLowerCase(),n=0;
 rows.forEach(function(r){var ok=(mode==='all'||(' '+r.getAttribute('data-tags')+' ').indexOf(' '+mode+' ')>=0)&&(!s||r.textContent.toLowerCase().indexOf(s)>=0);r.hidden=!ok;if(ok)n++;});
 var sh=document.getElementById('shown');if(sh)sh.textContent=n===rows.length?'':n+' of '+rows.length+' shown';
 var none=document.getElementById('none');if(none)none.hidden=n>0;
 [].forEach.call(document.querySelectorAll('[data-mode]'),function(b){b.classList.toggle('on',b.getAttribute('data-mode')===mode);});}
try{var sq=sessionStorage.getItem('am-q'),sm=sessionStorage.getItem('am-m');if(q&&sq)q.value=sq;if(sm)mode=sm;}catch(e){}
if(q)q.addEventListener('input',apply);
document.addEventListener('click',function(e){
 var m=e.target.closest('[data-mode]');if(m){mode=m.getAttribute('data-mode');apply();return;}
 var b=e.target.closest('[data-copy]');if(!b)return;var t=b.getAttribute('data-copy');
 function done(x){b.textContent=x;setTimeout(function(){b.textContent='copy';},1400);}
 function fallback(){var c=b.previousElementSibling,r=document.createRange();r.selectNodeContents(c);var s=getSelection();s.removeAllRanges();s.addRange(r);
  try{done(document.execCommand('copy')?'copied':'press ⌘C');}catch(_){done('press ⌘C');}}
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(function(){done('copied');},fallback);}else fallback();
});
apply();
if(document.body.getAttribute('data-live')){var v=document.body.getAttribute('data-version'),dot=document.getElementById('livedot');
 setInterval(function(){fetch('/version',{cache:'no-store'}).then(function(r){return r.text();}).then(function(t){
  if(dot)dot.className='';t=t.trim();if(/^[0-9a-f]{12}$/.test(t)&&t!==v){try{sessionStorage.setItem('am-q',q?q.value:'');sessionStorage.setItem('am-m',mode);}catch(e){}location.reload();}
 }).catch(function(){if(dot)dot.className='off';});},3000);}
})();
"""

CSS = """
:root{--bg:#060a12;--panel:#0a0f1a;--panel2:#0e1524;--line:#1a2435;--fg:#ece4d3;--dim:#858ea1;
--accent:#ff7a1a;--amber:#ffb54d;--stale:#ff5a74;--ok:#58e0a2;--onacc:#060a12;
--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#f6f3ec;--panel:#fffdf8;--panel2:#f3efe6;--line:#e0d9ca;--fg:#1d1a14;
--dim:#6b6558;--accent:#c25500;--amber:#975700;--stale:#c8203f;--ok:#12784f;--onacc:#fff;color-scheme:light}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--mono);font-size:12.5px;line-height:1.55;overflow-x:hidden}
.wrap{max-width:1200px;margin:0 auto;padding:18px 16px 32px;display:flex;flex-direction:column;gap:12px}
.dim{color:var(--dim)} .ok{color:var(--ok)} .stale{color:var(--stale)} .amb{color:var(--amber)}
code{font-family:inherit;color:var(--amber)}
header{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:18px;align-items:center}
.brand{background:var(--accent);color:var(--onacc);padding:10px 16px;display:flex;flex-direction:column;align-items:center}
.brand b{font-size:22px;font-weight:800;letter-spacing:.06em}
.brand small{font-size:9px;font-weight:700;letter-spacing:.18em;text-transform:uppercase}
.headline{min-width:0}
.headline h1{margin:0;font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);overflow-wrap:anywhere}
.verdict{margin:2px 0 0;font-size:19px;font-weight:800;line-height:1.3}
.meta{margin:4px 0 0;color:var(--dim);font-size:11px}
.stats{display:flex;gap:20px;flex-wrap:wrap}
.stat{display:flex;flex-direction:column;gap:2px}
.stat span{font-size:9px;color:var(--dim);letter-spacing:.14em;text-transform:uppercase;white-space:nowrap}
.stat b{font-size:18px;color:var(--amber);font-variant-numeric:tabular-nums}
.stat b.s{color:var(--stale)} .stat b.f{color:var(--ok)}
.frame{position:relative;border:1px solid var(--line);background:var(--panel);min-width:0}
.frame::before,.frame::after{content:"";position:absolute;width:10px;height:10px;border:2px solid var(--accent);pointer-events:none}
.frame::before{top:-1px;left:-1px;border-right:0;border-bottom:0}
.frame::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.ph{display:flex;align-items:center;gap:8px;padding:8px 10px 6px;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;min-width:0}
.ph .n{background:var(--accent);color:var(--onacc);padding:1px 5px;font-size:9px}
.ph .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ph .tag{margin-left:auto;color:var(--accent);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pad{padding:4px 12px 12px} .pad p{margin:0 0 8px;max-width:82ch}
.intro summary{cursor:pointer;padding:10px 12px;font-weight:700}
.big{font-size:14px;margin:4px 0}
.steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.step{display:grid;grid-template-columns:62px minmax(0,1fr);gap:10px;align-items:start;padding:8px 10px;background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--dim)}
.step p{margin:0 0 6px}
.s-fix{border-left-color:var(--stale)} .s-todo{border-left-color:var(--amber)} .s-tip{border-left-color:var(--ok)}
.lv{font-size:9.5px;text-transform:uppercase;letter-spacing:.1em;font-weight:700;padding-top:2px}
.s-fix .lv{color:var(--stale)} .s-todo .lv{color:var(--amber)} .s-tip .lv{color:var(--ok)}
.cmd{display:flex;align-items:stretch;max-width:100%;margin-top:4px;border:1px solid var(--line);background:var(--bg)}
.cmd code{flex:1;min-width:0;padding:5px 8px;white-space:nowrap;overflow-x:auto;font-size:11.5px}
.cmd button{flex:none;font:inherit;font-size:10px;text-transform:uppercase;letter-spacing:.08em;cursor:pointer;border:0;border-left:1px solid var(--line);background:var(--panel2);color:var(--accent);padding:0 10px;min-width:64px;min-height:30px}
.cmd button:hover{background:var(--accent);color:var(--onacc)}
.strip{display:flex;height:14px;gap:2px} .strip i{display:block;min-width:4px}
.legend{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.lg{display:grid;grid-template-columns:96px 28px minmax(0,1fr);gap:8px;align-items:baseline}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:0 10px 8px}
.tools input{font:inherit;flex:1 1 220px;min-width:0;padding:7px 9px;background:var(--bg);color:var(--fg);border:1px solid var(--line)}
.tools input:focus,.chips button:focus-visible,.cmd button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chips button{font:inherit;font-size:11px;cursor:pointer;padding:6px 10px;background:var(--panel2);color:var(--fg);border:1px solid var(--line)}
.chips button.on{background:var(--accent);color:var(--onacc);border-color:var(--accent)}
.tw{overflow-x:auto;padding:0 10px 10px}
table{width:100%;border-collapse:collapse;font-size:11.5px;min-width:820px}
th{text-align:left;color:var(--dim);font-weight:500;font-size:9px;letter-spacing:.14em;text-transform:uppercase;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td p{margin:4px 0 0} td .cmd{max-width:420px} .nw{white-space:nowrap}
td:nth-child(3){min-width:230px} td:nth-child(4){min-width:120px}
.body{color:var(--dim);max-width:60ch}
tr.bad td{background:color-mix(in srgb,var(--stale) 8%,transparent)}
tr[hidden]{display:none}
.pill{display:inline-block;border:1px solid var(--c);color:var(--c);padding:0 6px;font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;white-space:nowrap}
.anc{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:3px}
.anc li{display:flex;align-items:center;gap:6px;overflow-wrap:anywhere} .anc i{width:7px;height:7px;flex:none;display:block}
.st{color:var(--stale);text-transform:uppercase;font-size:9.5px}
.conf{text-transform:uppercase;font-size:10px;display:block} .c-high{color:var(--ok)} .c-medium{color:var(--amber)} .c-low{color:var(--stale)}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.panel{display:flex;flex-direction:column}
.pb{margin:0 10px 10px;background:var(--panel2);border:1px solid var(--line);padding:9px;font-size:11.5px;min-height:150px;max-height:280px;overflow:auto;display:flex;flex-direction:column;gap:7px}
.pb p{margin:0}
.bar{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) 22px;gap:8px;align-items:center}
.bar code{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .tr{height:8px;background:var(--line);display:flex;gap:1px} .bar .tr i{display:block;height:100%}
.bar b{text-align:right;font-weight:500}
.cols{display:flex;align-items:flex-end;gap:4px;height:110px;flex:none}
.col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;gap:3px;min-width:0}
.col i{display:block;width:100%;background:var(--accent);min-height:1px}
.col span{font-size:9px;color:var(--dim)}
.ho{border-left:2px solid var(--line);padding-left:8px;display:flex;flex-direction:column;gap:2px;overflow-wrap:anywhere}
.lint{border-left:2px solid var(--dim);padding-left:8px} .l-error{border-color:var(--stale)} .l-warn{border-color:var(--amber)}
.log{padding:0 10px 12px;display:flex;flex-direction:column;gap:2px;font-size:11.5px;max-height:340px;overflow:auto}
.log div{overflow-wrap:anywhere}
.op{font-weight:700;color:var(--accent)} .op-verify,.op-confirm{color:var(--ok)} .op-retire,.op-rm{color:var(--stale)}
.empty{margin:auto 0;display:flex;flex-direction:column;gap:4px} .empty p{margin:0}
footer{font-size:11.5px;line-height:1.8;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
footer .cmd{margin:0}
.live{display:inline-flex;align-items:center;gap:8px}
#livedot{width:8px;height:8px;border-radius:50%;background:var(--ok);display:inline-block;animation:pulse 2s infinite}
#livedot.off{background:var(--stale);animation:none}
@keyframes pulse{50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){#livedot{animation:none}}
@media (max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}header{grid-template-columns:auto minmax(0,1fr)}.stats{grid-column:1/-1}}
@media (max-width:600px){.grid{grid-template-columns:minmax(0,1fr)}.stats{gap:14px}.step{grid-template-columns:minmax(0,1fr);gap:4px}
.lg{grid-template-columns:auto auto;justify-content:start}.lg .dim{grid-column:1/-1}.verdict{font-size:16px}}
"""

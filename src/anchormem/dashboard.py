"""`anchormem dashboard`: one self-contained HTML page showing the repo's real memory state.

The page is rendered here in Python and works without JavaScript. Small inline scripts add
copy buttons, search and filters, and (with --serve) a live refresh when the repo changes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter, OrderedDict
from html import escape
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, codemap, core, gitutil, handoff, store
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
            f'<tr id="m-{_e(m.id)}" class="{"bad" if not r.ok else ""}" data-tags="{tags}">'
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

    # ---- map: memories -> anchored code -> files, on top of the repo's code
    try:
        keep = [a.anchor.path for r in results for a in r.anchors]
        skel = codemap.skeleton(root, keep=keep)
    except Exception:
        skel = []
    graph = graph_data(results, skel)
    n_files = sum(1 for n in graph["nodes"] if n["kind"] == "file")
    n_code = sum(1 for n in graph["nodes"] if n["kind"] == "symbol")
    if graph["nodes"]:
        gjson = json.dumps(graph, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
        mapsec = (f'<div class="map"><canvas id="graph" role="img" tabindex="0" aria-label="Neural map of {len(mems)} memories '
                  f'and the {n_files} files and {n_code} functions they sit in"></canvas>'
                  '<div class="ghud"><div id="gstat"></div><div class="gctl" role="group" aria-label="Zoom">'
                  '<button type="button" data-z="in" aria-label="Zoom in">+</button>'
                  '<button type="button" data-z="out" aria-label="Zoom out">−</button>'
                  '<button type="button" data-z="fit">fit</button></div></div>'
                  '<div id="gtip" class="gtip" hidden></div>'
                  '<div class="gkey"><span><i class="k-m"></i>memory</span><span><i class="k-s"></i>function / class</span>'
                  '<span><i class="k-f"></i>file</span><span><i style="background:#58e0a2"></i>ok</span>'
                  '<span><i style="background:#ff5a74"></i>changed / missing</span>'
                  '<span><i style="background:#ffb54d"></i>moved / flagged</span>'
                  '<span><i style="background:#7f93ff"></i>code with no memory yet</span></div>'
                  '<div id="ginfo" class="ginfo" hidden></div>'
                  '<p class="dim ghelp">Hover a neuron to see what it is. Click a memory to jump to it, a file to list its memories, '
                  'or dim code to see how to add a memory there. Drag to move, ⌘/Ctrl + scroll or the buttons to zoom.</p>'
                  '<noscript><p class="dim">The map needs JavaScript; everything it shows is also in the table below.</p></noscript>'
                  f'</div><script type="application/json" id="graph-data">{gjson}</script>')
    else:
        mapsec = '<div class="pad">' + _empty("No code found to map yet.") + '</div>'

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
        _panel(5, "Files with memories", f"{len(files)} files", fmap or _empty("No memories point at code yet.")),
        _panel(6, "Activity, last 14 days", f"{len(log)} events",
               f'<div class="cols">{cols}</div><p class="dim">{_e(opline) or "nothing logged yet"}</p>'),
        _panel(7, "Handoffs", f"{len(s.pending)} waiting",
               hrows or _empty("No handoffs. When a session stops mid-task, it can leave one:",
                               'anchormem handoff new "<where things stand>" -n "<next step>"')),
        _panel(8, "Notes inbox", f"{len(s.groups)} ready",
               ibody if s.notes else _empty("No notes. Agents jot small observations with:",
                                            'anchormem note <file>#<function> "<what you noticed>"')),
        _panel(9, "Lint", f"{len(s.findings)} findings", lbody or '<p class="ok">No problems found in the memories.</p>'),
        _panel(10, "Retired", f"{len(retired)} retired", retired_rows or _empty("Nothing retired.")),
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

<section class="frame"><div class="ph"><span class="n">03</span><span class="t">Neural map of memories and code</span>
<span class="tag">{len(mems)} memories · {n_files} files · {n_code} functions</span></div>{mapsec}</section>

<section class="frame"><div class="ph"><span class="n">04</span><span class="t">Memories</span>
<span class="tag">the ones that need a look come first</span></div>{table}</section>

<div class="grid">{panels}</div>

<section class="frame"><div class="ph"><span class="n">11</span><span class="t">Log</span>
<span class="tag">.anchormem/log.md · newest first</span></div><div class="log">{recent or _empty("No log entries yet.")}</div></section>
<footer class="dim">{refresh}</footer>
</div><script>{JS}</script><script>{GRAPH_JS}</script></body></html>
"""


_RANK = {core.MISSING: 5, core.CHANGED: 4, core.MOVED: 3, core.FLAGGED: 2, core.UNVERIFIED: 1, core.OK: 0, "code": -1}


def graph_data(results, skeleton=()) -> dict:
    """Nodes for memories, the functions/classes/lines they point at, and files; links between them.
    `skeleton` adds the rest of the repo's code (status "code") so the map shows what memories sit in."""
    nodes, links, seen, linked = [], [], {}, set()

    def node(nid: str, kind: str, label: str, status: str, title: str = "", count: int = 1) -> None:
        if nid in seen:
            n = nodes[seen[nid]]
            if _RANK[status] > _RANK[n["status"]]:
                n["status"] = status
            n["count"] += count
            return
        seen[nid] = len(nodes)
        nodes.append({"id": nid, "kind": kind, "label": label, "status": status, "title": title, "count": count})

    def part(aid: str, fid: str) -> None:
        if (aid, fid) not in linked:
            linked.add((aid, fid))
            links.append([aid, fid, "part"])

    for r in results:
        m = r.memory
        mid = "m:" + m.id
        node(mid, "memory", m.id, r.status, m.title)
        for a in r.anchors:
            fid = "f:" + a.anchor.path
            node(fid, "file", a.anchor.path, a.status)
            if a.anchor.symbol or a.anchor.start is not None:
                aid = "a:" + str(a.anchor)
                label = a.anchor.symbol or f"lines {a.anchor.start}-{a.anchor.end}"
                node(aid, "symbol", label, a.status, str(a.anchor))
                links.append([mid, aid, a.status])
                part(aid, fid)
            else:
                links.append([mid, fid, a.status])
    for path, symbols in skeleton:
        fid = "f:" + path
        node(fid, "file", path, "code", count=0)
        for sym in symbols:
            aid = f"a:{path}#{sym}"
            node(aid, "symbol", sym, "code", f"{path}#{sym}", count=0)
            part(aid, fid)
    return {"nodes": nodes, "links": links}


# ---------------------------------------------------------------- live server

_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


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
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except _GONE:
                pass  # the browser reloaded or closed the tab mid-response

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], _GONE):  # a closed tab is normal, not an error
                super().handle_error(request, client_address)

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

    def _stop(*_):
        raise KeyboardInterrupt  # closing the terminal or `kill` stops cleanly too (so `try` removes its clone)
    import signal
    for sig in ("SIGTERM", "SIGHUP"):
        try:
            signal.signal(getattr(signal, sig), _stop)
        except (AttributeError, ValueError, OSError):
            pass  # not available on this OS, or not the main thread
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


GRAPH_JS = r"""
(function(){
var el=document.getElementById('graph-data'),cv=document.getElementById('graph');
if(!el||!cv||!cv.getContext)return;
var G=JSON.parse(el.textContent),N=G.nodes,ctx=cv.getContext('2d'),idx={},i,j,k;
for(i=0;i<N.length;i++)idx[N[i].id]=i;
var E=[];G.links.forEach(function(l){if(idx[l[0]]!=null&&idx[l[1]]!=null)E.push({a:idx[l[0]],b:idx[l[1]],s:l[2]});});
var nb=N.map(function(){return[];});E.forEach(function(e){nb[e.a].push(e.b);nb[e.b].push(e.a);});
function h(s){var x=2166136261;for(var q=0;q<s.length;q++){x^=s.charCodeAt(q);x=Math.imul(x,16777619);}return(x>>>0)/4294967296;}
var RGB={ok:'88,224,162',changed:'255,90,116',missing:'255,90,116',moved:'255,181,77',flagged:'255,181,77',unverified:'160,168,185',code:'127,147,255'};
function rgb(s){return RGB[s]||RGB.code;}
N.forEach(function(n){n.lit=n.status!=='code';n.bad=n.lit&&n.status!=='ok'&&n.status!=='unverified';});
var nMem=N.filter(function(n){return n.kind==='memory';}).length,nFile=N.filter(function(n){return n.kind==='file';}).length;
var nSym=N.length-nMem-nFile,nBad=N.filter(function(n){return n.kind==='memory'&&n.bad;}).length;
var st=document.getElementById('gstat');
if(st)st.textContent=nFile+' files · '+nSym+' functions · '+nMem+' memories'+(nBad?' · '+nBad+' need a look':'')+' · '+E.length+' links';

// ---------- layout: worked out once, the same on every refresh
var files=N.filter(function(n){return n.kind==='file';}).sort(function(a,b){return(b.lit-a.lit)||(a.id<b.id?-1:1);});
var R=70+Math.sqrt(N.length)*26;
files.forEach(function(n,q){var t=6.2832*q/Math.max(files.length,1),r=R*(n.lit?.5:1)*(.8+h(n.id)*.4);n.x=Math.cos(t)*r;n.y=Math.sin(t)*r;});
var parent={};E.forEach(function(e){if(e.s==='part')parent[e.a]=e.b;});
N.forEach(function(n,q){if(n.kind==='symbol'){var p=N[parent[q]],t=h(n.id)*6.2832;n.x=(p?p.x:0)+Math.cos(t)*26;n.y=(p?p.y:0)+Math.sin(t)*26;}});
N.forEach(function(n,q){if(n.kind==='memory'){var sx=0,sy=0,c=0;nb[q].forEach(function(m){if(N[m].x!=null){sx+=N[m].x;sy+=N[m].y;c++;}});
 var t=h(n.id)*6.2832;n.x=(c?sx/c:0)*.75+Math.cos(t)*38;n.y=(c?sy/c:0)*.75+Math.sin(t)*38;}});
N.forEach(function(n){if(n.x==null){n.x=0;n.y=0;}n.vx=0;n.vy=0;});
var M=N.length,iters=M>260?90:M>160?160:300;
for(var it=0;it<iters;it++){var cool=1-it/iters;
 for(i=0;i<M;i++){var A=N[i];for(j=i+1;j<M;j++){var B=N[j],dx=B.x-A.x,dy=B.y-A.y,d2=dx*dx+dy*dy+.01;if(d2>90000)continue;
  var f=((A.lit||B.lit)?2000:850)/d2,d=Math.sqrt(d2);dx/=d;dy/=d;A.vx-=dx*f;A.vy-=dy*f;B.vx+=dx*f;B.vy+=dy*f;}}
 for(k=0;k<E.length;k++){var e=E[k],P=N[e.a],Q=N[e.b],ex=Q.x-P.x,ey=Q.y-P.y,dd=Math.sqrt(ex*ex+ey*ey)||1,rest=e.s==='part'?24:60,ff=(dd-rest)*.07;
  ex/=dd;ey/=dd;P.vx+=ex*ff;P.vy+=ey*ff;Q.vx-=ex*ff;Q.vy-=ey*ff;}
 for(i=0;i<M;i++){var n=N[i],g=n.lit?.018:.009;n.vx-=n.x*g;n.vy-=n.y*g;var sp=Math.sqrt(n.vx*n.vx+n.vy*n.vy),mx=12*cool+1;
  if(sp>mx){n.vx*=mx/sp;n.vy*=mx/sp;}n.x+=n.vx;n.y+=n.vy;n.vx*=.55;n.vy*=.55;}}
N.forEach(function(n){n.bx=n.x;n.by=n.y;});
E.forEach(function(e,q){e.bend=(h('b'+q)-.5)*.55;});

// ---------- signals travelling along the links
var S=[];
E.forEach(function(e,q){var A=N[e.a],B=N[e.b],mem=e.s!=='part',lit=A.lit||B.lit;
 var c=mem?2:(lit?1:(h('p'+q)<.3?1:0));
 for(var z=0;z<c;z++)S.push({e:q,t:h('t'+q+z),v:(mem?(e.s==='ok'?.0032:.0085):(lit?.0024:.0016))*(.7+h('v'+q+z)*.6)});});
if(S.length>600)S.length=600;

// ---------- glow sprites (much faster than shadowBlur)
var SP={};
function spr(c){if(SP[c])return SP[c];var s=document.createElement('canvas'),z=64;s.width=s.height=z;var g=s.getContext('2d'),gr=g.createRadialGradient(32,32,0,32,32,32);
 gr.addColorStop(0,'rgba('+c+',1)');gr.addColorStop(.18,'rgba('+c+',.55)');gr.addColorStop(.45,'rgba('+c+',.14)');gr.addColorStop(1,'rgba('+c+',0)');
 g.fillStyle=gr;g.fillRect(0,0,z,z);return SP[c]=s;}

// ---------- camera
var W=0,H=0,dpr=1,cam={s:1,x:0,y:0},home={s:1,x:0,y:0},bg=null,hover=null,sel=null;
function fit(){var x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;N.forEach(function(n){x0=Math.min(x0,n.bx);y0=Math.min(y0,n.by);x1=Math.max(x1,n.bx);y1=Math.max(y1,n.by);});
 var bw=Math.max(x1-x0,1),bh=Math.max(y1-y0,1),sx=Math.max(1,Math.min(2.4,(W/H)*bh/bw));
 N.forEach(function(n){n.x=(n.bx-x0)*sx;n.y=n.by-y0;});bw*=sx;
 var s=Math.min((W-90)/bw,(H-70)/bh);s=Math.max(.15,Math.min(s,3));
 home={s:s,x:W/2-bw*s/2,y:H/2-bh*s/2+4};cam={s:home.s,x:home.x,y:home.y};}
function makeBg(){bg=document.createElement('canvas');bg.width=W*dpr;bg.height=H*dpr;var g=bg.getContext('2d');g.scale(dpr,dpr);
 var gr=g.createRadialGradient(W*.5,H*.45,0,W*.5,H*.5,Math.max(W,H)*.75);gr.addColorStop(0,'#0f1a36');gr.addColorStop(.55,'#070c1a');gr.addColorStop(1,'#03050b');
 g.fillStyle=gr;g.fillRect(0,0,W,H);
 g.fillStyle='rgba(130,150,220,.07)';for(var x=13;x<W;x+=26)for(var y=13;y<H;y+=26)g.fillRect(x,y,1,1);
 for(var q=0;q<Math.round(W*H/5200);q++){var a=h('s'+q),b=h('y'+q),c=h('z'+q);g.fillStyle='rgba(200,210,255,'+(.08+c*.35)+')';
  g.beginPath();g.arc(a*W,b*H,c<.92?.6:1.3,0,6.2832);g.fill();}}
function resize(){var r=cv.getBoundingClientRect();if(!r.width)return;W=r.width;H=r.height;dpr=Math.min(window.devicePixelRatio||1,2);
 cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);fit();makeBg();kick();}

// ---------- drawing
function rad(n){var z=Math.max(.75,Math.min(2.2,Math.sqrt(cam.s/home.s)));
 if(n.kind==='memory')return 5.6*z;if(n.kind==='file')return(n.lit?6.5+Math.min(n.count,6)*.8:3.6)*z;return(n.lit?3.3:2)*z;}
function ctrl(e,A,B){var mx=(A.sx+B.sx)/2,my=(A.sy+B.sy)/2;return[mx-(B.sy-A.sy)*e.bend,my+(B.sx-A.sx)*e.bend];}
function qp(A,c,B,t){var u=1-t;return[u*u*A.sx+2*u*t*c[0]+t*t*B.sx,u*u*A.sy+2*u*t*c[1]+t*t*B.sy];}
var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches,now=0;
function draw(){
 ctx.setTransform(dpr,0,0,dpr,0,0);ctx.globalCompositeOperation='source-over';ctx.globalAlpha=1;
 if(bg)ctx.drawImage(bg,0,0,W,H);
 var focus=hover!=null?hover:sel,near=null;
 if(focus!=null){near={};near[focus]=1;nb[focus].forEach(function(m){near[m]=1;});}
 N.forEach(function(n){n.sx=n.x*cam.s+cam.x;n.sy=n.y*cam.s+cam.y;});
 ctx.globalCompositeOperation='lighter';ctx.lineCap='round';
 E.forEach(function(e){var A=N[e.a],B=N[e.b],mem=e.s!=='part',c=ctrl(e,A,B),on=!near||(near[e.a]&&near[e.b]);
  var col=mem?rgb(e.s):(A.lit||B.lit?'150,170,255':'110,130,220'),al=mem?.5:(A.lit||B.lit?.22:.11);
  if(near)al=on?Math.min(1,al*2.2):al*.12;
  ctx.strokeStyle='rgba('+col+','+al+')';ctx.lineWidth=mem?1.4:.9;
  ctx.beginPath();ctx.moveTo(A.sx,A.sy);ctx.quadraticCurveTo(c[0],c[1],B.sx,B.sy);ctx.stroke();});
 if(!reduce)S.forEach(function(p){var e=E[p.e],A=N[e.a],B=N[e.b],mem=e.s!=='part',c=ctrl(e,A,B),on=!near||(near[e.a]&&near[e.b]);
  var t=(mem&&e.s!=='ok')?1-p.t:p.t,pt=qp(A,c,B,t),sz=mem?(e.s==='ok'?11:14):(A.lit||B.lit?8:6);
  ctx.globalAlpha=(on?1:.1)*(mem?.95:(A.lit||B.lit?.6:.35))*Math.sin(Math.PI*p.t);
  ctx.drawImage(spr(mem?rgb(e.s):'150,170,255'),pt[0]-sz/2,pt[1]-sz/2,sz,sz);});
 ctx.globalAlpha=1;
 N.forEach(function(n,q){var r=rad(n),on=!near||near[q],g=n.kind==='memory'?r*7:n.lit?r*6:r*4.5;
  ctx.globalAlpha=(on?1:.1)*(n.lit?(n.kind==='memory'?.75:.5):.28)*(n.bad?.85+.15*Math.sin(now/260+q):1);
  ctx.drawImage(spr(rgb(n.status)),n.sx-g/2,n.sy-g/2,g,g);
  if(n.bad&&n.kind==='memory'&&!reduce){var ph=((now/1700)+h(n.id))%1;ctx.globalAlpha=(on?1:.15)*(1-ph)*.9;ctx.strokeStyle='rgba('+rgb(n.status)+',1)';
   ctx.lineWidth=1.6;ctx.beginPath();ctx.arc(n.sx,n.sy,r+4+ph*22,0,6.2832);ctx.stroke();}});
 ctx.globalCompositeOperation='source-over';
 N.forEach(function(n,q){var r=rad(n),on=!near||near[q],c=rgb(n.status);ctx.globalAlpha=on?1:.18;
  if(n.kind==='memory'){var gr=ctx.createRadialGradient(n.sx-r*.3,n.sy-r*.3,0,n.sx,n.sy,r);gr.addColorStop(0,'#ffffff');gr.addColorStop(.45,'rgba('+c+',1)');gr.addColorStop(1,'rgba('+c+',.85)');
   ctx.fillStyle=gr;ctx.beginPath();ctx.arc(n.sx,n.sy,r,0,6.2832);ctx.fill();}
  else if(n.kind==='file'){ctx.fillStyle='#070b16';ctx.strokeStyle='rgba('+c+','+(n.lit?1:.7)+')';ctx.lineWidth=n.lit?2:1.2;
   ctx.beginPath();ctx.arc(n.sx,n.sy,r,0,6.2832);ctx.fill();ctx.stroke();ctx.fillStyle='rgba('+c+','+(n.lit?.9:.5)+')';ctx.beginPath();ctx.arc(n.sx,n.sy,r*.38,0,6.2832);ctx.fill();}
  else{ctx.fillStyle='rgba('+c+','+(n.lit?1:.55)+')';ctx.beginPath();ctx.arc(n.sx,n.sy,r,0,6.2832);ctx.fill();}
  if(q===sel){ctx.strokeStyle='#ff7a1a';ctx.lineWidth=2;ctx.beginPath();ctx.arc(n.sx,n.sy,r+5,0,6.2832);ctx.stroke();}});
 // labels
 var zoom=cam.s/home.s,few=N.length<70;
 ctx.font='600 11px "JetBrains Mono",ui-monospace,monospace';ctx.textAlign='center';ctx.textBaseline='top';ctx.lineJoin='round';
 N.forEach(function(n,q){var show,on=!near||near[q];
  if(n.kind==='file')show=n.lit||few||zoom>1.5||(near&&near[q]);
  else if(n.kind==='symbol')show=(near&&near[q])||zoom>2.3||(few&&n.lit);
  else show=near&&near[q]&&q!==focus;
  if(!show||(!on&&near))return;
  var text=n.kind==='file'?n.label.split('/').pop():n.kind==='memory'?n.label:n.label.split('.').pop(),r=rad(n);
  ctx.font=(n.kind==='file'?'700 11px':'500 10px')+' "JetBrains Mono",ui-monospace,monospace';
  ctx.globalAlpha=n.lit||near?1:.7;ctx.lineWidth=3.5;ctx.strokeStyle='rgba(3,5,11,.9)';ctx.strokeText(text,n.sx,n.sy+r+5);
  ctx.fillStyle=n.kind==='file'?'#ece4d3':n.lit?'rgba('+rgb(n.status)+',1)':'#9aa6d6';ctx.fillText(text,n.sx,n.sy+r+5);});
 ctx.globalAlpha=1;}
var raf=0,last=0,visible=true;
function frame(ts){raf=0;if(!visible||document.hidden)return;var dt=last?Math.min((ts-last)/16.7,3):1;last=ts;now=ts;
 S.forEach(function(p){p.t+=p.v*dt;if(p.t>1)p.t-=1;});draw();raf=requestAnimationFrame(frame);}
function kick(){if(reduce){draw();return;}if(!raf){last=0;raf=requestAnimationFrame(frame);}}
if('IntersectionObserver' in window)new IntersectionObserver(function(es){visible=es[0].isIntersecting;if(visible)kick();}).observe(cv);
document.addEventListener('visibilitychange',kick);

// ---------- interaction
var tip=document.getElementById('gtip'),info=document.getElementById('ginfo'),wrap=cv.parentNode;
function hit(mx,my){var best=null,bd=1e9;N.forEach(function(n,q){var r=rad(n)+7,dx=n.sx-mx,dy=n.sy-my,d=dx*dx+dy*dy;if(d<r*r&&d<bd){bd=d;best=q;}});return best;}
function kindName(n){return n.kind==='memory'?'memory':n.kind==='file'?'file':'function / class';}
function showTip(q){var n=N[q];if(!tip)return;tip.innerHTML='';var b=document.createElement('b');b.textContent=n.kind==='memory'?n.title:n.label;tip.appendChild(b);
 var d=document.createElement('div');d.className='dim';
 d.textContent=kindName(n)+' · '+(n.lit?n.status:'no memory yet')+(n.kind==='memory'?' · '+n.label:n.count?' · '+n.count+' memor'+(n.count>1?'ies':'y'):'');tip.appendChild(d);
 tip.hidden=false;var cr=cv.getBoundingClientRect(),wr=wrap.getBoundingClientRect(),x=cr.left-wr.left+n.sx,y=cr.top-wr.top+n.sy;
 tip.style.left=Math.max(4,Math.min(x-tip.offsetWidth/2,wr.width-tip.offsetWidth-4))+'px';tip.style.top=Math.max(4,y-tip.offsetHeight-rad(n)-10)+'px';}
function hideTip(){if(tip)tip.hidden=true;}
function cmdBox(text){var c=document.createElement('div');c.className='cmd';var code=document.createElement('code');code.textContent=text;
 var b=document.createElement('button');b.type='button';b.textContent='copy';b.setAttribute('data-copy',text);b.setAttribute('aria-label','Copy command');c.appendChild(code);c.appendChild(b);return c;}
function showInfo(q){if(!info)return;var n=N[q];info.innerHTML='';var p=document.createElement('p');
 var where=n.kind==='file'?n.label:n.title;var b=document.createElement('b');b.textContent=where;p.appendChild(b);
 p.appendChild(document.createTextNode(' has no memory yet. Know something about it that isn’t obvious from the code? Save it:'));info.appendChild(p);
 info.appendChild(cmdBox('anchormem add "<what is true>" --anchor '+where+' --body "<why it matters>"'));info.hidden=false;}
function search(v){var q=document.getElementById('q');if(!q)return;q.value=v;q.dispatchEvent(new Event('input'));
 var row=document.querySelector('tr[data-tags]:not([hidden])');if(row)row.scrollIntoView({behavior:reduce?'auto':'smooth',block:'center'});}
function act(q){var n=N[q];sel=q;if(info)info.hidden=true;
 if(n.kind==='memory'){var row=document.getElementById('m-'+n.label);if(row){var qq=document.getElementById('q');
   if(row.hidden){var all=document.querySelector('[data-mode="all"]');if(all)all.click();if(qq&&qq.value){qq.value='';qq.dispatchEvent(new Event('input'));}}
   row.scrollIntoView({behavior:reduce?'auto':'smooth',block:'center'});row.classList.remove('flash');void row.offsetWidth;row.classList.add('flash');}}
 else if(!n.lit)showInfo(q);
 else search(n.kind==='file'?n.label:n.title);
 kick();}
var drag=null,moved=false;
function pos(e){var r=cv.getBoundingClientRect();return[e.clientX-r.left,e.clientY-r.top];}
cv.addEventListener('pointerdown',function(e){drag={x:e.clientX,y:e.clientY,cx:cam.x,cy:cam.y};moved=false;});
window.addEventListener('pointerup',function(e){var d=drag;drag=null;cv.classList.remove('grab');if(d&&!moved&&e.target===cv){var p=pos(e),q=hit(p[0],p[1]);
 if(q!=null){act(q);if(e.pointerType!=='mouse')showTip(q);}else{sel=null;hideTip();if(info)info.hidden=true;kick();}}});
cv.addEventListener('pointermove',function(e){var p=pos(e);
 if(drag){var dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>4)moved=true;
  if(moved){cam.x=drag.cx+dx;cam.y=drag.cy+dy;cv.classList.add('grab');hideTip();hover=null;kick();return;}}
 if(e.pointerType!=='mouse')return;var q=hit(p[0],p[1]);cv.style.cursor=q!=null?'pointer':'grab';
 if(q!==hover){hover=q;if(q==null)hideTip();else showTip(q);kick();}});
cv.addEventListener('pointerleave',function(){if(!drag&&hover!=null){hover=null;hideTip();kick();}});
function zoomAt(mx,my,f){var ns=Math.max(home.s*.35,Math.min(home.s*8,cam.s*f));f=ns/cam.s;cam.x=mx-(mx-cam.x)*f;cam.y=my-(my-cam.y)*f;cam.s=ns;hideTip();kick();}
cv.addEventListener('wheel',function(e){if(!(e.ctrlKey||e.metaKey))return;e.preventDefault();var p=pos(e);zoomAt(p[0],p[1],Math.exp(-e.deltaY*.01));},{passive:false});
cv.addEventListener('dblclick',function(e){var p=pos(e);zoomAt(p[0],p[1],1.8);});
wrap.addEventListener('click',function(e){var b=e.target.closest('[data-z]');if(!b)return;var z=b.getAttribute('data-z');
 if(z==='fit'){cam={s:home.s,x:home.x,y:home.y};hideTip();kick();}else zoomAt(W/2,H/2,z==='in'?1.4:1/1.4);});
cv.addEventListener('keydown',function(e){var s=40;if(e.key==='+'||e.key==='=')zoomAt(W/2,H/2,1.3);else if(e.key==='-')zoomAt(W/2,H/2,1/1.3);
 else if(e.key==='0'){cam={s:home.s,x:home.x,y:home.y};kick();}else if(e.key==='ArrowLeft')cam.x+=s;else if(e.key==='ArrowRight')cam.x-=s;
 else if(e.key==='ArrowUp')cam.y+=s;else if(e.key==='ArrowDown')cam.y-=s;else return;e.preventDefault();kick();});
var rt;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(resize,120);});
resize();
})();
"""

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
--accent:#ff7a1a;--amber:#ffb54d;--stale:#ff5a74;--ok:#58e0a2;--onacc:#060a12;--line2:#3a475e;
--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#f6f3ec;--panel:#fffdf8;--panel2:#f3efe6;--line:#e0d9ca;--fg:#1d1a14;
--dim:#6b6558;--line2:#b9b09c;--accent:#c25500;--amber:#975700;--stale:#c8203f;--ok:#12784f;--onacc:#fff;color-scheme:light}}
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
.map{position:relative;padding:0 10px 10px}
#graph{display:block;width:100%;height:520px;background:#03050b;border:1px solid #1a2435;cursor:grab;touch-action:pan-y;outline:none}
#graph.grab{cursor:grabbing} #graph:focus-visible{box-shadow:0 0 0 2px var(--accent)}
.ghud{position:absolute;left:20px;right:20px;top:10px;display:flex;justify-content:space-between;align-items:flex-start;gap:10px;pointer-events:none}
#gstat{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#9aa6d6;background:rgba(3,5,11,.6);padding:4px 8px;border:1px solid rgba(127,147,255,.25)}
.gctl{display:flex;gap:3px;pointer-events:auto}
.gctl button{font:inherit;font-size:12px;min-width:30px;height:28px;cursor:pointer;color:#ece4d3;background:rgba(10,15,30,.75);border:1px solid rgba(127,147,255,.35)}
.gctl button:hover{border-color:#ff7a1a;color:#ff7a1a} .gctl button:focus-visible{outline:2px solid #ff7a1a}
.gtip{position:absolute;z-index:2;max-width:300px;pointer-events:none;background:rgba(6,10,20,.94);color:#ece4d3;border:1px solid rgba(127,147,255,.5);padding:6px 9px;font-size:11px;box-shadow:0 0 24px rgba(127,147,255,.25)}
.gtip .dim{color:#9aa6d6}
.gkey{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-size:10.5px;color:var(--dim)}
.gkey span{display:flex;align-items:center;gap:6px} .gkey i{width:9px;height:9px;display:block;border-radius:50%}
.gkey .k-m{background:radial-gradient(circle at 35% 35%,#fff,#58e0a2)} .gkey .k-s{background:#7f93ff;width:6px;height:6px}
.gkey .k-f{border:2px solid #7f93ff;background:transparent}
.ginfo{margin-top:8px;padding:8px 10px;border:1px solid var(--line);border-left:3px solid #7f93ff;background:var(--panel2)} .ginfo p{margin:0 0 4px}
.ghelp{margin:6px 0 0;font-size:10.5px}
tr.flash td{animation:flash 1.6s ease-out}
@keyframes flash{0%{background:color-mix(in srgb,var(--accent) 35%,transparent)}100%{background:transparent}}
@media (max-width:600px){#graph{height:400px}.ghud{left:16px;right:16px}#gstat{font-size:9px}}
@media (max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}header{grid-template-columns:auto minmax(0,1fr)}.stats{grid-column:1/-1}}
@media (max-width:600px){.grid{grid-template-columns:minmax(0,1fr)}.stats{gap:14px}.step{grid-template-columns:minmax(0,1fr);gap:4px}
.lg{grid-template-columns:auto auto;justify-content:start}.lg .dim{grid-column:1/-1}.verdict{font-size:16px}}
"""

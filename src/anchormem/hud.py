"""`anchormem dashboard --hud`: a "control room" view of the repo's real memory state.

One canvas, two layouts (wide 16:9 for screens, tall 4:5 for phones). Every panel is drawn from
the repo's own data: memories and their statuses, stored vs current fingerprints, the git diff
under a stale memory, the log, what the Claude Code hooks delivered, handoffs and confidence.
Nothing on it is made up: a memory is red only when its code really changed. The animation is
decoration over that data (typing, a slow turn, signals along links) and loops every 30 s.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import time
from collections import Counter
from pathlib import Path
from typing import List

from . import __version__, codemap, gitutil, handoff, hooks
from .advice import snapshot
from .anchors import extract, find_symbol
from .confidence import band, counts, score
from .dashboard import graph_data, read_log
from .lint import lint


def _code_panel(root: Path, results) -> dict:
    """The real code behind the first stale memory (its git diff), else the code of the first memory."""
    stale = [r for r in results if not r.ok]
    r = stale[0] if stale else (results[0] if results else None)
    if r is None:
        return {"title": "", "anchor": "", "lines": [], "changed": False}
    a = next((x for x in r.anchors if not x.ok), r.anchors[0])
    lines: List[list] = []
    if not a.ok and r.memory.commit:
        old_src = gitutil._git(root, "show", f"{r.memory.commit}:{a.anchor.path}")
        if old_src is not None:
            try:
                now = extract(root, a.anchor).splitlines()
                if a.anchor.symbol:
                    span = find_symbol(old_src, a.anchor.symbol, Path(a.anchor.path).suffix)
                    old = old_src.splitlines()[span[0] - 1:span[1]] if span else []
                elif a.anchor.start is not None:
                    old = old_src.splitlines()[a.anchor.start - 1:a.anchor.end]
                else:
                    old = old_src.splitlines()
                for ln in difflib.unified_diff(old, now, lineterm="", n=1):
                    if ln.startswith(("---", "+++", "@@")):
                        continue
                    lines.append([ln[:1] if ln[:1] in "+-" else " ", ln[1:]])
                    if len(lines) >= 9:
                        break
            except Exception:
                lines = []
    if not lines:
        try:
            code = extract(root, a.anchor).splitlines()
        except Exception:
            code = []
        lines = [[" ", ln] for ln in code[:9]]
    return {"title": r.memory.id, "anchor": str(a.anchor), "lines": lines, "changed": not a.ok}


def data(root: Path) -> dict:
    t0 = time.perf_counter()
    snap = snapshot(root)
    check_ms = max(1, round((time.perf_counter() - t0) * 1000))
    results = snap.results
    findings = {f.memory for f in lint(root, snap.mems) if f.level in ("error", "warn")}
    mems = []
    for r in results[:60]:
        m = r.memory
        a0 = r.anchors[0] if r.anchors else None
        applied, violated = counts(m)
        sc = score(m)
        mems.append({"id": m.id, "title": m.title, "status": r.status, "ok": r.ok,
                     "anchor": str(a0.anchor) if a0 else "", "anchors": [str(a.anchor) for a in r.anchors],
                     "body": " ".join(m.body.split())[:110], "origin": m.origin or "user",
                     "approved": not m.unconfirmed_agent, "fp": a0.stored_fp if a0 else None,
                     "fp_now": a0.current_fp if a0 else None, "applied": applied, "violated": violated,
                     "score": round(sc, 2) if sc is not None else None, "band": band(sc),
                     "matrix": [all(a.status != "missing" for a in r.anchors), r.ok, not m.unconfirmed_agent,
                                applied + violated > 0, m.id not in findings]})
    try:
        skel = codemap.skeleton(root, keep=[a.anchor.path for r in results for a in r.anchors])
    except Exception:
        skel = []
    graph = graph_data(results, skel)
    files = Counter()
    stale_files = set()
    for r in results:
        for a in r.anchors:
            files[a.anchor.path] += 1
            if not a.ok:
                stale_files.add(a.anchor.path)
    hs = handoff.load_all(root)
    hs = sorted(sorted(hs, key=lambda h: h.created, reverse=True), key=lambda h: h.status != "pending")
    ho = None
    if hs:
        h = hs[0]
        ho = {"from": h.sender, "to": h.to, "summary": (h.summary.splitlines() or [""])[0], "status": h.status,
              "next": h.next_steps[:2], "by": h.accepted_by,
              "files": [[p, handoff.file_status(root, p, fp)] for p, fp in h.files[:3]]}
    evidence = sorted(mems, key=lambda m: -(m["applied"] + m["violated"]))
    conf = evidence[0] if evidence and evidence[0]["applied"] + evidence[0]["violated"] else None
    seeded = [m for m in snap.mems if " comment in " in m.body]
    stale = [m for m in mems if not m["ok"]]
    return {
        "repo": root.name, "commit": gitutil.head(root) or "", "version": __version__,
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stats": {"memories": len(snap.mems), "stale": len(snap.stale), "approved": len(snap.mems) - len(snap.unconfirmed),
                  "anchors": sum(len(r.anchors) for r in results),
                  "files": sum(1 for n in graph["nodes"] if n["kind"] == "file"),
                  "functions": sum(1 for n in graph["nodes"] if n["kind"] == "symbol")},
        "memories": mems, "focus": (stale + [m for m in mems if m["ok"]])[:6], "graph": graph,
        "check": {"ms": check_ms, "exit": 1 if snap.stale else 0},
        "code": _code_panel(root, results),
        "log": [[d[5:] + " " + t, op, mid] for d, t, op, mid, _ in read_log(root)[-12:]],
        "hooks": [[e.get("event", ""), e.get("file", ""), e.get("memories", 0), e.get("stale", 0), e.get("note", "")]
                  for e in hooks.read_events(root, 8)],
        "files": [[p, n, p in stale_files] for p, n in files.most_common(6)],
        "seed": {"seeded": len(seeded), "notes": len(snap.notes), "ready": len(snap.groups),
                 "recent": [m.title for m in seeded[-4:]]},
        "handoff": ho, "confidence": conf,
    }


def build(root: Path, live: bool = False, layout: str = "wide") -> str:
    d = data(root)
    payload = json.dumps(d, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    return (TEMPLATE.replace("@@DATA@@", payload).replace("@@LAYOUT@@", "tall" if layout == "tall" else "wide")
            .replace("@@LIVE@@", "true" if live else "false").replace("@@TITLE@@", f"anchormem HUD · {root.name}"
            .replace("&", "&amp;").replace("<", "&lt;")))


def version(root: Path) -> str:
    import hashlib
    d = data(root)
    d.pop("generated", None)
    d["check"].pop("ms", None)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&display=block">
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}canvas{display:block;width:100vw;height:100vh;object-fit:contain}</style></head>
<body><canvas id="c"></canvas>
<script type="application/json" id="hud-data">@@DATA@@</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById('hud-data').textContent);
const LAYOUT=(new URLSearchParams(location.search).get('layout'))||'@@LAYOUT@@', LIVE=@@LIVE@@;
const WIDE=LAYOUT!=='tall', W=WIDE?1920:1080, H=WIDE?1080:1350, LOOP=30;
const cv=document.getElementById('c'); cv.width=W; cv.height=H; const X=cv.getContext('2d');
const F='"JetBrains Mono", ui-monospace, monospace';
const C={bg:'#0a0805',panel:'#110d08',panel2:'#17110a',line:'#3b2c17',line2:'#5a4322',gold:'#e8ae4f',amber:'#ffcf7a',dim:'#8d7b5c',dim2:'#5d5139',cream:'#f6e6c4',red:'#ff5b47'};
const cl=(v,a=0,b=1)=>Math.max(a,Math.min(b,v)),lerp=(a,b,t)=>a+(b-a)*t,seg=(t,a,b)=>cl((t-a)/(b-a)),eOut=t=>1-Math.pow(1-t,3);
const TAU=Math.PI*2,per=(t,p)=>((t%p)+p)%p;
function rnd(s){let x=Math.sin(s*12.9898+78.233)*43758.5453;return x-Math.floor(x);}
function hs(str){let x=2166136261;for(let i=0;i<str.length;i++){x^=str.charCodeAt(i);x=Math.imul(x,16777619);}return (x>>>0)/4294967296;}
function rgba(hex,a){const n=parseInt(hex.slice(1),16);return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`;}
function T(s,x,y,o={}){const{size=14,w=500,c=C.cream,a='left',al=1,ls=0,glow=0}=o;X.save();X.globalAlpha*=al;X.font=`${w} ${size}px ${F}`;X.fillStyle=c;X.textAlign=a;X.textBaseline='alphabetic';
  if('letterSpacing' in X)X.letterSpacing=ls+'px';if(glow){X.shadowColor=c;X.shadowBlur=glow;}X.fillText(s,x,y);X.restore();}
function MW(s,size,w=500,ls=0){X.save();X.font=`${w} ${size}px ${F}`;if('letterSpacing' in X)X.letterSpacing=ls+'px';const m=X.measureText(s).width;X.restore();return m;}
function fit(s,size,w,max){if(MW(s,size,w)<=max)return s;let t=s;while(t.length>1&&MW(t+'…',size,w)>max)t=t.slice(0,-1);return t+'…';}
function glow(x,y,r,c,a){const g=X.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,rgba(c,a));g.addColorStop(.3,rgba(c,a*.35));g.addColorStop(1,rgba(c,0));X.fillStyle=g;X.fillRect(x-r,y-r,2*r,2*r);}
function brackets(x,y,w,h,l=12,c=C.gold,lw=2){X.save();X.strokeStyle=c;X.lineWidth=lw;X.beginPath();X.moveTo(x,y+l);X.lineTo(x,y);X.lineTo(x+l,y);X.moveTo(x+w-l,y);X.lineTo(x+w,y);X.lineTo(x+w,y+l);
  X.moveTo(x+w,y+h-l);X.lineTo(x+w,y+h);X.lineTo(x+w-l,y+h);X.moveTo(x+l,y+h);X.lineTo(x,y+h);X.lineTo(x,y+h-l);X.stroke();X.restore();}
const HEX='0123456789abcdef';
function scr(s,p,seed){let o='';for(let i=0;i<s.length;i++)o+=p>i/s.length+.12?s[i]:HEX[Math.floor(rnd(seed+i*3.7+Math.floor(p*24))*16)];return o;}
const typed=(s,p)=>s.slice(0,Math.floor(s.length*cl(p)));
const S=D.stats, MEM=D.memories, FOCUS=D.focus.length?D.focus:[{id:'no memories yet',anchor:'anchormem add …',status:'ok',ok:true}];
function empty(b,msg,cmd){T(msg,b.x,b.y+30,{size:11.5,c:C.dim});if(cmd)T(cmd,b.x,b.y+54,{size:11.5,w:700,c:C.amber});}
function panel(x,y,w,h,n,title,tag){X.fillStyle=C.panel;X.fillRect(x,y,w,h);X.strokeStyle=C.line;X.lineWidth=1;X.strokeRect(x+.5,y+.5,w-1,h-1);brackets(x,y,w,h,10,C.gold,2);
  X.fillStyle=C.gold;X.fillRect(x+10,y+10,26,18);T(String(n).padStart(2,'0'),x+23,y+24,{size:12,w:800,c:C.bg,a:'center'});
  T(title,x+44,y+24,{size:12.5,w:700,c:C.cream,ls:1.5});if(tag)T(fit(tag,11,700,w-60-MW(title,12.5,700,1.5)),x+w-10,y+24,{size:11,w:700,c:C.gold,a:'right',ls:1});return{x:x+10,y:y+38,w:w-20,h:h-48};}

// ---------- constellation from the real graph (files, functions, memories)
const STAR=(()=>{const n=[],l=[],idx={},G=D.graph;const files=G.nodes.filter(v=>v.kind==='file');const R=WIDE?440:340;
  files.forEach(f=>{const th=hs(f.id)*TAU,ph=Math.acos(2*hs(f.id+'y')-1),r=R*(.55+.45*hs(f.id+'r'));idx[f.id]=n.length;
    n.push({x:r*Math.sin(ph)*Math.cos(th),y:r*Math.cos(ph)*.62,z:r*Math.sin(ph)*Math.sin(th),k:'file',st:f.status});});
  const parent={};G.links.forEach(([a,b,s])=>{if(s==='part')parent[a]=b;});
  G.nodes.forEach(v=>{if(v.kind!=='symbol')return;const p=n[idx[parent[v.id]]]||{x:0,y:0,z:0},a=hs(v.id)*TAU,b=hs(v.id+'b')*TAU,d=28+hs(v.id+'d')*34;idx[v.id]=n.length;
    n.push({x:p.x+d*Math.cos(a)*Math.cos(b),y:p.y+d*Math.sin(b)*.8,z:p.z+d*Math.sin(a)*Math.cos(b),k:'sym',st:v.status});});
  G.nodes.forEach(v=>{if(v.kind!=='memory')return;const tgt=G.links.find(([a])=>a===v.id);const p=tgt&&n[idx[tgt[1]]]||{x:0,y:0,z:0};idx[v.id]=n.length;
    n.push({x:p.x*.82+(hs(v.id)-.5)*60,y:p.y*.82+(hs(v.id+'y')-.5)*44,z:p.z*.82+(hs(v.id+'z')-.5)*60,k:'mem',st:v.status,id:v.label});});
  G.links.forEach(([a,b,s])=>{if(idx[a]!=null&&idx[b]!=null)l.push([idx[a],idx[b],s]);});
  for(let i=0;i<200;i++)n.push({x:(rnd(i*.71)-.5)*1300,y:(rnd(i*.37)-.5)*620,z:(rnd(i*.53)-.5)*1300,k:'dust'});
  return{n,l,idx};})();
const bad=s=>s&&s!=='ok'&&s!=='unverified'&&s!=='code';
function constellation(t,x0,y0,w,h){X.save();X.beginPath();X.rect(x0,y0,w,h);X.clip();
  const g=X.createRadialGradient(x0+w/2,y0+h/2,0,x0+w/2,y0+h/2,Math.max(w,h)*.6);g.addColorStop(0,'#1a1209');g.addColorStop(1,'#0b0805');X.fillStyle=g;X.fillRect(x0,y0,w,h);
  X.strokeStyle=rgba(C.gold,.06);X.lineWidth=1;for(let gx=x0;gx<x0+w;gx+=40){X.beginPath();X.moveTo(gx,y0);X.lineTo(gx,y0+h);X.stroke();}for(let gy=y0;gy<y0+h;gy+=40){X.beginPath();X.moveTo(x0,gy);X.lineTo(x0+w,gy);X.stroke();}
  const rot=t*TAU/LOOP,cr=Math.cos(rot),sr=Math.sin(rot),tl=.28,ct=Math.cos(tl),st=Math.sin(tl),Dd=1300,cx=x0+w/2,cy=y0+h/2+6,sx=w/(WIDE?820:880),sy=h/(WIDE?640:430);
  const P=STAR.n.map(v=>{const x=v.x*cr-v.z*sr,z=v.x*sr+v.z*cr,y2=v.y*ct-z*st,z2=v.y*st+z*ct,s=Dd/(Dd+z2+300);return{x:cx+x*s*sx,y:cy+y2*s*sy,s,z:z2};});
  const slot=Math.floor(t/5)%FOCUS.length,fp=per(t,5)/5,fm=FOCUS[slot],fi=STAR.idx['m:'+fm.id];
  X.globalCompositeOperation='lighter';
  STAR.l.forEach(([i,j,s],q)=>{const A=P[i],B=P[j],mem=s!=='part',red=bad(s),dep=cl(1.15-A.z/700,.25,1);
    X.strokeStyle=rgba(red?C.red:C.gold,(mem?.5:.16)*dep);X.lineWidth=mem?1.5:1;X.beginPath();X.moveTo(A.x,A.y);X.lineTo(B.x,B.y);X.stroke();
    if(mem){const p=per(t*.4+rnd(q),1),u=red?1-p:p;glow(lerp(A.x,B.x,u),lerp(A.y,B.y,u),7,red?C.red:C.amber,.85*Math.sin(Math.PI*p)*dep);}});
  P.forEach((p,i)=>{const v=STAR.n[i],dep=cl(1.15-p.z/700,.2,1);if(v.k==='dust'){X.fillStyle=rgba(C.amber,.22*dep);X.fillRect(p.x,p.y,1.3,1.3);return;}
    const red=bad(v.st),r=v.k==='mem'?3.8:v.k==='file'?2.9:1.5,c=red?C.red:v.k==='file'?C.cream:C.gold,lit=v.k!=='sym'||v.st!=='code';
    glow(p.x,p.y,r*(v.k==='mem'?8:5)*p.s,c,(v.k==='sym'?(lit?.35:.2):.55)*dep);X.fillStyle=rgba(c,(lit?.95:.6)*dep);X.beginPath();X.arc(p.x,p.y,r*p.s,0,TAU);X.fill();
    if(red&&v.k==='mem'){const ph=per(t*.6+hs(v.id||''),1);X.strokeStyle=rgba(C.red,(1-ph)*.8*dep);X.lineWidth=1.6;X.beginPath();X.arc(p.x,p.y,6+ph*22,0,TAU);X.stroke();}});
  X.globalCompositeOperation='source-over';
  if(fi!=null){const f=P[fi],a=eOut(seg(fp,0,.12))*(1-seg(fp,.9,1)),spn=t*Math.PI/5;
    X.save();X.globalAlpha=a;X.strokeStyle=C.amber;X.lineWidth=1.5;X.translate(f.x,f.y);X.rotate(spn);for(let k=0;k<4;k++){X.rotate(TAU/4);X.beginPath();X.arc(0,0,22,-.45,.45);X.stroke();}
    X.rotate(-spn);X.beginPath();X.arc(0,0,36,0,TAU);X.setLineDash([3,6]);X.strokeStyle=rgba(C.gold,.6);X.stroke();X.setLineDash([]);X.restore();
    const red=!fm.ok,bw=290,bx=f.x>x0+w*.58?f.x-bw-60:f.x+60,by=cl(f.y-70,y0+40,y0+h-130);
    X.save();X.globalAlpha=a;X.strokeStyle=rgba(C.amber,.7);X.lineWidth=1;X.beginPath();X.moveTo(f.x+(bx<f.x?-36:36),f.y);X.lineTo(bx<f.x?bx+bw:bx,by+40);X.stroke();
    X.fillStyle='rgba(10,8,5,.92)';X.fillRect(bx,by,bw,98);X.strokeStyle=red?C.red:C.gold;X.lineWidth=1.5;X.strokeRect(bx,by,bw,98);X.restore();
    T('MEMORY',bx+12,by+20,{size:10,w:800,c:C.dim,ls:2,al:a});T(fit(fm.id,14,700,bw-24),bx+12,by+42,{size:14,w:700,c:C.cream,al:a});
    T(fit(fm.anchor,11,500,bw-24),bx+12,by+62,{size:11,c:C.dim,al:a});T(red?'✗ '+fm.status.toUpperCase():'✓ OK',bx+12,by+85,{size:12,w:800,c:red?C.red:C.gold,al:a,ls:1});
    if(red)T('check before trusting',bx+bw-12,by+85,{size:10,c:C.red,a:'right',al:a});}
  X.restore();}

// ---------- panels: all real data
function pMemory(t,b){if(!MEM.length)return empty(b,'no memories yet','anchormem add …');const p=per(t,7.5)/7.5,m=MEM[Math.floor(t/7.5)%MEM.length],red=!m.ok;
  T('MEMORY · '+(red?m.status.toUpperCase():'OK'),b.x,b.y+14,{size:10,w:800,c:red?C.red:C.dim,ls:2});
  const words=m.title.split(' ');let line='',ln=[];for(const wd of words){if(MW(line+' '+wd,15,700)>b.w&&line){ln.push(line);line=wd;}else line=line?line+' '+wd:wd;}ln.push(line);
  ln.slice(0,3).forEach((s,k)=>T(s,b.x,b.y+40+k*20,{size:15,w:700,c:C.cream,al:eOut(seg(p,0,.1))}));
  T(fit(m.body||'',11,500,b.w),b.x,b.y+108,{size:11,c:C.dim});
  X.strokeStyle=red?C.red:C.gold;X.lineWidth=1.5;X.strokeRect(b.x+.5,b.y+120,b.w-1,30);T(fit('⚓ '+m.anchor,11,700,b.w-20),b.x+10,b.y+140,{size:11,w:700,c:red?C.red:C.amber});
  T('origin: '+m.origin,b.x,b.y+176,{size:10.5,c:C.dim});T(m.approved?'approved ✓':'not approved',b.x+b.w,b.y+176,{size:10.5,c:m.approved?C.gold:C.red,a:'right'});}
function pMatrix(t,b){const cols=['found','fp','okd','fdbk','lint'],rows=MEM.slice(0,Math.min(6,Math.floor((b.h-30)/24)));if(!rows.length)return empty(b,'no memories yet');
  const cx=j=>b.x+b.w-((cols.length-1-j)*30)-12;cols.forEach((c,j)=>T(c,cx(j),b.y+12,{size:9.5,c:C.dim,a:'center'}));
  rows.forEach((r,i)=>{const y=b.y+34+i*24,on=Math.floor(t*2)%rows.length===i;if(on){X.fillStyle=rgba(C.gold,.12);X.fillRect(b.x,y-15,b.w,22);}
    T(fit(r.id,11,500,cx(0)-b.x-22),b.x,y,{size:11,c:!r.ok?C.red:on?C.amber:C.cream});
    r.matrix.forEach((v,j)=>{X.fillStyle=v?(on?C.amber:rgba(C.gold,.8)):(j===1?C.red:C.line2);X.beginPath();X.arc(cx(j),y-4,on&&v?5:3.6,0,TAU);X.fill();});});}
function pFingerprint(t,b){const m=FOCUS.find(x=>x.fp)||{};if(!m.fp)return empty(b,'no fingerprints yet');const p=per(t,6)/6,changed=m.fp_now&&m.fp_now!==m.fp;
  T(fit(m.id,10.5,500,b.w),b.x,b.y+14,{size:10.5,c:C.dim});const shown=changed&&p>.5?scr(m.fp_now,(p-.5)*3,9):scr(m.fp,p*4,3);
  T(shown,b.x+b.w/2,b.y+70,{size:WIDE?22:24,w:800,c:changed&&p>.5?C.red:C.amber,a:'center',ls:2,glow:12});
  T('stored  '+m.fp,b.x,b.y+108,{size:11,c:C.dim});T(changed?'now     '+m.fp_now:'now     '+m.fp,b.x,b.y+126,{size:11,c:changed?C.red:C.dim});
  T(changed?'≠ mismatch → CHANGED':'= match → OK',b.x,b.y+148,{size:12,w:800,c:changed?C.red:C.gold});
  const hex=(m.fp+(m.fp_now||m.fp)).slice(0,24),bh=Math.max(0,b.h-170);if(bh>12)for(let k=0;k<24;k++){const v=parseInt(hex[k],16)/15,hh=6+v*(bh-6);X.fillStyle=rgba(changed&&k>=12?C.red:C.gold,.5);X.fillRect(b.x+k*(b.w/24),b.y+b.h-hh,b.w/24-2,hh);}}
function pCheck(t,b){const p=per(t,10)/10;T('$ '+typed('anchormem check',seg(p,0,.15)),b.x,b.y+14,{size:12,w:700,c:C.cream});
  const rows=MEM.slice(0,Math.max(1,Math.floor((b.h-60)/20)));rows.forEach((m,i)=>{const a=seg(p,.17+i*.04,.2+i*.04);T((m.ok?'✓ ':'✗ ')+fit(m.id,11,500,b.w-90),b.x,b.y+40+i*20,{size:11,c:m.ok?C.cream:C.red,al:a});
    T(m.status.toUpperCase(),b.x+b.w,b.y+40+i*20,{size:10.5,w:700,c:m.ok?C.gold:C.red,a:'right',al:a});});
  const a=seg(p,.5,.55),yy=b.y+b.h-4;T(S.stale?`${S.stale} of ${S.memories} need checking`:`all ${S.memories} fresh`,b.x,yy,{size:11,c:C.dim,al:a});
  T(`${D.check.ms} ms · exit ${D.check.exit}`,b.x+b.w,yy,{size:11,w:800,c:C.amber,a:'right',al:a});}
function pCode(t,b){const cd=D.code;if(!cd.lines.length)return empty(b,'no code to show');const p=per(t,10)/10;
  T(fit(cd.anchor,10.5,500,b.w),b.x,b.y+14,{size:10.5,c:C.dim});const max=Math.floor((b.h-50)/21);
  cd.lines.slice(0,max).forEach(([k,s],i)=>{const y=b.y+40+i*21,a=seg(p,i*.06,i*.06+.05);if(k!==' '){X.fillStyle=rgba(k==='+'?C.red:C.dim2,.18*a);X.fillRect(b.x,y-15,b.w,20);X.fillStyle=rgba(k==='+'?C.red:C.dim,a);X.fillRect(b.x,y-15,3,20);}
    T(k===' '?'':k,b.x+8,y,{size:11,w:800,c:k==='+'?C.red:C.dim,al:a});T(fit(s,11.5,500,b.w-26),b.x+22,y,{size:11.5,c:k==='-'?C.dim:k==='+'?C.cream:C.cream,al:a});});
  T(cd.changed?'git diff since the memory was verified':'the anchored code',b.x,b.y+b.h-4,{size:10.5,w:700,c:cd.changed?C.red:C.gold});}
function pLog(t,b){const L=D.log;if(!L.length)return empty(b,'nothing logged yet');const n=Math.floor((b.h-10)/20),rows=L.slice(-n).reverse();
  const short={'feedback-applied':'applied','feedback-violated':'violated','feedback-outdated':'outdated','handoff-new':'handoff','handoff-take':'taken'};
  rows.forEach(([ts,op,id],i)=>{const y=b.y+16+i*20,o=short[op]||op;T(ts.slice(6),b.x,y,{size:10.5,c:C.dim});
    T(fit(o,11,700,66),b.x+50,y,{size:11,w:700,c:o==='retire'||o==='rm'||o==='violated'?C.red:C.amber});T(fit(id,11,500,b.w-124),b.x+124,y,{size:11,c:C.cream});});}
function pHooks(t,b){const E=D.hooks;if(!E.length)return empty(b,'no hook events yet','anchormem hooks install');const p=per(t,15)/15,n=Math.floor((b.h-20)/22);
  E.slice(-n).forEach(([ev,file,nm,ns,note],i)=>{const a=seg(p,i*.1,i*.1+.05),y=b.y+16+i*22,red=ns>0;X.fillStyle=rgba(red?C.red:C.gold,.85*a);X.beginPath();X.arc(b.x+4,y-4,3.5,0,TAU);X.fill();
    const label=ev==='SessionStart'?'start':ev;T(label,b.x+16,y,{size:11,w:700,c:red?C.red:C.amber,al:a});
    const rest=ev==='SessionStart'?`${nm} memories${ns?' · '+ns+' stale':''}${note?' · '+note:''}`:`${file.split('/').pop()} → ${nm}${ns?' · '+ns+' stale':''}`;
    T(fit(rest,10.5,500,b.w-26-MW(label,11,700)),b.x+26+MW(label,11,700),y,{size:10.5,c:C.cream,al:a});});}
function pFiles(t,b){const Fs=D.files;if(!Fs.length)return empty(b,'no anchors yet');const mx=Math.max(...Fs.map(f=>f[1])),n=Math.floor((b.h-10)/25);
  Fs.slice(0,n).forEach(([f,c,st],i)=>{const y=b.y+18+i*25,bw=b.w-120,wv=bw*(c/mx)*(.9+.1*Math.sin(t*TAU/LOOP*3+i));T(fit(f.split('/').pop(),11,500,86),b.x,y,{size:11,c:C.cream});
    X.fillStyle=C.line;X.fillRect(b.x+92,y-9,bw,9);X.fillStyle=st?C.red:C.gold;X.fillRect(b.x+92,y-9,wv,9);T(String(c),b.x+b.w,y,{size:11,w:700,c:C.amber,a:'right'});});}
function pSeed(t,b){const sd=D.seed,p=per(t,15)/15,n=Math.round(sd.seeded*eOut(seg(p,0,.6)));T('from the repo\'s comments',b.x,b.y+14,{size:10.5,c:C.dim});
  T(String(n),b.x+b.w,b.y+42,{size:34,w:800,c:C.amber,a:'right',glow:14});T('memories seeded',b.x,b.y+36,{size:11,c:C.cream});T(`${sd.notes} notes · ${sd.ready} ready`,b.x,b.y+52,{size:11,c:C.cream});
  const r=sd.recent.length?sd.recent:['anchormem seed --apply'];r.slice(0,Math.floor((b.h-80)/21)).forEach((s,i)=>T((sd.recent.length?'+ ':'$ ')+fit(s,10.5,500,b.w-16),b.x,b.y+84+i*21,{size:10.5,c:i===0?C.amber:C.dim,al:1-i*.18}));}
function pHandoff(t,b){const h=D.handoff;if(!h)return empty(b,'no handoffs','anchormem handoff new "…"');T(`from ${h.from||'?'} → ${h.to}`,b.x,b.y+14,{size:10.5,c:C.dim});
  const words=h.summary.split(' ');let line='',ln=[];for(const wd of words){if(MW(line+' '+wd,12,700)>b.w&&line){ln.push(line);line=wd;}else line=line?line+' '+wd:wd;}ln.push(line);
  ln.slice(0,2).forEach((s,k)=>T(s,b.x,b.y+40+k*18,{size:12,w:700,c:C.cream}));let y=b.y+84;
  h.next.slice(0,1).forEach(s=>{T(fit('next: '+s,11,500,b.w),b.x,y,{size:11,c:C.amber});y+=20;});
  h.files.slice(0,1).forEach(([f,st])=>{T(fit(`${f.split('/').pop()} (${st})`,10.5,500,b.w),b.x,y,{size:10.5,c:C.dim});});
  const taken=h.status==='accepted';X.strokeStyle=taken?C.gold:C.line2;X.lineWidth=1.5;X.strokeRect(b.x+.5,b.y+b.h-30,b.w-1,28);
  T(taken?`✓ TAKEN${h.by?' BY '+h.by.toUpperCase():''} · ONCE`:h.status==='pending'?'WAITING · TAKE IT ONCE':h.status.toUpperCase(),b.x+b.w/2,b.y+b.h-11,{size:10.5,w:800,c:taken?C.gold:C.dim,a:'center',ls:1});}
function pConfidence(t,b){const m=D.confidence;T(m?fit(m.id,10.5,500,b.w):'wilson lower bound',b.x,b.y+14,{size:10.5,c:C.dim});if(!m)return empty({x:b.x,y:b.y+20},'no feedback reports yet','anchormem feedback <id> applied');
  const v=(m.score||0)*eOut(seg(per(t,10)/10,0,.4)),cx=b.x+b.w/2,cy=b.y+b.h*.62,r=Math.min(62,b.w/2-14);X.lineWidth=10;X.strokeStyle=C.line;X.beginPath();X.arc(cx,cy,r,Math.PI,TAU);X.stroke();
  X.strokeStyle=m.band==='high'?C.gold:m.band==='medium'?C.amber:C.red;X.beginPath();X.arc(cx,cy,r,Math.PI,Math.PI+Math.PI*cl(v));X.stroke();
  T(v.toFixed(2),cx,cy-8,{size:24,w:800,c:C.cream,a:'center'});T((m.band||'').toUpperCase(),cx,cy+12,{size:10.5,w:800,c:C.dim,a:'center',ls:2});
  T(`${m.applied} applied · ${m.violated} violated`,cx,b.y+b.h-2,{size:10.5,c:C.dim,a:'center'});}
function pStart(t,b){const p=per(t,10)/10;T('START TODAY',b.x,b.y+14,{size:10.5,w:800,c:C.amber,ls:2});
  const L=['$ pip install \\','  git+https://github.com/','  ChiragPatankar/anchormem','$ anchormem dashboard --hud'];
  L.forEach((s,i)=>T(typed(s,seg(p,i*.15,i*.15+.14)),b.x,b.y+42+i*22,{size:11,c:i===3?C.amber:C.cream}));
  if(Math.floor(t*2.4)%2===0){X.fillStyle=C.amber;X.fillRect(b.x+MW(typed(L[3],seg(p,.45,.59)),11)+2,b.y+97,7,14);}
  T('free · MIT · zero deps',b.x,b.y+b.h-4,{size:10.5,c:C.dim});}

const TABS=['check','dashboard','seed','try','hooks','handoff'];
const CMDS=['anchormem check','anchormem dashboard --hud','anchormem seed --apply','anchormem try github.com/psf/requests','anchormem hooks install','anchormem handoff take'];
const grain=[...Array(5)].map((_,k)=>{const g=document.createElement('canvas');g.width=W/2;g.height=H/2;const gx=g.getContext('2d'),im=gx.createImageData(g.width,g.height);
  for(let i=0;i<im.data.length;i+=4){const v=rnd(i*.37+k*99)*255;im.data[i]=im.data[i+1]=im.data[i+2]=v;im.data[i+3]=255;}gx.putImageData(im,0,0);return g;});
const P12=[['MEMORY',`${S.memories} total`,pMemory],['STATUS MATRIX','',pMatrix],['FINGERPRINT','no llm',pFingerprint],['CHECK','exit codes',pCheck],
  ['CODE','',pCode],['LOG','append-only',pLog],['HOOKS','claude code',pHooks],['FILES','by memories',pFiles],
  ['SEED · NOTES','',pSeed],['HANDOFF','agent → agent',pHandoff],['CONFIDENCE','',pConfidence],['ONE COMMAND','setup',pStart]];
function render(t){t=per(t,LOOP);X.setTransform(1,0,0,1,0,0);X.globalAlpha=1;X.globalCompositeOperation='source-over';X.fillStyle=C.bg;X.fillRect(0,0,W,H);
  T('ANCHORMEM / '+D.repo.toUpperCase(),30,40,{size:15,w:800,c:C.cream,ls:2});
  T(`${S.memories} memories · ${S.anchors} anchors · ${S.approved}/${S.memories} approved · commit ${D.commit}`,30,62,{size:11.5,c:C.dim});
  const tab=Math.floor(t/5)%TABS.length;let tx=W-30;for(let i=TABS.length-1;i>=0;i--){const w2=MW(TABS[i],11,700)+18;tx-=w2;const on=i===tab;
    X.fillStyle=on?C.gold:'transparent';X.fillRect(tx,24,w2-4,22);X.strokeStyle=on?C.gold:C.line2;X.strokeRect(tx+.5,24.5,w2-5,21);T(TABS[i],tx+(w2-4)/2,39,{size:11,w:700,c:on?C.bg:C.dim,a:'center'});}
  T(LIVE?'LIVE':'SNAPSHOT '+D.generated,W-30,66,{size:11,w:800,c:LIVE?C.red:C.dim,a:'right'});if(LIVE){X.fillStyle=Math.floor(t*2)%2?C.red:rgba(C.red,.35);X.beginPath();X.arc(W-70,62,4,0,TAU);X.fill();}
  // areas
  let G,CB,GRID;if(WIDE){G=[30,86,900,890];CB=[30,986,900,40];GRID=[950,86,940,940];}else{G=[30,86,W-60,430];CB=[30,528,W-60,40];GRID=[30,580,W-60,710];}
  X.strokeStyle=C.line;X.strokeRect(G[0]+.5,G[1]+.5,G[2]-1,G[3]-1);constellation(t,...G);brackets(G[0],G[1],G[2],G[3],16,C.gold,2.5);
  X.fillStyle=C.gold;X.fillRect(G[0]+12,G[1]+12,26,18);T('01',G[0]+25,G[1]+26,{size:12,w:800,c:C.bg,a:'center'});T('MEMORY GRAPH · memories anchored to code',G[0]+46,G[1]+26,{size:12.5,w:700,c:C.cream,ls:1.5});
  T(`${S.memories} memories · ${S.files} files`,G[0]+G[2]-12,G[1]+26,{size:11,w:700,c:C.gold,a:'right'});
  T('gold = memory · cream = file · red = its code changed',G[0]+12,G[1]+G[3]-12,{size:10.5,c:C.dim});
  const ci=Math.floor(t/5)%CMDS.length,cp=per(t,5)/5;X.fillStyle=C.panel2;X.fillRect(CB[0],CB[1],CB[2],CB[3]);X.strokeStyle=C.line2;X.strokeRect(CB[0]+.5,CB[1]+.5,CB[2]-1,CB[3]-1);
  T('>',CB[0]+16,CB[1]+26,{size:15,w:800,c:C.gold});const cmd=typed(CMDS[ci],seg(cp,0,.35));T(cmd,CB[0]+36,CB[1]+26,{size:14,c:C.cream});
  if(Math.floor(t*2.4)%2===0){X.fillStyle=C.gold;X.fillRect(CB[0]+36+MW(cmd,14),CB[1]+12,8,18);}
  T(S.stale?`${S.stale} ${S.stale===1?'memory needs':'memories need'} a look`:'all memories fresh',CB[0]+CB[2]-16,CB[1]+26,{size:11.5,w:700,c:S.stale?C.red:C.gold,a:'right'});
  const gap=10,pw=(GRID[2]-3*gap)/4,ph=(GRID[3]-2*gap)/3;
  P12.forEach(([ti,tag,fn],k)=>{const c=k%4,r=Math.floor(k/4),px=GRID[0]+c*(pw+gap),py=GRID[1]+r*(ph+gap),b=panel(px,py,pw,ph,k+2,ti,tag);
    X.save();X.beginPath();X.rect(b.x-2,b.y-2,b.w+4,b.h+6);X.clip();fn(per(t+k*2.35,LOOP),b);X.restore();});
  const msg=`anchormem  ·  ${D.repo}  ·  ${S.memories} memories  ·  ${S.stale} need a look  ·  ${S.approved} approved  ·  github.com/ChiragPatankar/anchormem  ·  `;
  const ty=WIDE?1046:1300,mw=MW(msg,12,700,1),off=-(t/LOOP)*mw*(WIDE?2:1);X.fillStyle=C.panel;X.fillRect(0,ty,W,H-ty);X.strokeStyle=C.line;X.beginPath();X.moveTo(0,ty+.5);X.lineTo(W,ty+.5);X.stroke();
  for(let k=0;k<Math.ceil(W/mw)+3;k++)T(msg,per(off,mw)-mw+k*mw,ty+(H-ty)/2+5,{size:12,w:700,c:C.gold,ls:1});
  const v=X.createRadialGradient(W/2,H/2,H*.3,W/2,H/2,Math.max(W,H)*.75);v.addColorStop(0,'rgba(0,0,0,0)');v.addColorStop(1,'rgba(0,0,0,.42)');X.fillStyle=v;X.fillRect(0,0,W,H);
  X.save();X.globalAlpha=.045;X.globalCompositeOperation='overlay';X.drawImage(grain[Math.floor(t*24)%5],0,0,W,H);X.restore();
  X.save();X.globalAlpha=.06;X.fillStyle='#000';for(let y=0;y<H;y+=3)X.fillRect(0,y,W,1);X.restore();}
window.render=render;
window.ready=Promise.all(['800 15px "JetBrains Mono"','500 14px "JetBrains Mono"','700 12px "JetBrains Mono"'].map(f=>document.fonts.load(f).catch(()=>null))).then(()=>{render(0);return true;});
if(!new URLSearchParams(location.search).has('still')){window.ready.then(()=>{const t0=performance.now();(function f(){render((performance.now()-t0)/1000);requestAnimationFrame(f);})();});}
if(LIVE){let ver=null;setInterval(()=>{fetch('/version',{cache:'no-store'}).then(r=>r.text()).then(v=>{v=v.trim();if(!/^[0-9a-f]{12}$/.test(v))return;if(ver&&v!==ver)location.reload();ver=v;}).catch(()=>{});},3000);}
</script></body></html>
"""

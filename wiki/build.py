"""data/ → dist/ 整站生成。

    python build.py            # 校验通过才生成；ERROR>0 直接停，不产出半成品站

路由：
    index.html                   首页 · 纪元与分期入口 + 六类实体门户
    timeline.html                年表（非线性刻度：史前压缩，唐宋以后舒展）
    about.html                   编纂方针 + 信源分级 + 参考书目
    periods/<id>.html            分期
    artists/<id>.html            艺术家（十二维）
    works/<id>.html              作品（图版对照；按 kind 分派维度）
    sites/<id>.html              遗址（含子遗址与其下作品）
    classes/<id>.html            类目（器类/窑口/书体/画科/流派）
    treatises/<id>.html          画论
    events/<id>.html             事件
    catalog/{works,artists,sites,classes,treatises,events}.html
    search.json                  客户端检索索引
"""

import json
import shutil
import sys
from pathlib import Path

import render as r
from schema import (ARTIST_GENERATED, ARTIST_SECTIONS, CLASS_SECTIONS, DISPUTES,
                    EVENT_SECTIONS, IMAGE_STATUS, RECORD_SECTIONS, SITE_SECTIONS,
                    TREATISE_SECTIONS, WORK_KIND_GROUP, WORK_SECTIONS, Corpus,
                    depth_of, validate)

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
STATIC = ROOT / "static"

# 年表刻度断点：(年份, 百分位)。刻意非线性——史前压到极窄，唐宋以后舒展，
# 因为条目密度就长在唐宋以后。硬拉成线性会把宋以降全挤成一条缝。
SCALE = [(-7000, 0), (-2000, 10), (-1046, 16), (-221, 24), (220, 32),
         (589, 40), (907, 50), (1279, 64), (1368, 71), (1644, 80),
         (1911, 90), (1949, 94), (2030, 100)]


def pos(year):
    if year is None:
        year = 2026
    year = max(SCALE[0][0], min(SCALE[-1][0], year))
    for (y1, p1), (y2, p2) in zip(SCALE, SCALE[1:]):
        if y1 <= year <= y2:
            return p1 + (p2 - p1) * (year - y1) / (y2 - y1)
    return 100.0


def clean():
    """清旧产物但保留 dist/ 目录本身——起了预览服务器时 Windows 会锁住它，
    整目录 rmtree 会失败。逐文件删既能重建，又不必先停服务器。"""
    if not DIST.exists():
        DIST.mkdir(parents=True)
        return
    locked = []
    for f in sorted(DIST.rglob("*"), key=lambda p: -len(p.parts)):
        if f.is_file():
            try:
                f.unlink()
            except PermissionError:
                locked.append(f)
    if locked:
        print(f"警告：{len(locked)} 个文件被占用未能删除，将被覆盖而非重建")


def _write(rel, text):
    p = DIST / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _sections(obj, spec, depth, c, notes=None):
    notes = notes or {}
    known = {s for s, _ in spec} | {DISPUTES[0]}
    out = []
    for sid, label in spec:
        if sid in obj.get("sections", {}):
            out.append(r.section(sid, label, r.blocks(obj["sections"][sid], depth, c),
                                 notes.get(sid, "")))
    for sid, body in obj.get("sections", {}).items():
        if sid not in known:
            out.append(r.section(sid, obj.get("section_titles", {}).get(sid, sid),
                                 r.blocks(body, depth, c), "本条目自加维度"))
    if DISPUTES[0] in obj.get("sections", {}):
        out.append(r.section(DISPUTES[0], DISPUTES[1],
                             r.blocks(obj["sections"][DISPUTES[0]], depth, c),
                             "并陈异说，不代读者裁断"))
    return "\n".join(out)


def _srcbar(obj, depth, c):
    items = []
    for s in obj.get("sources", []):
        if isinstance(s, str):
            items.append(r.inline(f"[[src:{s}]]", depth, c))
        else:
            note = f' <span class="sb-n">{r.e(s.get("note", ""))}</span>' if s.get("note") else ""
            ref = r.inline(f"[[src:{s['ref']}]]", depth, c) if s.get("ref") else r.e(s.get("text", ""))
            items.append(ref + note)
    v = obj.get("verification", "—")
    vlabel = {"museum": "馆藏官方核实", "wikidata": "结构化数据核对",
              "memory": "未经外部核对"}.get(v, v)
    return (f'<section class="sec srcbar" id="s-src"><h2 class="sec-h">'
            f'<span class="sec-n">信源</span>'
            f'<span class="sec-note">核查等级：{r.e(vlabel)}</span></h2>'
            f'<ol class="sb">{"".join(f"<li>{i}</li>" for i in items)}</ol>'
            f'<p class="sb-f">更新于 {r.e(obj.get("updated", "—"))}</p></section>')


def _toc(spec, obj, extra=()):
    ids = [(s, l) for s, l in spec if s in obj.get("sections", {})] + list(extra)
    if DISPUTES[0] in obj.get("sections", {}):
        ids.append(DISPUTES)
    li = "".join(f'<li><a href="#s-{r.e(s)}">{r.e(l)}</a></li>' for s, l in ids)
    return f'<nav class="toc"><ol>{li}</ol></nav>'


def _crumbs(c, period_id, tail):
    p = c.periods.get(period_id) or {}
    era = c.eras.get(p.get("era"), {})
    out = [("中国美术史", "index.html")]
    if era:
        out.append((era.get("name", "—"), "timeline.html"))
    if p:
        out.append((p.get("name", "—"), f"periods/{period_id}.html"))
    return out + [(tail, None)]


# ── 页面 ──────────────────────────────────────────────────────────────────


def page_artist(a, c):
    d, aid = 1, a["id"]
    works = c.works_of(aid)
    events = [e for e in c.events.values() if e.get("artist") == aid]
    gen = []
    if works:
        gen.append(r.section("works-index", ARTIST_GENERATED[0][1],
                             r.grid([r.card_work(w, d, c) for w in works]), "由数据推导，不手写"))
        gen.append(r.section("works-table", ARTIST_GENERATED[1][1], _works_table(works, d),
                             "由数据推导，不手写"))
    if events:
        gen.append(r.section("events-index", "关键事件索引",
                             r.grid([r.card_event(x, d, c) for x in events], "grid grid-flat")))
    alt = "、".join(a.get("name_alt") or [])
    hero = (f'<div class="hero-t"><h1>{r.e(a["name"])}</h1>'
            f'{f"<p class=hero-alt>{r.e(alt)}</p>" if alt else ""}'
            f'<p class="hero-m">{r.e(r._years(a))}'
            f'{" · " + r.e(a.get("native_place","")) if a.get("native_place") else ""}</p>'
            f'<p class="hero-d">{r.inline(a.get("one_line", ""), d, c)}</p>'
            f'<p class="hero-chips">{r.inline(f"[[period:{a['period']}]]", d, c)}'
            f'{r.chip(a.get("cat", ""))}'
            f'{"".join(r.chip(x, "chip-q") for x in (a.get("schools") or []))}</p></div>')
    plate = r.img_tag(a["portrait"], d, "ph-portrait") if a.get("portrait") else ""
    body = (f'<main class="wrap wrap-entry"><div class="hero">{hero}'
            f'{f"<div class=hero-p>{plate}</div>" if plate else ""}</div>'
            f'<div class="cols"><div class="col-main">'
            f'{_sections(a, ARTIST_SECTIONS, d, c)}{"".join(gen)}{_srcbar(a, d, c)}</div>'
            f'<aside class="col-rail">{_toc(ARTIST_SECTIONS, a, [("works-index", "代表作")] if works else [])}'
            f'{_lineage(a, d, c)}</aside></div></main>')
    return r.shell(f'{a["name"]} · 中国美术史', d, body,
                   _crumbs(c, a.get("period"), a["name"]), "t-artist")


def _works_table(works, d):
    rows = "".join(
        f'<tr><td><a href="{"../"*d}works/{r.e(w["id"])}.html">{r.e(w["title"])}</a></td>'
        f'<td>{r.e(w.get("kind",""))}</td><td>{r.e(w.get("year",""))}</td>'
        f'<td>{r.e(w.get("medium",""))}</td><td>{r.e(w.get("holder",""))}</td></tr>' for w in works)
    return (f'<table class="tbl"><thead><tr><th>作品</th><th>类</th><th>年代</th>'
            f'<th>材质</th><th>现藏</th></tr></thead><tbody>{rows}</tbody></table>')


def _lineage(a, d, c):
    """师承与交游。中国画史极重此线——「私淑」「上追」也是关系。"""
    ln = a.get("lineage") or {}
    if not ln:
        return ""
    def col(k, label):
        items = ln.get(k) or []
        if not items:
            return ""
        li = "".join(f"<li>{r.inline(i, d, c)}</li>" for i in items)
        return f'<div class="inf-c"><h4>{label}</h4><ul>{li}</ul></div>'
    cols = (col("teachers", "师") + col("peers", "友") + col("students", "弟子")
            + col("follows", "上追") + col("patrons", "赞助与鉴藏"))
    return f'<div class="inf"><h3>师承与交游</h3>{cols}</div>' if cols else ""


def page_work(w, c):
    d = 1
    group = WORK_KIND_GROUP.get(w.get("kind"), "书画")
    spec = WORK_SECTIONS[group]
    owner = ""
    if w.get("artist"):
        owner = r.inline(f'[[artist:{w["artist"]}]]', d, c)
    elif w.get("site"):
        owner = r.inline(f'[[site:{w["site"]}]]', d, c)
    im = w.get("image")
    if im:
        plate = (f'<div class="plate"><div class="plate-in">'
                 f'{r.img_tag(im, d, "ph-plate", "(min-width:1100px) 52vw, 94vw")}'
                 f'<div class="plate-cap"><span class="pc-t">{r.e(w["title"])}</span>'
                 f'<span class="pc-o">{r.e(w.get("title_orig", ""))}</span>'
                 f'<span class="pc-m">{r.e(w.get("medium", ""))}'
                 f'{" · " + r.e(w.get("size","")) if w.get("size") else ""}</span>'
                 f'<span class="pc-h">{r.e(w.get("holder", ""))}</span>'
                 f'{r._credit(im)}{r._img_links(im)}</div></div></div>')
    else:
        why = IMAGE_STATUS.get(w.get("image_status"), "原因未说明")
        extra = ""
        if w.get("image_status") in ("no-free-image", "in-situ"):
            extra = ("国内主要馆藏（故宫、国博、上博、台北故宫、敦煌研究院）均无公开 API "
                     "与自由授权影像，本库不自行取图。")
        plate = (f'<div class="plate"><div class="plate-in"><div class="plate-none">'
                 f'<span class="pn-t">无图版</span><span class="pn-w">{r.e(why)}</span>'
                 f'<span class="pn-e">{r.e(extra)}</span></div></div></div>')
    dets = "".join(r.figure(f["image"], d, f.get("caption"), c) for f in w.get("details", []))
    notes = {"dating": "依据与可靠度并列，不只给年份",
             "relayers": "今日所见常为叠压层，非原作原貌",
             "rubbings": "研究对象常是拓本而非原石"}
    # 著录级必须在页面上说出来。**不标出来就等于宣称它也经过了完整级那套审查**——
    # 而它只是照录了馆方字段，没有人读过材料、做过取舍。
    rec = ""
    if depth_of(w) == "record":
        spec = RECORD_SECTIONS
        rec = ('<p class="depth-note"><span class="dn-tag">著录级</span>'
               '本条只照录结构化来源（馆方记录）实际载明的内容——题名、年代、现藏、'
               '材质、尺寸、藏品号与图版出处。**未作论述与阐释，未下四态判断，'
               '断代依据亦非本库所判**。它与本库的完整级条目是两种体裁，'
               '不是同一体裁的详略之别。</p>')
    body = (f'<main class="split">{plate}<div class="app"><div class="app-in">'
            f'<h1 class="app-h1">{r.e(w["title"])}'
            f'<span class="app-o">{r.e(w.get("title_orig", ""))}</span></h1>'
            f'<p class="app-by">{owner}'
            f'{" · " + r.e(w.get("year","")) if w.get("year") else ""}'
            f' · {r.chip(w.get("kind",""))}</p>{rec}'
            f'{_toc(spec, w)}{_sections(w, spec, d, c, notes)}'
            f'{r.section("details", "细部", dets) if dets else ""}{_srcbar(w, d, c)}'
            f'<p class="backlink">{("← 返回 " + owner) if owner else ""}</p>'
            f'</div></div></main>')
    per = w.get("period") or (c.artists.get(w.get("artist")) or {}).get("period")
    return r.shell(f'{w["title"]} · 中国美术史', d, body,
                   _crumbs(c, per, w["title"]), "t-work has-plate")


def page_site(s, c):
    d, sid = 1, s["id"]
    kids, works = c.children_of(sid), c.works_at(sid)
    parent = c.sites.get(s.get("parent")) if s.get("parent") else None
    keys = "".join(r.chip(k) for k in (s.get("keys") or []))
    body = (f'<main class="wrap"><div class="p-hero">'
            f'<p class="p-era">{r.e("遗址" if not parent else "属 " + parent.get("name",""))}</p>'
            f'<h1>{r.e(s["name"])}<span class="p-lat">{r.e(s.get("name_en",""))}</span></h1>'
            f'<p class="p-span">{r.e(s.get("span",""))}'
            f'{" · " + r.e(s.get("where","")) if s.get("where") else ""}</p>'
            f'<p class="p-one">{r.e(s.get("one_line",""))}</p>'
            f'<div class="p-chips">{keys}</div></div>'
            f'<div class="cols"><div class="col-main">'
            f'{_sections(s, SITE_SECTIONS, d, c)}'
            f'{r.section("caves", f"子遗址与窟龛 · {len(kids)}", r.grid([r.card_site(k, d, c) for k in kids])) if kids else ""}'
            f'{r.section("works", f"此地作品 · {len(works)}", r.grid([r.card_work(x, d, c) for x in works])) if works else ""}'
            f'{_srcbar(s, d, c)}</div>'
            f'<aside class="col-rail">{_toc(SITE_SECTIONS, s)}</aside></div></main>')
    crumbs = [("中国美术史", "index.html"), ("遗址", "catalog/sites.html")]
    if parent:
        crumbs.append((parent.get("name", "—"), f'sites/{parent["id"]}.html'))
    return r.shell(f'{s["name"]} · 中国美术史', d, body, crumbs + [(s["name"], None)], "t-site")


def page_class(cl, c):
    d = 1
    members = [w for w in c.works.values() if cl["id"] in (w.get("classes") or [])]
    body = (f'<main class="wrap wrap-entry"><div class="hero"><div class="hero-t">'
            f'<h1>{r.e(cl["name"])}</h1>'
            f'<p class="hero-m">{r.e(cl.get("facet",""))}'
            f'{" · " + r.e(cl.get("span","")) if cl.get("span") else ""}</p>'
            f'<p class="hero-d">{r.inline(cl.get("one_line",""), d, c)}</p></div></div>'
            f'<div class="cols"><div class="col-main">'
            f'{_sections(cl, CLASS_SECTIONS, d, c)}'
            f'{r.section("members", f"归入此类的作品 · {len(members)}", r.grid([r.card_work(x, d, c) for x in members])) if members else ""}'
            f'{_srcbar(cl, d, c)}</div>'
            f'<aside class="col-rail">{_toc(CLASS_SECTIONS, cl)}</aside></div></main>')
    return r.shell(f'{cl["name"]} · 中国美术史', d, body,
                   [("中国美术史", "index.html"), ("类目", "catalog/classes.html"),
                    (cl["name"], None)], "t-class")


def page_treatise(t, c):
    d = 1
    body = (f'<main class="wrap wrap-entry"><div class="hero"><div class="hero-t">'
            f'<h1>{r.e(t["title"])}</h1>'
            f'<p class="hero-m">{r.e(t.get("author",""))}'
            f'{" · " + r.e(t.get("era_text","")) if t.get("era_text") else ""}</p>'
            f'<p class="hero-d">{r.inline(t.get("one_line",""), d, c)}</p></div></div>'
            f'<div class="cols"><div class="col-main">'
            f'{_sections(t, TREATISE_SECTIONS, d, c)}{_srcbar(t, d, c)}</div>'
            f'<aside class="col-rail">{_toc(TREATISE_SECTIONS, t)}</aside></div></main>')
    return r.shell(f'{t["title"]} · 中国美术史', d, body,
                   [("中国美术史", "index.html"), ("画论", "catalog/treatises.html"),
                    (t["title"], None)], "t-treatise")


def page_event(ev, c):
    d = 1
    who = r.inline(f'[[artist:{ev["artist"]}]]', d, c) if ev.get("artist") else ""
    body = (f'<main class="wrap wrap-entry"><div class="hero"><div class="hero-t">'
            f'<p class="hero-when">{r.e(ev.get("when",""))}</p>'
            f'<h1>{r.e(ev["title"])}</h1>'
            f'<p class="hero-d">{r.inline(ev.get("one_line",""), d, c)}</p></div></div>'
            f'<div class="cols"><div class="col-main">'
            f'{_sections(ev, EVENT_SECTIONS, d, c)}{_srcbar(ev, d, c)}'
            f'<p class="backlink">{("← 返回 " + who) if who else ""}</p></div>'
            f'<aside class="col-rail">{_toc(EVENT_SECTIONS, ev)}</aside></div></main>')
    per = (c.artists.get(ev.get("artist")) or {}).get("period")
    return r.shell(f'{ev["title"]} · 中国美术史', d, body,
                   _crumbs(c, per, ev["title"]), "t-event")


def page_period(p, c):
    d = 1
    arts = sorted((a for a in c.artists.values() if a.get("period") == p["id"]),
                  key=lambda a: (a.get("birth") or 0, a["id"]))
    works = [w for w in c.works.values() if w.get("period") == p["id"]]
    keys = "".join(r.chip(k) for k in (p.get("keys") or []))
    ctr = "".join(r.chip(k, "chip-q") for k in (p.get("centres") or []))
    disp = r.blocks([{"t": "stmt", "state": "disp", "text": p["dispute"]}], d, c) \
        if p.get("dispute") else ""
    anon = ""
    if p.get("anonymity"):
        anon = r.section("anonymity", "署名状况",
                         r.blocks([{"t": "p", "text": p["anonymity"]}], d, c),
                         "为何此期少有或没有艺术家条目")
    empty_a = ('<p class="empty">本期少有或没有可指名的作者，故艺术家条目稀少或缺席'
               '——理由见上方「署名状况」。这是史料状况，不是编纂欠账。</p>'
               if p.get("anonymity") else
               '<p class="empty">此分期尚无艺术家条目——尚未编纂，不是此期无人。</p>')
    body = (f'<main class="wrap"><div class="p-hero">'
            f'<p class="p-era">{r.e(c.eras.get(p.get("era"), {}).get("name",""))}</p>'
            f'<h1>{r.e(p["name"])}<span class="p-lat">{r.e(p.get("name_en",""))}</span></h1>'
            f'<p class="p-span">{r.e(p.get("span",""))}</p>'
            f'<p class="p-one">{r.e(p.get("one_line",""))}</p>'
            f'<div class="p-chips">{keys}{ctr}</div></div>'
            f'{r.section("dispute", "这个分期本身的问题", disp, "分期是后人的整理，不是史实") if disp else ""}'
            f'{anon}'
            f'{r.section("artists", f"艺术家 · {len(arts)}", r.grid([r.card_artist(a, d, c) for a in arts]) or empty_a)}'
            f'{r.section("works", f"作品 · {len(works)}", r.grid([r.card_work(w, d, c) for w in works]) or "<p class=empty>此分期尚无作品条目。</p>")}'
            f"</main>")
    return r.shell(f'{p["name"]} · 中国美术史', d, body,
                   [("中国美术史", "index.html"),
                    (c.eras.get(p.get("era"), {}).get("name", "—"), "timeline.html"),
                    (p["name"], None)], "t-period")


PORTAL_STAT = {"works": "作品", "artists": "艺术家", "sites": "遗址",
               "classes": "类目", "treatises": "画论", "events": "事件"}

PORTALS = [("works", "作品", "书画、青铜、陶瓷、玉器、石刻、壁画、塑像"),
           ("artists", "艺术家", "画家、书家、篆刻家、雕塑家"),
           ("sites", "遗址", "石窟、墓葬、窑址、聚落——可嵌套至单窟"),
           ("classes", "类目", "器类、窑口、书体、画科、流派"),
           ("treatises", "画论", "既是信源，也是研究对象"),
           ("events", "事件", "有据可考的关键事件")]


def page_index(c):
    d, s = 0, c.stats()
    bands = []
    for era in sorted(c.eras.values(), key=lambda x: x["sort"]):
        ps = [p for p in c.periods_sorted() if p.get("era") == era["id"]]
        cells = "".join(
            f'<a class="pcell" href="periods/{r.e(p["id"])}.html">'
            f'<span class="pc-n">{r.e(p["name"])}</span>'
            f'<span class="pc-s">{r.e(p.get("span",""))}</span>'
            f'<span class="pc-c">'
            f'{len([a for a in c.artists.values() if a.get("period") == p["id"]])} 家 · '
            f'{len([w for w in c.works.values() if w.get("period") == p["id"]])} 件</span></a>'
            for p in ps)
        bands.append(f'<section class="band"><h2>{r.e(era["name"])}'
                     f'<span class="band-lat">{r.e(era.get("name_en",""))}</span></h2>'
                     f'<div class="pgrid">{cells}</div></section>')
    portals = "".join(
        f'<a class="portal" href="catalog/{k}.html"><span class="po-n">{r.e(n)}</span>'
        f'<span class="po-c">{s.get(PORTAL_STAT[k], 0)}</span>'
        f'<span class="po-d">{r.e(desc)}</span></a>' for k, n, desc in PORTALS)
    body = (f'<main class="wrap"><div class="lede">'
            f'<h1>中国美术史<span class="lede-lat">Chinese Art</span></h1>'
            f'<p class="lede-p">先秦至当代的可核查档案。**不止书画**——青铜、陶瓷、玉器、'
            f'石窟、碑刻与画论同为一等实体，因为中国美术史里最重要的部分大多无作者可指。'
            f'每条陈述标明它是本库采纳、待校验、有争议，还是已被推翻的旧说；'
            f'器物与石窟另标断代依据与可靠度，只给年份等于把不同等级的证据抹平。</p>'
            f'<p class="lede-n">'
            + " · ".join(f"{k} {v}" for k, v in s.items())
            + f' &nbsp;|&nbsp; <a href="about.html">编纂方针</a> · '
              f'<a href="timeline.html">年表</a></p></div>'
            f'<div class="portals">{portals}</div>{"".join(bands)}</main>')
    return r.shell("中国美术史 · WIKI", d, body, None, "t-home")


def page_timeline(c):
    d = 0
    rows = []
    for p in c.periods_sorted():
        x1, x2 = pos(p.get("span_start")), pos(p.get("span_end"))
        rows.append(f'<a class="tlrow" href="periods/{r.e(p["id"])}.html" '
                    f'style="--x1:{x1:.2f}%;--x2:{x2:.2f}%">'
                    f'<span class="tl-n">{r.e(p["name"])}</span>'
                    f'<span class="tl-bar"><span class="tl-fill"></span></span>'
                    f'<span class="tl-s">{r.e(p.get("span",""))}</span></a>')
    ticks = "".join(f'<span class="tick" style="left:{pos(y):.2f}%">'
                    f'{"前 " + str(-y) if y < 0 else str(y)}</span>'
                    for y in (-7000, -2000, -221, 220, 907, 1279, 1644, 1911))
    body = (f'<main class="wrap"><div class="lede"><h1>年表</h1>'
            f'<p class="lede-p">横轴刻度<b>非线性</b>：史前压缩，唐宋以后舒展——'
            f'因为条目密度就长在唐宋以后。条形长度不可用于比较时长。'
            f'分期相互重叠是常态：辽金与两宋并行了三百年，五代与北宋在画史上是连续的，'
            f'教科书把它们排成先后，是整理，不是史实。</p></div>'
            f'<div class="tlwrap"><div class="tlticks">{ticks}</div>'
            f'<div class="tlrows">{"".join(rows)}</div></div></main>')
    return r.shell("年表 · 中国美术史", d, body,
                   [("中国美术史", "index.html"), ("年表", None)], "t-timeline")


CATALOGS = {
    "works": ("作品图录", lambda c: sorted(c.works.values(),
                                          key=lambda w: (w.get("year_sort") or 0, w["id"])),
              r.card_work, "grid"),
    "artists": ("艺术家图录", lambda c: sorted(c.artists.values(),
                                             key=lambda a: (a.get("birth") or 0, a["id"])),
                r.card_artist, "grid"),
    "sites": ("遗址图录", lambda c: sorted((s for s in c.sites.values() if not s.get("parent")),
                                          key=lambda s: s.get("sort", 0)),
              r.card_site, "grid"),
    "classes": ("类目图录", lambda c: sorted(c.classes.values(),
                                           key=lambda x: (x.get("facet", ""), x["id"])),
                r.card_class, "grid grid-flat"),
    "treatises": ("画论图录", lambda c: sorted(c.treatises.values(),
                                             key=lambda t: (t.get("year_sort") or 0, t["id"])),
                  r.card_treatise, "grid grid-flat"),
    "events": ("事件图录", lambda c: sorted(c.events.values(),
                                          key=lambda x: (x.get("year_sort") or 0, x["id"])),
               r.card_event, "grid grid-flat"),
}


def page_catalog(key, c):
    d = 1
    title, getter, carder, cls = CATALOGS[key]
    items = getter(c)
    nav = " · ".join(f'<a href="{k}.html">{v[0].replace("图录","")}</a>'
                     for k, v in CATALOGS.items())
    body = (f'<main class="wrap"><div class="lede"><h1>{title}'
            f'<span class="lede-lat">{len(items)}</span></h1>'
            f'<p class="cat-nav">{nav}</p></div>'
            f'{r.grid([carder(x, d, c) for x in items], cls) or "<p class=empty>尚无条目。</p>"}'
            f"</main>")
    return r.shell(f"{title} · 中国美术史", d, body,
                   [("中国美术史", "index.html"), (title, None)], "t-catalog")


POLICY = """
<ol class="policy">
<li><b>分期不自创。</b>骨架取自国内美术院校通用教材的通行章节划分，并与英文通史交叉核对；
本库把它细分为十七期，五代与辽金单列——这层细分是本库的操作性划分，不冒充教材原有章节。</li>
<li><b>不止书画。</b>青铜、陶瓷、玉器、石窟、碑刻、画论同为一等实体。中国美术史里最重要的
部分大多无作者可指，若只立艺术家与作品两类实体，这些材料根本装不进来。</li>
<li><b>断代必附依据与可靠度。</b>靠铭文、靠器形序列、靠共出器物断代，可靠性差三个等级。
只填一个年份等于把三种证据抹平。<b>「风格比对」一档不允许判为「确证」</b>——它本来就是最弱的依据。</li>
<li><b>重修层是史实，不是杂质。</b>敦煌大量洞窟经西夏、元、清重绘，今天看到的画面是叠压层。
不写清重修，就是把后代的画当原作讲。</li>
<li><b>拓本谱系必注。</b>碑刻的研究对象常是拓本而非原石，宋拓、明拓、翻刻是不同证据等级。
翻刻的性质与摹本相同。</li>
<li><b>摹本与原作分清。</b>顾恺之无真迹，《女史箴图》为唐摹；王羲之所见皆唐摹与刻帖。
凡此一律写明，不把摹本当原作谈风格——这与希腊雕塑只存罗马摹本是同一类问题。</li>
<li><b>争议并陈，不替读者裁断。</b>每条陈述标明地位：本库采纳／待校验／有争议／旧说已废。
标为旧说已废的，必须能追到出处。</li>
<li><b>不作回溯性诊断、不作单因归结。</b>不给古人安现代精神病名；不把风格突破归因于苦难或伤病。</li>
<li><b>后世命名不当作当时事实。</b>「饕餮纹」出自宋人《宣和博古图》一系，商人如何称呼不明；
「南北宗」是董其昌的理论建构。这类词本库当研究对象，不当分类工具。</li>
<li><b>核查等级必填。</b>每条注明是馆藏官方核实、结构化数据核对，还是未经外部核对。
本库不留「未标」这个逃生口——凭记忆写的与核实过的在文件里长得一样，不标就分不出来。</li>
</ol>"""

IMAGES_NOTE = """
<p>图像策略在 2026-08-04 逐一实测各信源后确定。一个必须正视的不对称：</p>
<ul>
<li><b>可编程核查并取图的</b>：克利夫兰美术馆（中国部多为 CC0，元数据含文化断代）、
大都会博物馆（亚洲部，isPublicDomain 判权）、哈佛艺术博物馆（IIIF）。</li>
<li><b>无公开 API 的</b>：故宫博物院、中国国家博物馆、数字敦煌仅有 HTML 页面；
台北故宫与上海博物馆实测 SSL 握手失败。</li>
</ul>
<p><b>而最重要的中国文物恰在北京与台北。</b>后母戊鼎、《清明上河图》、汝窑天青釉洗，
既无 API 可核，也无自由授权图像；反倒是流散在西方馆的中国文物数据齐备。</p>
<p>所以两类分别对待：西方馆藏可标核查等级为「馆藏官方核实」并取图；国内馆藏记下官方藏品页
URL 供人工核对，核查等级最高只能标「结构化数据核对」，缺图按状态说明原因。
<b>这个不对称是图像权属的现实，不是编纂偷懒。</b></p>
<p>许可一律取自源头 API 自报字段，不做目测。三维物件尤其要紧——器物早已进入公版，
但拍它的那张照片是新的受版权作品，故常为 CC BY-SA 而非 PD，本库不把它压成 PD。</p>"""


def page_about(c):
    d = 0
    tiers = c.sources.get("tiers", {})
    tl = "".join(f'<tr><td class="tier t{r.e(k)}">{r.e(k)}</td><td>{r.e(v)}</td></tr>'
                 for k, v in sorted(tiers.items()))
    items = sorted(c.sources.get("items", []), key=lambda s: (s.get("tier", 9), s.get("id")))
    body = (f'<main class="wrap wrap-text"><div class="lede"><h1>编纂方针</h1>'
            f'<p class="lede-p">这些不是文风偏好，是内容判断的准绳。</p></div>'
            f'{r.section("policy", "十条", POLICY)}'
            f'{r.section("images", "图像来源与一个不对称", IMAGES_NOTE)}'
            f'{r.section("tiers", "信源分级", f"<table class=tbl>{tl}</table>")}'
            f'{r.section("biblio", f"参考书目与信源 · {len(items)}", "<div class=biblio>" + "".join(_src_entry(s) for s in items) + "</div>")}'
            f"</main>")
    return r.shell("编纂方针 · 中国美术史", d, body,
                   [("中国美术史", "index.html"), ("编纂方针", None)], "t-about")


def _src_entry(s):
    meta = " · ".join(x for x in [s.get("edition"), str(s.get("year") or ""),
                                  s.get("publisher"), s.get("isbn")] if x)
    bits = ""
    for k, cls, pre in (("free", "src-free", "公开可查："), ("url", "src-url", ""),
                        ("note", "src-note", "")):
        if s.get(k):
            v = (f'<a href="{r.e(s[k])}" target="_blank" rel="noopener">{r.e(s[k])}</a>'
                 if k == "url" else r.e(s[k]))
            bits += f'<p class="{cls}">{pre}{v}</p>'
    spine = r.badge("ok", "分期依据") if s.get("spine") else ""
    unver = r.badge("warn", "书目未核") if s.get("verified") is False else ""
    return (f'<div class="src" id="src-{r.e(s["id"])}">'
            f'<p class="src-h"><span class="tier t{s.get("tier",9)}">{s.get("tier","?")}</span>'
            f'<b>{r.e(s["title"])}</b>'
            f'{f"<span class=src-zh>{r.e(s['title_zh'])}</span>" if s.get("title_zh") else ""}'
            f'{spine}{unver}</p>'
            f'{f"<p class=src-a>{r.e(s['authors'])}</p>" if s.get("authors") else ""}'
            f'{f"<p class=src-m>{r.e(meta)}</p>" if meta else ""}'
            f'<p class="src-w">{r.e(s.get("authority",""))}</p>{bits}</div>')


def search_index(c):
    out = []
    for kind, eid, o in c.all_entities():
        name = o.get("name") or o.get("title") or eid
        alt = "、".join(o.get("name_alt") or []) or o.get("title_orig") or o.get("author") or ""
        ctx = (o.get("kind") or o.get("facet") or o.get("cat")
               or (c.periods.get(o.get("period")) or {}).get("name") or "")
        out.append({"k": kind, "i": eid, "n": name, "l": alt, "p": ctx,
                    "d": o.get("one_line") or o.get("holder") or ""})
    for p in c.periods.values():
        out.append({"k": "period", "i": p["id"], "n": p["name"], "l": p.get("name_en", ""),
                    "p": c.eras.get(p.get("era"), {}).get("name", ""),
                    "d": p.get("one_line", "")})
    return out


def check_output():
    """产物级回归：数据校验保证条目之间引用得通，这里保证生成出来的 href/src 真的落地。
    模板改错（路径深度算错、少个 ../）只会在这一层暴露。"""
    import re as _re
    links, broken = 0, []
    for f in DIST.rglob("*.html"):
        html = f.read_text(encoding="utf-8")
        for m in _re.findall(r'(?:href|src)="([^"#][^"]*)"', html):
            if m.startswith(("http", "mailto:", "data:")):
                continue
            links += 1
            if not (f.parent / m.split("#")[0]).resolve().exists():
                broken.append((str(f.relative_to(DIST)), m))
    return links, broken


def build():
    c = Corpus()
    problems = validate(c)
    errors = [p for p in problems if p.level == "ERROR"]
    for p in problems:
        print(p)
    if errors:
        print(f"\n停止生成：{len(errors)} 个 ERROR。先修数据，不产半成品站。")
        return 1

    clean()
    (DIST / "assets" / "img").mkdir(parents=True, exist_ok=True)
    for f in STATIC.glob("*.*"):
        shutil.copy2(f, DIST / "assets" / f.name)
    if (STATIC / "img").exists():
        for f in (STATIC / "img").glob("*.*"):
            shutil.copy2(f, DIST / "assets" / "img" / f.name)

    n = 3
    _write("index.html", page_index(c))
    _write("timeline.html", page_timeline(c))
    _write("about.html", page_about(c))
    for p in c.periods.values():
        _write(f'periods/{p["id"]}.html', page_period(p, c)); n += 1
    for a in c.artists.values():
        _write(f'artists/{a["id"]}.html', page_artist(a, c)); n += 1
    for w in c.works.values():
        _write(f'works/{w["id"]}.html', page_work(w, c)); n += 1
    for s in c.sites.values():
        _write(f'sites/{s["id"]}.html', page_site(s, c)); n += 1
    for cl in c.classes.values():
        _write(f'classes/{cl["id"]}.html', page_class(cl, c)); n += 1
    for t in c.treatises.values():
        _write(f'treatises/{t["id"]}.html', page_treatise(t, c)); n += 1
    for ev in c.events.values():
        _write(f'events/{ev["id"]}.html', page_event(ev, c)); n += 1
    for k in CATALOGS:
        _write(f"catalog/{k}.html", page_catalog(k, c)); n += 1
    _write("search.json", json.dumps(search_index(c), ensure_ascii=False, separators=(",", ":")))

    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    links, broken = check_output()
    print(f"\n生成 {n} 页 · 产出 {total/1024:.0f} KB · 内部链接 {links} · "
          f"WARN {len(problems) - len(errors)}")
    for b in broken[:12]:
        print(f"  断链 {b[0]} → {b[1]}")
    if broken:
        print(f"\n{len(broken)} 条断链，来自模板，需改 render/build。")
        return 1
    print("预览：cd dist && python -m http.server 8733")
    return 0


if __name__ == "__main__":
    sys.exit(build())

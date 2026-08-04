"""HTML 组件层。data → 片段字符串。

配色沿用既有单文件库的宣纸＋朱印——那套色是对的，且它是这部库的既有身份，
不该因为换了架构就换掉（creation.md 所谓稳定核心）。变的是结构：六类实体、
图像一等、遗址自嵌套。

四种中国材料专设的块，视觉都从材料本身来，不是随手配色：

  dating   断代依据  依据与可靠度并列，可靠度四档分色；「风格比对」一档
                     刻意做得最弱，因为它本来就是最弱的证据
  relayer  重修层    横向叠压的层带，读起来像地层剖面——因为它就是
  rubbing  拓本      **黑底白字**。拓本本身就是白字黑底，让它在页面上
                     保持原样，比配个浅色框更诚实
  colophon 题跋鉴藏  缩进另起，印记用朱色方框标出，仿卷后题跋的排布
"""

import html
import re

from schema import (BADGES, BLOCKS, DATING_BASIS, DATING_CONF, LINK_RE,
                    RUBBING_EDITIONS, STMT_STATES, license_ok, LICENSE_BLOCKED)

KIND_PATH = {"artist": "artists", "work": "works", "site": "sites",
             "class": "classes", "treatise": "treatises", "event": "events",
             "period": "periods"}
KIND_LABEL = {"artist": "艺术家", "work": "作品", "site": "遗址",
              "class": "类目", "treatise": "画论", "event": "事件", "period": "分期"}


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def up(depth):
    return "../" * depth


# ── 行内 ──────────────────────────────────────────────────────────────────


def inline(text, depth, corpus=None):
    out, pos = [], 0
    for m in LINK_RE.finditer(str(text)):
        out.append(_emph(e(text[pos:m.start()])))
        out.append(_ref(m.group(1), m.group(2), m.group(3), depth, corpus))
        pos = m.end()
    out.append(_emph(e(text[pos:])))
    return "".join(out)


def _emph(s):
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)


def _ref(kind, eid, label, depth, corpus):
    if kind == "src":
        return f'<a class="ref-src" href="{up(depth)}about.html#src-{e(eid)}">{e(label or eid)}</a>'
    name = label
    if corpus and not name:
        obj = corpus.periods.get(eid) if kind == "period" else corpus.entity(kind, eid)
        name = (obj or {}).get("name") or (obj or {}).get("title") or eid
    return (f'<a class="ref ref-{kind}" href="{up(depth)}{KIND_PATH[kind]}/{e(eid)}.html">'
            f"{e(name or eid)}</a>")


def badge(kind, text):
    k = kind if kind in BADGES else "neu"
    return f'<span class="bdg b-{k}">{e(text)}</span>'


def chip(text, cls=""):
    return f'<span class="chip {cls}">{e(text)}</span>'


# ── 块 ────────────────────────────────────────────────────────────────────


def blocks(bs, depth, corpus=None):
    return "\n".join(_block(b, depth, corpus) for b in bs or [])


def _block(b, depth, c):
    t = b.get("t")
    if t == "p":
        return f'<p>{inline(b["text"], depth, c)}</p>'
    if t == "ul":
        return "<ul>" + "".join(f"<li>{inline(i, depth, c)}</li>" for i in b["items"]) + "</ul>"
    if t == "kv":
        rows = "".join(f'<tr><th>{inline(k, depth, c)}</th><td>{inline(v, depth, c)}</td></tr>'
                       for k, v in b["rows"])
        return f'<table class="kv"><tbody>{rows}</tbody></table>'
    if t == "stmt":
        return _stmt(b, depth, c)
    if t == "gap":
        return f'<p class="gap">· {e(b.get("text") or "暂无可靠资料")}</p>'
    if t == "quote":
        by = f'<cite>—— {inline(b["by"], depth, c)}</cite>' if b.get("by") else ""
        src = f'<span class="q-src">{inline(b["src"], depth, c)}</span>' if b.get("src") else ""
        return f'<blockquote>{inline(b["text"], depth, c)}{by}{src}</blockquote>'
    if t == "fig":
        return figure(b["image"], depth, b.get("caption"), c)
    if t == "tl":
        rows = "".join(f'<li><span class="tl-when">{e(r.get("when", ""))}</span>'
                       f'<span class="tl-what">{inline(r.get("text", ""), depth, c)}</span></li>'
                       for r in b["rows"])
        return f'<ol class="tl">{rows}</ol>'
    if t == "dating":
        return _dating(b, depth, c)
    if t == "relayer":
        return _relayer(b, depth, c)
    if t == "rubbing":
        return _rubbing(b, depth, c)
    if t == "colophon":
        return _colophon(b, depth, c)
    if t == "prov":
        return _prov(b, depth, c)
    if t == "exhib":
        return _exhib(b, depth, c)
    # 契约里声明了、这里却没有渲染器的块，从前会静默返回空字符串——
    # schema 放行、build 报 0 WARN、内容凭空消失，哪里都不报错。
    # 那是最坏的一种失败，所以宁可让构建当场炸掉。
    if t in BLOCKS:
        raise ValueError(f"块类型 {t!r} 已在 schema.BLOCKS 中声明，但 render.py 无渲染器——"
                         f"加块类型必须同时加渲染器，否则数据会被静默丢弃")
    return ""


def _stmt(b, depth, c):
    state = b.get("state", "pend")
    src = (f' <a class="ref-src" href="{up(depth)}about.html#src-{e(b["src"])}">'
           f'[{e(b["src"])}]</a>') if b.get("src") else ""
    return (f'<div class="stmt s-{e(state)}">'
            f'<span class="stmt-tag">{e(STMT_STATES.get(state, state))}</span>'
            f'<div class="stmt-body">{inline(b["text"], depth, c)}{src}</div></div>')


def _dating(b, depth, c):
    """断代依据。依据与可靠度并列显示——只写年份等于把不同等级的证据抹平。"""
    basis, conf = b.get("basis", ""), b.get("conf", "")
    src = (f' <a class="ref-src" href="{up(depth)}about.html#src-{e(b["src"])}">'
           f'[{e(b["src"])}]</a>') if b.get("src") else ""
    year = f'<span class="dt-year">{e(b["year"])}</span>' if b.get("year") else ""
    return (f'<div class="dating c-{_conf_cls(conf)}">'
            f'<div class="dt-head"><span class="dt-label">断代依据</span>'
            f'<span class="dt-basis">{e(basis)}</span>'
            f'<span class="dt-conf">{e(conf)}</span>{year}</div>'
            f'<div class="dt-body">{inline(b["text"], depth, c)}{src}</div>'
            f'<div class="dt-note">{e(DATING_BASIS.get(basis, ""))}'
            f'{" · " + e(DATING_CONF.get(conf, "")) if conf in DATING_CONF else ""}</div></div>')


def _conf_cls(conf):
    return {"确证": "sure", "较可靠": "ok", "存疑": "doubt", "不可考": "none"}.get(conf, "none")


def _relayer(b, depth, c):
    """重修层。做成横向叠压的层带，读起来像地层剖面——因为它就是。"""
    ev = f'<span class="rl-ev">依据：{inline(b["evidence"], depth, c)}</span>' if b.get("evidence") else ""
    return (f'<div class="relayer"><span class="rl-when">{e(b["when"])}</span>'
            f'<span class="rl-what">{inline(b["what"], depth, c)}{ev}</span></div>')


def _rubbing(b, depth, c):
    """拓本。黑底白字——拓本本身就是白字黑底，让它在页面上保持原样。"""
    ed = b.get("edition", "")
    holder = f'<span class="rb-holder">{e(b["holder"])}</span>' if b.get("holder") else ""
    return (f'<div class="rubbing"><div class="rb-head">'
            f'<span class="rb-ed">{e(ed)}</span>{holder}</div>'
            f'<div class="rb-body">{inline(b["text"], depth, c)}</div>'
            f'<div class="rb-note">{e(RUBBING_EDITIONS.get(ed, ""))}</div></div>')


def _colophon(b, depth, c):
    """题跋与鉴藏印。印记用朱色方框标出，仿卷后题跋的排布。"""
    when = f'<span class="cl-when">{e(b["when"])}</span>' if b.get("when") else ""
    seals = "".join(f'<span class="seal">{e(s)}</span>'
                    for s in (b.get("seals") or ([b["seal"]] if b.get("seal") else [])))
    return (f'<div class="colophon"><div class="cl-head">'
            f'<span class="cl-by">{e(b["by"])}</span>{when}{seals}</div>'
            f'<div class="cl-body">{inline(b["text"], depth, c)}</div></div>')


def _archive_src(b, depth):
    """馆方档案块的出处标记：既指回本库的信源页，也直链那一件的藏品页。

    只写 `[cleveland]` 等于要读者自己去馆里搜一遍。**留下原始链接是署名义务的
    一部分**（CC BY-SA 要求署名，「适当引用」要求指明出处），也是这条记录能被
    第三方复核的前提——本库全部认知等级的意义都建立在「可复核」上。
    """
    out = ""
    if b.get("src"):
        out += (f'<a class="ref-src" href="{up(depth)}about.html#src-{e(b["src"])}">'
                f'[{e(b["src"])}]</a>')
    if b.get("url"):
        out += (f'<a class="ref-src" href="{e(b["url"])}" target="_blank" '
                f'rel="noopener">藏品页 ↗</a>')
    return out


def _prov(b, depth, c):
    """递藏。做成竖向链条，因为它就是一条链——断在哪里比经过谁更要紧。

    与 colophon 刻意做得不像：题跋是**刻在物件上**的证据（朱印、宣纸底），
    递藏是**物件之外**的机构档案。两者证据性质不同，看起来就不该一样。
    """
    return (f'<div class="prov"><span class="pv-when">{e(b["when"])}</span>'
            f'<span class="pv-body">{inline(b["text"], depth, c)}'
            f'{_archive_src(b, depth)}</span></div>')


def _exhib(b, depth, c):
    """展览史。年份立在左侧成一列，因为要看的正是时间分布——
    一件东西哪几十年被反复展出、哪几十年没人碰，本身就是接受史。"""
    note = (f'<span class="ex-note">{inline(b["text"], depth, c)}</span>'
            if b.get("text") else "")
    return (f'<div class="exhib"><span class="ex-when">{e(b["when"] or "年份不详")}</span>'
            f'<span class="ex-body"><span class="ex-title">{inline(b["title"], depth, c)}</span>'
            f'{note}{_archive_src(b, depth)}</span></div>')


# ── 图像 ──────────────────────────────────────────────────────────────────


def displayable(im):
    return isinstance(im, dict) and im.get("license") not in LICENSE_BLOCKED


def thumb_src(im, depth):
    t = str(im.get("thumb", ""))
    return f"{up(depth)}assets/{t}" if t.startswith("img/") else t


def img_tag(im, depth, cls="", sizes="(min-width:1100px) 46vw, 92vw", ratio=None):
    """w/h 靠 aspect-ratio 预留位置，图未到时版面已定，不跳动。
    许可未判定的图不渲染，只留说明位——宁可缺图，不可越权。"""
    w, h = im.get("w") or 3, im.get("h") or 4
    ar = ratio or (f"{w}/{h}" if w and h else "3/4")
    if not displayable(im):
        return (f'<span class="ph ph-none ph-held {cls}" style="aspect-ratio:{ar}">'
                f"<span>许可待复核<br>暂不展示</span></span>")
    return (f'<span class="ph {cls}" style="aspect-ratio:{ar}">'
            f'<img src="{e(thumb_src(im, depth))}" width="{w or 800}" height="{h or 1000}" '
            f'loading="lazy" decoding="async" sizes="{e(sizes)}" '
            f'alt="{e(im.get("alt") or "")}"'
            f'{f" data-zoom=&#34;{e(zoom_url(im))}&#34;" if zoom_url(im) else ""} '
            f'onerror="this.closest(&#39;.ph&#39;).classList.add(&#39;ph-fail&#39;)"></span>')


ZOOM_W = 1600


def zoom_url(im):
    """放大用的那一张，一律要 1600 档，不挂原始文件。
    Commons 上公版名作常是吉像素扫描，直接给 <img> 会拖死浏览器；
    克利夫兰的 full 是 TIF，浏览器不认，故对该源改用 web 图。"""
    iiif = str(im.get("iiif") or "")
    if iiif.endswith("/info.json"):
        return f"{iiif[:-len('/info.json')]}/full/{ZOOM_W},/0/default.jpg"
    src = str(im.get("source_url") or "")
    if im.get("source") == "wikimedia" and "/wiki/File:" in src:
        name = src.split("/wiki/File:", 1)[1]
        return ("https://commons.wikimedia.org/w/index.php?title=Special:Redirect/file/"
                f"{name}&width={ZOOM_W}")
    full = str(im.get("full") or "")
    if full.lower().endswith((".tif", ".tiff")):
        return ""                     # TIF 浏览器不认，不挂放大
    return full


def _credit(im):
    """署名区。**图示级标记放在这里，不放在各调用点**——

    作品页的粘性图版、条目里的插图、卡片各走不同渲染路径，逐个补标记必漏一处，
    而漏掉的那处恰恰会让读者以为看过了原作。放在署名这个咽喉点，
    凡显示出处的地方就一定带着限定。同一条教训刚在块渲染上吃过一次：
    契约里声明而渲染层没接的块，会被静默丢掉且哪里都不报错。
    """
    src = (f'<a class="cr-src" href="{e(im["source_url"])}" target="_blank" rel="noopener">'
           f'{e(im.get("credit", ""))}</a>') if im.get("source_url") else \
        f'<span class="cr-src">{e(im.get("credit", ""))}</span>'
    return (f'{src}<span class="cr-lic">{e(im.get("license", ""))}</span>'
            f'{_plate_grade(im)}')


def _img_links(im):
    out = []
    if im.get("iiif"):
        out.append(f'<a href="{e(im["iiif"])}" target="_blank" rel="noopener">深缩放</a>')
    if im.get("full") and not str(im["full"]).lower().endswith((".tif", ".tiff")):
        out.append(f'<a href="{e(im["full"])}" target="_blank" rel="noopener">高清原图</a>')
    if im.get("source_url"):
        out.append(f'<a href="{e(im["source_url"])}" target="_blank" rel="noopener">藏品页</a>')
    return f'<span class="cr-links">{" · ".join(out)}</span>' if out else ""


PLATE_MIN_W = 1000


def _plate_grade(im):
    """分辨率不足时自动打「图示级」标记。

    **自动，而非靠撰写者记得**——忘记加标记的那一次，正是最需要它的那一次。
    本库的图是图示（这是哪一件）而非图版（这一笔怎么走的）：600px 认得出是
    《浮玉山居图》，据此谈钱选的用笔却是空话。两件事的界限必须让读者看见，
    否则一张小图会被当成看过了原作。
    """
    w = im.get("w") or 0
    if not w or w >= PLATE_MIN_W:
        return ""
    return (f'<span class="cr-grade" title="本库的图用于辨认作品，不用于论笔墨">'
            f'图示级 {w}px</span>')


def figure(im, depth, caption=None, corpus=None):
    cap = f'<div class="fig-cap">{inline(caption, depth, corpus)}</div>' if caption else ""
    return (f'<figure class="fig">{img_tag(im, depth)}{cap}'
            f'<figcaption class="credit">{_credit(im)}{_img_links(im)}</figcaption></figure>')


# ── 卡片 ──────────────────────────────────────────────────────────────────


def _card(cls, href, im, depth, title, sub, meta, desc, ratio="3/4"):
    fig = img_tag(im, depth, "ph-card", "(min-width:900px) 22vw, 45vw", ratio) if im else \
        f'<span class="ph ph-none" style="aspect-ratio:{ratio}"></span>'
    return (f'<a class="card {cls}" href="{href}">{fig}'
            f'<span class="card-t">{e(title)}</span>'
            f'{f"<span class=card-sub>{e(sub)}</span>" if sub else ""}'
            f'<span class="card-m">{e(meta)}</span>'
            f'<span class="card-d">{e(desc)}</span></a>')


def card_artist(a, depth, c=None):
    """艺术家卡片：**文字优先，图是可选项且放在名字下方。**

    两个理由，都不是版面偏好：

    一、**130 位艺术家里 0 位有肖像。**中国美术史的画家绝大多数没有可靠的传世画像，
       这是史料状况而非欠账。而原先无图时会渲染一个 3:4 的空灰框——
       130 张卡片里 96 张是空框，**把「本无其人之像」显示成「图还没配上」**。

    二、有图的 34 张，图其实借自该家名下某件作品。**图在名字上方，读起来就成了
       「这就是范宽」**；移到名字下方并标出是哪一件，才不至于把作品当人像。
    """
    im = a.get("portrait")
    from_work = None
    if not im and c:
        from_work = next((w for w in c.works_of(a["id"]) if w.get("image")), None)
        if from_work:
            im = from_work.get("image")

    fig = ""
    if im:
        cap = (f'<span class="ca-cap">{e(from_work.get("title", ""))}</span>'
               if from_work else '<span class="ca-cap">传世画像</span>')
        fig = (f'<span class="ca-fig">'
               f'{img_tag(im, depth, "ph-card ph-mini", "(min-width:900px) 12vw, 26vw", "3/2")}'
               f'{cap}</span>')

    alt = a.get("name_alt_str") or ""
    sub = f'<span class="card-sub">{e(alt)}</span>' if alt else ""
    cls = "card c-artist" + ("" if im else " c-artist-txt")
    return (f'<a class="{cls}" href="{up(depth)}artists/{e(a["id"])}.html">'
            f'<span class="card-t">{e(a.get("name", ""))}</span>{sub}'
            f'<span class="card-m">{e(_years(a))}</span>'
            f'<span class="card-d">{e(a.get("one_line", ""))}</span>'
            f'{fig}</a>')


def card_work(w, depth, c=None):
    who = ""
    if c and w.get("artist"):
        who = (c.artists.get(w["artist"]) or {}).get("name", "")
    elif w.get("site") and c:
        who = (c.sites.get(w["site"]) or {}).get("name", "")
    meta = " · ".join(x for x in (w.get("kind"), w.get("year") or "", who) if x)
    return _card("c-work", f'{up(depth)}works/{e(w["id"])}.html', w.get("image"), depth,
                 w.get("title", ""), w.get("title_orig") or "", meta, w.get("holder", ""))


def card_site(s, depth, c=None):
    return _card("c-site", f'{up(depth)}sites/{e(s["id"])}.html', s.get("image"), depth,
                 s.get("name", ""), s.get("name_en") or "",
                 s.get("span", ""), s.get("one_line", ""), "4/3")


def card_flat(cls, href, title, meta, desc):
    return (f'<a class="card card-flat {cls}" href="{href}">'
            f'<span class="card-m">{e(meta)}</span>'
            f'<span class="card-t">{e(title)}</span>'
            f'<span class="card-d">{e(desc)}</span></a>')


def card_class(cl, depth, c=None):
    return card_flat("c-class", f'{up(depth)}classes/{e(cl["id"])}.html',
                     cl.get("name", ""), cl.get("facet", ""), cl.get("one_line", ""))


def card_treatise(t, depth, c=None):
    return card_flat("c-treatise", f'{up(depth)}treatises/{e(t["id"])}.html',
                     t.get("title", ""), t.get("author", ""), t.get("one_line", ""))


def card_event(ev, depth, c=None):
    who = (c.artists.get(ev.get("artist")) or {}).get("name", "") if c and ev.get("artist") else ""
    return card_flat("c-event", f'{up(depth)}events/{e(ev["id"])}.html',
                     ev.get("title", ""), f'{ev.get("when","")} {who}'.strip(),
                     ev.get("one_line", ""))


def _years(a):
    b, d = a.get("birth"), a.get("death")
    f = (lambda y: f"前 {-y}" if y and y < 0 else str(y) if y else "?")
    return f"{f(b)} – {f(d)}" if (b or d) else (a.get("active") or "")


def grid(cards, cls="grid"):
    return f'<div class="{cls}">{"".join(cards)}</div>' if cards else ""


def section(sid, label, body, note=""):
    n = f'<span class="sec-note">{e(note)}</span>' if note else ""
    return (f'<section class="sec" id="s-{e(sid)}"><h2 class="sec-h">'
            f'<span class="sec-n">{e(label)}</span>{n}</h2>'
            f'<div class="sec-b">{body}</div></section>')


# ── 外壳 ──────────────────────────────────────────────────────────────────

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Noto+Serif+SC:wght@400;500;600;700&family=Noto+Sans+SC:wght@400;500&'
         'family=IBM+Plex+Mono:wght@400;500&display=swap">')


def crumb(items, depth):
    parts = [f'<a href="{up(depth)}{e(h)}">{e(l)}</a>' if h
             else f'<span aria-current="page">{e(l)}</span>' for l, h in items]
    return f'<nav class="crumb">{"<i>›</i>".join(parts)}</nav>'


def shell(title, depth, body, crumbs=None, cls="", head=""):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="{up(depth)}assets/site.css">
{FONTS}
{head}
</head>
<body class="{e(cls)}">
<header class="topbar"><div class="tb-in">
  <a class="brand" href="{up(depth)}index.html">
    <span class="sealmini" aria-hidden="true"></span>
    <span class="brand-t">中国美术史</span><span class="brand-s">WIKI</span></a>
  {crumb(crumbs, depth) if crumbs else ""}
  <div class="tb-r">
    <a href="{up(depth)}catalog/works.html">图录</a>
    <a href="{up(depth)}catalog/sites.html">遗址</a>
    <a href="{up(depth)}timeline.html">年表</a>
    <a href="{up(depth)}about.html">方针</a>
    <button class="q-open" id="qOpen" aria-label="检索">检索</button>
  </div>
</div></header>
<div class="qbox" id="qBox" hidden>
  <input id="qIn" type="search" placeholder="检索艺术家 / 作品 / 遗址 / 类目 / 画论…" autocomplete="off">
  <div class="q-res" id="qRes"></div>
</div>
{body}
<footer class="foot"><div class="foot-in">
  <p>中国美术史 · 数据源为 <code>data/*.json</code>，本页由 <code>build.py</code> 生成，不手改。</p>
  <p>图像版权归各馆所有，署名与许可标于每图之下。分期依据、断代口径与信源分级见<a href="{up(depth)}about.html">编纂方针</a>。</p>
</div></footer>
<script src="{up(depth)}assets/site.js" data-up="{up(depth)}"></script>
</body>
</html>
"""

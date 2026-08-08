# -*- coding: utf-8 -*-
"""条目插入器 —— 往 index.html 里加画家 / 作品 / 事件，并自动重算全部计数。

原始的 build_site_120.py 等生成脚本已丢失，且内容早已在单文件里手工演进，
反向抽成数据会有损。故不重建整站生成器，只提供"安全地往里加一条"的能力：

  python tools/entry.py artist  --id liusongnian --name 刘松年 --pinyin "Liú Sōngnián" \
      --cat 绘画 --small 绘 --sub "南宋" --one-line "南宋四家,暗门刘" \
      --after artist-litang --fragment frag.html
  python tools/entry.py work    --id liusongnian-01 --title 四景山水图 --artist liusongnian \
      --tag "画作 南宋" --meta "绢本设色·故宫博物院" --desc "四季组画典范" --fragment frag.html
  python tools/entry.py event   --id lsn-jinshi --title 进耕织图赐金带 --artist liusongnian \
      --tag "事件 宁宗朝" --meta "宁宗朝 · 赐金带" --desc "其生平唯一的宫廷高光" \
      --fragment frag.html
  python tools/entry.py resync                      # 只重算计数，不加内容

  --after 指插在哪位画家之后（如 artist-litang），决定其在首页与侧栏中的位置。
  --small 是侧栏索引里的单字：绘 / 书 / 篆 / 雕。
  --chip 可选，卡片上显示的类别文字，默认同 --cat。

计数（首页图录 / 筛选器 / 朝代分组 / 侧栏 / title / 面包屑 / 页脚）一律由
实际卡片重新推导后回写，不做数字的字符串替换——避免手工同步时漏改。
改完自己跑一遍 validate.py，不通过就退出非零。
"""
import argparse
import io
import os
import re
import subprocess
import time
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")

CN_DIGITS = "〇一二三四五六七八九"


def cn_num(n):
    """121 -> 一百二十一（本库只会用到 1–999）。"""
    if n < 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return CN_DIGITS[n // 10] + "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    out = CN_DIGITS[n // 100] + "百"
    rest = n % 100
    if rest == 0:
        return out
    if rest < 10:
        return out + "〇" + CN_DIGITS[rest]
    return out + CN_DIGITS[rest // 10] + "十" + (CN_DIGITS[rest % 10] if rest % 10 else "")


def load():
    return io.open(INDEX, encoding="utf-8").read()


def save(h):
    """先写临时文件再原子替换。

    直接以 "w" 打开会立即截断 index.html——写入中途失败就只剩 git 能救；
    且本地起 http server 预览时，Windows 会锁住刚被请求过的文件，
    直接写会 EINVAL 失败。os.replace 两个问题一起解决。
    """
    tmp = INDEX + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(h)
    for attempt in range(5):  # 预览服务器可能正占着句柄，短暂重试
        try:
            os.replace(tmp, INDEX)
            return
        except PermissionError:
            if attempt == 4:
                os.remove(tmp)
                sys.exit("index.html 被占用（本地预览服务器？），请关掉后重试。原文件未改动。")
            time.sleep(0.4)


def sub_once(h, old, new, why):
    n = h.count(old)
    if n != 1:
        sys.exit("FAIL [%s] 命中 %d 次（须为 1）: %r" % (why, n, old[:70]))
    return h.replace(old, new, 1)


def view_span(h, vid):
    """返回某个 view 在原文中的 (start, end)。"""
    marks = [(m.start(), m.group(2))
             for m in re.finditer(r'<div class="view([^"]*)" id="([^"]+)"', h)]
    for i, (s, v) in enumerate(marks):
        if v == vid:
            return s, (marks[i + 1][0] if i + 1 < len(marks) else len(h))
    sys.exit("找不到条目 %s" % vid)


# ---------------------------------------------------------------- 计数重算

def _cards(h):
    return re.findall(r'<a class="acard" data-go="([^"]+)" data-cat="([^"]+)"', h)


def _resync_totals(h, total, by_cat, changes):
    """title / 面包屑 / 侧栏首页链接 / 图录三处 / 筛选器 / 页脚。"""
    def rx(pattern, repl, why):
        new, n = re.subn(pattern, repl, h, count=1)
        if n != 1:
            sys.exit("FAIL [计数·%s] 匹配 %d 次" % (why, n))
        if new != h:
            changes.append(why)
        return new

    h = rx(r"(<title>中国艺术史知识库 · )\d+( 家)", r"\g<1>%d\g<2>" % total, "title")
    h = rx(r"(首页 · <b>中国艺术史 )\d+( 家</b>)", r"\g<1>%d\g<2>" % total, "面包屑")
    h = rx(r'(data-go="home">← 全部 )\d+( · 首页</a>)', r"\g<1>%d\g<2>" % total, "侧栏首页链接")
    h = rx(r"(A PICTORIAL RECORD OF )\d+( CHINESE MASTERS)", r"\g<1>%d\g<2>" % total, "图录英文题")
    h = rx(r'(<div class="tulu-sub">)[^<]+(</div>)',
           r"\g<1>%s\g<2>" % " ".join(cn_num(total) + "家圖録"), "图录副标题")
    h = rx(r"(<b>)[一二三四五六七八九十百〇廿]+家(</b>)",
           r"\g<1>%s家\g<2>" % cn_num(total), "图录总计")
    h = rx(r"\d+( 位艺术家全维度档案)", r"%d\g<1>" % total, "页脚")
    h = rx(r'(data-filter="all">全部<b>)\d+(</b>)', r"\g<1>%d\g<2>" % total, "筛选器·全部")
    for cat, cnt in sorted(by_cat.items()):
        h = rx(r'(data-filter="%s">[^<]*<b>)\d+(</b>)' % re.escape(cat),
               r"\g<1>%d\g<2>" % cnt, "筛选器·" + cat)
    return h


def _resync_meta_and_eras(h, by_cat, changes):
    """首页图录分类计数 + 朝代分组计数（首页 era-h 与侧栏 grp-era）。"""
    meta = re.search(r'(<div class="tulu-meta">)(.*?)(</div>)', h, re.S)
    if not meta:
        sys.exit("FAIL 找不到 tulu-meta")
    body = meta.group(2)
    # 图录里"雕塑"对应卡片上的 data-cat="雕刻"
    alias = {"雕塑": "雕刻"}
    for label, _ in re.findall(r"([一-龥]+) <b>(\d+)</b>", body):
        key = alias.get(label, label)
        if key in by_cat:
            body = re.sub(r"(%s <b>)\d+(</b>)" % label, r"\g<1>%d\g<2>" % by_cat[key], body, count=1)
    if body != meta.group(2):
        changes.append("图录分类计数")
    h = h[:meta.start(2)] + body + h[meta.end(2):]

    # 首页：每个 era-h 后面那一段 agrid 里的卡片数
    def fix_era(m):
        seg = h[m.end():]
        grid = seg[:seg.find('<div class="era-h"')] if '<div class="era-h"' in seg else seg
        n = len(re.findall(r'<a class="acard"', grid))
        return '<div class="era-h">%s<span class="ct">%d</span></div>' % (m.group(1), n)

    h2 = re.sub(r'<div class="era-h">([^<]+)<span class="ct">\d+</span></div>', fix_era, h)
    if h2 != h:
        changes.append("首页朝代计数")
    h = h2

    # 侧栏：每个 grp-era 后面那一段里的 ixx 数
    def fix_grp(m):
        seg = h[m.end():]
        cut = seg.find('<p class="grp-era">')
        block = seg[:cut] if cut >= 0 else seg[:seg.find("</aside>")]
        n = len(re.findall(r'<a class="ixx"', block))
        return '<p class="grp-era">%s · %d</p>' % (m.group(1).strip(), n)

    h2 = re.sub(r'<p class="grp-era">([^·<]+)·\s*\d+</p>', fix_grp, h)
    if h2 != h:
        changes.append("侧栏朝代计数")
    return h2


def _resync_event_count(h, changes):
    """首页编纂方针里的事件条目数，由实际事件 view 数推导。"""
    n = len(re.findall(r'<div class="view t-event" id="', h))
    new, k = re.subn(r'(<b class="ct-events">)\d+(</b>)', r"\g<1>%d\g<2>" % n, h, count=1)
    if k != 1:
        sys.exit("FAIL [计数·事件条目数] 匹配 %d 次" % k)
    if new != h:
        changes.append("首页事件条目数")
    return new


def resync(h):
    cards = _cards(h)
    by_cat = {}
    for _, cat in cards:
        by_cat[cat] = by_cat.get(cat, 0) + 1
    changes = []
    h = _resync_totals(h, len(cards), by_cat, changes)
    h = _resync_meta_and_eras(h, by_cat, changes)
    h = _resync_event_count(h, changes)
    return h, changes


# ---------------------------------------------------------------- labels 映射

def add_label(h, vid, kind, name):
    if '"%s"' % vid in h[h.rfind("var labels ="):]:
        sys.exit("FAIL labels 里已有 %s" % vid)
    anchor = '"home": ["首页"'
    entry = '"%s": ["%s", "%s"], ' % (vid, kind, name)
    return sub_once(h, anchor, entry + anchor, "labels 映射 " + vid)


# ---------------------------------------------------------------- 三种插入

def add_artist(h, a):
    frag = io.open(a.fragment, encoding="utf-8").read().rstrip("\n")
    vid = "artist-" + a.id
    if 'id="%s"' % vid in h:
        sys.exit("FAIL 条目 %s 已存在" % vid)

    _, end = view_span(h, a.after)
    h = h[:end] + frag + "\n\n" + h[end:]

    card = ('\n        <a class="acard" data-go="%s" data-cat="%s"><div class="ac-top">'
            '<span class="ac-name">%s</span><span class="ac-chip c-%s">%s</span></div>'
            '<div class="ac-en">%s · %s</div><div class="ac-one">%s</div></a>'
            % (vid, a.cat, a.name, a.chip_class, a.chip or a.cat, a.pinyin, a.sub, a.one_line))
    anchor_card = re.search(r'<a class="acard" data-go="%s".*?</a>' % re.escape(a.after), h, re.S)
    if not anchor_card:
        sys.exit("FAIL 找不到 %s 的首页卡片" % a.after)
    h = h[:anchor_card.end()] + card + h[anchor_card.end():]

    ix = '<a class="ixx" data-go="%s">%s<small>%s</small></a>' % (vid, a.name, a.small)
    anchor_ix = re.search(r'<a class="ixx" data-go="%s">.*?</a>' % re.escape(a.after), h, re.S)
    if not anchor_ix:
        sys.exit("FAIL 找不到 %s 的侧栏索引" % a.after)
    h = h[:anchor_ix.end()] + "\n      " + ix + h[anchor_ix.end():]

    return add_label(h, vid, "画家", a.name)


def add_work(h, a):
    frag = io.open(a.fragment, encoding="utf-8").read().rstrip("\n")
    vid = "work-" + a.id
    if 'id="%s"' % vid in h:
        sys.exit("FAIL 条目 %s 已存在" % vid)
    artist_vid = "artist-" + a.artist

    # 作品条目挂在作者最后一件作品之后；作者尚无作品则挂在其档案之后
    sibs = re.findall(r'<div class="view t-work" id="(work-%s-[^"]+)"' % re.escape(a.artist), h)
    _, end = view_span(h, sibs[-1] if sibs else artist_vid)
    h = h[:end] + frag + "\n\n" + h[end:]

    h = _append_ixcard(h, artist_vid, vid, a.tag, a.title, a.meta, a.desc, "代表作 · 独立条目", "wk")
    return add_label(h, vid, "画作", a.title)


def add_event(h, a):
    frag = io.open(a.fragment, encoding="utf-8").read().rstrip("\n")
    vid = "event-" + a.id
    if 'id="%s"' % vid in h:
        sys.exit("FAIL 条目 %s 已存在" % vid)
    artist_vid = "artist-" + a.artist

    sibs = re.findall(r'<div class="view t-event" id="(event-%s[^"]*)"' % re.escape(a.artist), h)
    _, end = view_span(h, sibs[-1] if sibs else artist_vid)
    h = h[:end] + frag + "\n\n" + h[end:]

    h = _append_ixcard(h, artist_vid, vid, a.tag, a.title, a.meta, a.desc, "关键事件索引", "ev")
    return add_label(h, vid, "事件", a.title)


def _append_ixcard(h, artist_vid, vid, tag, title, meta, desc, section_title, cls):
    """把一张 ixcard 追加进画家条目里指定的索引区块；区块不存在则新建。"""
    card = ('\n    <a class="ixcard" data-go="%s">\n      <span class="tag %s">%s</span>\n'
            '      <div class="ti">%s</div>\n      <div class="meta">%s</div>\n'
            '      <div class="ds">%s →</div>\n    </a>'
            % (vid, cls, tag, title, meta or "", desc or ""))
    s, e = view_span(h, artist_vid)
    seg = h[s:e]

    blk = re.search(r'<h2>%s.*?<div class="ixcards">(.*?)</div>' % re.escape(section_title), seg, re.S)
    if blk:
        pos = s + blk.end(1)
        return h[:pos] + card + "\n  " + h[pos:]

    # 新建区块，插在 ★代表作 区块之前（没有则插在 footer 之前）
    en = {"wk": "linked works", "ev": "linked events"}[cls]
    section = ('\n    <section class="blk">\n      <div class="sh"><span class="no">★</span>'
               '<h2>%s <span class="en">%s</span></h2></div>\n'
               '      <div class="ixcards">%s\n      </div>\n    </section>\n'
               % (section_title, en, card))
    star = seg.find('<span class="no">★</span>')
    anchor = seg.rfind('<section class="blk">', 0, star) if star > 0 else seg.rfind("<footer>")
    if anchor < 0:
        sys.exit("FAIL %s 里找不到插入索引区块的位置" % artist_vid)
    return h[:s + anchor] + section + h[s + anchor:]


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(description="往 index.html 加条目并重算计数")
    sp = p.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("artist", help="加一位画家")
    a.add_argument("--id", required=True, help="不含 artist- 前缀")
    a.add_argument("--name", required=True)
    a.add_argument("--pinyin", required=True)
    a.add_argument("--cat", required=True, choices=["绘画", "书法", "篆刻", "雕刻"])
    a.add_argument("--chip", help="卡片上显示的类别文字，默认同 --cat")
    a.add_argument("--small", required=True, help="侧栏单字：绘/书/篆/雕")
    a.add_argument("--sub", required=True, help="卡片上生卒或时代")
    a.add_argument("--one-line", required=True, dest="one_line")
    a.add_argument("--after", required=True, help="插在哪位画家之后，如 artist-litang")
    a.add_argument("--fragment", required=True)

    w = sp.add_parser("work", help="加一件作品")
    w.add_argument("--id", required=True, help="不含 work- 前缀，如 liusongnian-01")
    w.add_argument("--title", required=True)
    w.add_argument("--artist", required=True, help="不含 artist- 前缀")
    w.add_argument("--tag", default="画作")
    w.add_argument("--meta", default="")
    w.add_argument("--desc", default="")
    w.add_argument("--fragment", required=True)

    e = sp.add_parser("event", help="加一个事件")
    e.add_argument("--id", required=True, help="不含 event- 前缀")
    e.add_argument("--title", required=True)
    e.add_argument("--artist", required=True)
    e.add_argument("--tag", default="事件")
    e.add_argument("--meta", default="")
    e.add_argument("--desc", default="")
    e.add_argument("--fragment", required=True)

    sp.add_parser("resync", help="只重算计数")
    return p


CHIP_CLASS = {"绘画": "hua", "书法": "shu", "篆刻": "zhuan", "雕刻": "diao"}


def main():
    args = build_parser().parse_args()
    h = load()
    before = h

    if args.cmd == "artist":
        args.chip_class = CHIP_CLASS[args.cat]
        h = add_artist(h, args)
    elif args.cmd == "work":
        h = add_work(h, args)
    elif args.cmd == "event":
        h = add_event(h, args)

    h, changes = resync(h)
    if h == before:
        print("无改动")
        return 0
    save(h)
    print("已写入 index.html")
    if args.cmd != "resync":
        print("  新增条目: %s" % args.id)
    print("  计数重算: %s" % ("、".join(changes) if changes else "无需调整"))

    rc = subprocess.call([sys.executable, os.path.join(ROOT, "tools", "validate.py")])
    if rc:
        print("\n校验未通过——请检查上面的 ERROR（index.html 已被修改，可 git checkout 回滚）")
    return rc


if __name__ == "__main__":
    sys.exit(main())

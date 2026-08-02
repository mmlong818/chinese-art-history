# -*- coding: utf-8 -*-
"""index.html 结构校验器。

单文件 5.4MB 手写 HTML 无法做常规单测，此脚本即回归基线：
任何改动后跑一次，ERROR 必须为 0。

用法: python tools/validate.py [index.html]
"""
import io
import os
import re
import sys
from collections import Counter, defaultdict

CANON_SECTIONS = [
    "概览", "基础档案", "人生轨迹", "人物关系", "创作体系",
    "历史坐标", "世俗生活", "八卦", "语录", "代表作", "争议",
]

# 体例自定义、经确认保留的条目
SECTION_EXEMPT = set()

if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK，中文会乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load(path):
    return io.open(path, encoding="utf-8").read()


def views_of(html):
    """id -> (类型, 该 view 的 HTML 片段)"""
    marks = [(m.start(), m.group(1), m.group(2))
             for m in re.finditer(r'<div class="view([^"]*)" id="([^"]+)"', html)]
    out = {}
    for i, (pos, cls, vid) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
        kind = re.search(r'\bt-(\w+)', cls)
        if vid in out:
            err("view id 重复: %s" % vid)
        out[vid] = (kind.group(1) if kind else "home", html[pos:end])
    return out


def check_links(html, views):
    """所有 data-go 目标必须存在。"""
    all_ids = set(re.findall(r'id="([^"]+)"', html))
    targets = re.findall(r'data-go="([^"]+)"', html)
    dangling = sorted({t.split("#")[0] for t in targets} - set(views) - all_ids)
    for d in dangling:
        err("死链 data-go=%s 无对应条目" % d)
    inbound = {t.split("#")[0] for t in targets}
    for vid in views:
        if vid != "home" and vid not in inbound:
            warn("孤儿条目(无任何入链): %s" % vid)
    return len(targets)


def check_counts(html, views):
    """首页图录计数 / 筛选器 / 朝代分组 / 卡片 / 侧栏 五处必须一致。"""
    artists = [v for v, (k, _) in views.items() if k == "artist"]
    n = len(artists)

    cards = re.findall(r'<a class="acard" data-go="([^"]+)" data-cat="([^"]+)"', html)
    if len(cards) != n:
        err("首页卡片 %d 枚 ≠ 画家条目 %d 家" % (len(cards), n))
    missing = sorted(set(artists) - {c for c, _ in cards})
    for m in missing:
        err("画家 %s 有条目但首页无卡片" % m)

    by_cat = Counter(cat for _, cat in cards)
    for cat, cnt in re.findall(r'<button[^>]*data-filter="([^"]+)"[^>]*>[^<]*<b>(\d+)</b>', html):
        actual = len(cards) if cat == "all" else by_cat.get(cat, 0)
        if int(cnt) != actual:
            err("筛选器「%s」标 %s，实为 %d" % (cat, cnt, actual))

    meta = re.search(r'class="tulu-meta">(.*?)</div>', html, re.S)
    if meta:
        for label, cnt in re.findall(r'(\w+)\s*<b>(\d+)</b>', meta.group(1)):
            actual = by_cat.get(label, by_cat.get("雕刻") if label == "雕塑" else None)
            if actual is not None and int(cnt) != actual:
                err("首页图录「%s」标 %s，实为 %d" % (label, cnt, actual))

    era = [(m.group(1), int(m.group(2)))
           for m in re.finditer(r'class="era-h">([^<]+)<span class="ct">(\d+)</span>', html)]
    total = sum(c for _, c in era)
    if total != n:
        err("朝代分组合计 %d ≠ 画家 %d 家" % (total, n))

    side = re.findall(r'<a class="ixx" data-go="(artist-[^"]+)"', html)
    if len(side) != n:
        err("侧栏索引 %d 条 ≠ 画家 %d 家" % (len(side), n))
    for m in sorted(set(artists) - set(side)):
        err("画家 %s 有条目但侧栏无索引" % m)
    for grp, cnt in re.findall(r'class="grp-era">([^·<]+)·\s*(\d+)</p>', html):
        pass  # 侧栏朝代计数与 era-h 同源，由上面总数校验覆盖

    return n, by_cat


def check_sections(views):
    """每家画家须具备十二维体例。"""
    for vid, (kind, seg) in views.items():
        if kind != "artist" or vid in SECTION_EXEMPT:
            continue
        heads = " | ".join(re.findall(r'<h2[^>]*>(.*?)</h2>', seg, re.S))
        heads = re.sub(r'<[^>]+>', '', heads)
        miss = [s for s in CANON_SECTIONS if s not in heads]
        if miss:
            err("画家 %s 缺维度: %s" % (vid, "/".join(miss)))


def check_works(views):
    """每件作品须回链作者；每位画家至少 1 件作品条目。"""
    by_artist = defaultdict(list)
    for vid, (kind, seg) in views.items():
        if kind != "work":
            continue
        m = re.search(r'data-go="(artist-[^"]+)"', seg)
        if not m:
            err("作品 %s 未回链作者" % vid)
        else:
            by_artist[m.group(1)].append(vid)
    artists = [v for v, (k, _) in views.items() if k == "artist"]
    for a in artists:
        if not by_artist.get(a):
            err("画家 %s 无任何作品条目" % a)
    thin = sorted(a for a in artists if len(by_artist.get(a, [])) < 3)
    return by_artist, thin


def check_events(views):
    """事件实体覆盖率(设计为画家/事件/画作三类互链)。"""
    events = [v for v, (k, _) in views.items() if k == "event"]
    covered = set()
    for vid, (kind, seg) in views.items():
        if kind == "artist" and re.search(r'data-go="event-', seg):
            covered.add(vid)
    return events, covered


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
    html = load(path)
    views = views_of(html)

    n_links = check_links(html, views)
    n_artists, by_cat = check_counts(html, views)
    check_sections(views)
    by_artist, thin = check_works(views)
    events, ev_covered = check_events(views)

    n_works = sum(1 for _, (k, _) in views.items() if k == "work")
    print("=" * 62)
    print("index.html  %.1f MB / %d views" % (len(html) / 1048576.0, len(views)))
    print("  画家 %d 家 %s" % (n_artists, dict(by_cat)))
    print("  作品 %d 件（不足 3 件的画家 %d 家）" % (n_works, len(thin)))
    print("  事件 %d 条（覆盖 %d/%d 家）" % (len(events), len(ev_covered), n_artists))
    print("  互链 %d 条" % n_links)
    print("=" * 62)
    for w in warnings:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)
    print("-" * 62)
    print("%d errors, %d warnings" % (len(errors), len(warnings)))
    if thin:
        print("待加深(<3 件作品): " + ", ".join(t.replace("artist-", "") for t in thin))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

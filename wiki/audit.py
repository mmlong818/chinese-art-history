"""内容质量自检。与 schema.py 分工明确：

    schema.py   结构对不对——缺维度、死链、块类型不合法、断代块缺失
    audit.py    内容像不像话——范围、重复、稀薄、单源、态度分布、断代可靠度分布

分开是因为一份数据可以完全合规而依然很差：每一维都在、每条都有信源、断代块也填了，
但通篇断代全标「确证」、一处 gap 也无、所有条目引同一个源——那是把表格填满了，不是档案。

    python audit.py            # 只报告，不改动，不影响构建
"""

import re
import sys
from collections import Counter, defaultdict

from schema import (ARTIST_SECTIONS, DATING_BASIS, RUBBING_EDITIONS,
                    WORK_KIND_GROUP, Corpus)

THIN_CHARS = 120
MIN_SOURCES = 2
LATIN = re.compile(r"[A-Za-z]")
FUNCTION_WORDS = ("the", "of", "in", "to", "is", "was", "that", "with",
                  "for", "as", "by", "from", "this", "which", "it", "on")
# 中文语料里不该出现的外文脚本
CYR_GREEK = re.compile(r"[Ͱ-ϿЀ-ӿ]+")


def _text(bs):
    out = []
    for b in bs or []:
        out += [b.get("text", ""), b.get("what", "")] + list(b.get("items", []))
        for row in b.get("rows", []):
            out += list(row) if isinstance(row, list) else [row.get("text", "")]
    return "".join(str(x) for x in out)


def _blocks(o):
    for bs in o.get("sections", {}).values():
        for b in bs or []:
            yield b


def scope(c, rep):
    """范围符合度：分期须在骨架内；作品 kind 须已定义。"""
    for kind, eid, o in c.all_entities():
        p = o.get("period")
        if p and p not in c.periods:
            rep("范围", f"{kind}/{eid} 的分期 {p} 不在骨架内")
    for w in c.works.values():
        if w.get("kind") not in WORK_KIND_GROUP:
            rep("范围", f'work/{w["id"]} 的 kind {w.get("kind")!r} 未定义')
    covered = {a["period"] for a in c.artists.values() if a.get("period")}
    wcov = {w["period"] for w in c.works.values() if w.get("period")}
    anon = [p["name"] for p in c.periods_sorted()
            if p["id"] not in covered and p.get("anonymity")]
    todo = [p["name"] for p in c.periods_sorted()
            if p["id"] not in (covered | wcov) and not p.get("anonymity")]
    rep("覆盖", f"{len(covered)}/{len(c.periods)} 期有艺术家条目；"
                f"{len(wcov)}/{len(c.periods)} 期有作品条目")
    if anon:
        rep("覆盖", f'{len(anon)} 期无名可指，已写明署名状况（非欠账）：{"、".join(anon)}')
    if todo:
        rep("待编", f'{len(todo)} 期尚无任何条目：{"、".join(todo)}')


def dupes(c, rep):
    """去重：同一作者名下同名作品、同一张图被复用。

    按标题单独去重会误报——中国美术史撞名极多（历代都有《山水图》《墨竹图》），
    真正可疑的是同一作者名下重复。"""
    titles, thumbs = defaultdict(list), defaultdict(list)
    for w in c.works.values():
        titles[(w["title"], w.get("artist") or w.get("site"))].append(w["id"])
        im = w.get("image") or {}
        if im.get("thumb"):
            thumbs[im["thumb"]].append(w["id"])
    for (t, a), ids in titles.items():
        if len(ids) > 1:
            rep("重复", f"{a} 名下「{t}」出现 {len(ids)} 次：{ids}")
    for t, ids in thumbs.items():
        if len(ids) > 1:
            rep("重复", f"同一张图被 {len(ids)} 个条目使用：{ids}")


def thin(c, rep):
    """稀薄：某一维只写一两句就交差。写了 gap 或用 pend/disp/dep 交代过的不算。"""
    for a in c.artists.values():
        for sid, label in ARTIST_SECTIONS:
            body = a.get("sections", {}).get(sid)
            if body is None:
                continue
            owned = any(b.get("t") == "gap" or
                        (b.get("t") == "stmt" and b.get("state") in ("pend", "disp", "dep"))
                        for b in body)
            if len(_text(body)) < THIN_CHARS and not owned:
                rep("稀薄", f'{a["id"]}.{sid}（{label}）仅 {len(_text(body))} 字，'
                            f"且未以 gap 或 pend/disp 交代为何只有这些")


def sourcing(c, rep):
    used = Counter()
    for kind, eid, o in c.all_entities():
        refs = {s["ref"] for s in o.get("sources", []) if isinstance(s, dict) and s.get("ref")}
        used.update(refs)
        if len(refs) < MIN_SOURCES:
            rep("单源", f"{kind}/{eid} 只引了 {len(refs)} 个信源")
        for b in _blocks(o):
            if b.get("src"):
                used[b["src"]] += 1
    never = sorted(c.src_ids - set(used))
    if never:
        rep("信源", f"{len(never)} 个已登记但从未被引用：{never}")
    if used:
        rep("信源", "引用最多：" + "、".join(f"{k}×{v}" for k, v in used.most_common(5)))


def epistemics(c, rep):
    tot = Counter()
    for kind, eid, o in c.all_entities():
        s = Counter()
        for b in _blocks(o):
            if b.get("t") == "stmt":
                s[b.get("state")] += 1
            elif b.get("t") == "gap":
                s["gap"] += 1
        tot.update(s)
        if kind in ("artist", "work") and not s.get("disp") and not s.get("dep"):
            rep("态度", f"{kind}/{eid} 全篇无争议也无旧说——真的毫无异议？")
        if kind in ("artist", "work") and not s.get("gap") and not s.get("pend"):
            rep("态度", f"{kind}/{eid} 全篇无空缺也无待校——史料真这么齐全？")
    n = sum(tot.values()) or 1
    rep("态度", "全库分布：" + "、".join(f"{k} {v}（{v*100//n}%）" for k, v in tot.most_common()))


def dating_health(c, rep):
    """断代健康度。这是中国材料最要紧的一项自检。

    要看的不是「填了没有」（schema 已强制），而是**分布是否可信**：
    若通篇断代都标「确证」，几乎一定是没在分辨——中国器物与石窟里真能确证的
    只有带铭文、碑记纪年、造像题记那一小部分。"""
    basis, conf = Counter(), Counter()
    weak_sure = []
    for w in c.works.values():
        for b in _blocks(w):
            if b.get("t") != "dating":
                continue
            basis[b.get("basis")] += 1
            conf[b.get("conf")] += 1
            if b.get("basis") in ("风格比对", "文献著录") and b.get("conf") == "确证":
                weak_sure.append(w["id"])
    if not basis:
        rep("断代", "尚无 dating 块")
        return
    rep("断代", "依据分布：" + "、".join(f"{k} {v}" for k, v in basis.most_common()))
    rep("断代", "可靠度分布：" + "、".join(f"{k} {v}" for k, v in conf.most_common()))
    n = sum(conf.values())
    if conf.get("确证", 0) > n * 0.5:
        rep("断代", f"确证占 {conf['确证']*100//n}%——**过高可疑**。中国器物与石窟中"
                    f"真能确证的只有带铭文／碑记纪年／造像题记者，比例不该这么大")
    if weak_sure:
        rep("断代", f"以弱依据判确证：{weak_sure}（schema 已拦风格比对，文献著录亦不宜判确证）")


def rubbing_health(c, rep):
    """拓本谱系健康度。石刻条目若只列近拓或翻刻而无早拓说明，证据力要打折。"""
    eds = Counter()
    for w in c.works.values():
        for b in _blocks(w):
            if b.get("t") == "rubbing":
                eds[b.get("edition")] += 1
    if eds:
        rep("拓本", "版本分布：" + "、".join(f"{k} {v}" for k, v in eds.most_common()))
        if eds.get("翻刻") and not (eds.get("宋拓") or eds.get("明拓")):
            rep("拓本", "只见翻刻而无早拓记录——翻刻性质同摹本，不可据以论字口")


def relayer_health(c, rep):
    """重修层健康度。窟寺条目若只写一层，要么真是原貌，要么就是没查。"""
    single = []
    for w in c.works.values():
        if WORK_KIND_GROUP.get(w.get("kind")) != "窟寺":
            continue
        n = sum(1 for b in _blocks(w) if b.get("t") == "relayer")
        if n == 1:
            single.append(w["id"])
    if single:
        rep("重修", f"仅记一层重修的窟寺条目：{single}——若确为原貌未改，"
                    f"应在该维度用 stmt 明说；否则是没查")


def verification(c, rep):
    tot = Counter()
    for kind, eid, o in c.all_entities():
        tot[o.get("verification") or "未标"] += 1
    rep("核查", "、".join(f"{k} {v}" for k, v in tot.most_common()))
    mem = sorted(eid for kind, eid, o in c.all_entities()
                 if o.get("verification") == "memory")
    if mem:
        rep("核查", f"标 memory 的 {len(mem)} 条尚未外部核对，其年代／尺寸／现藏"
                    f"应按待校验对待：{mem[:8]}{'…' if len(mem) > 8 else ''}")


def images(c, rep):
    lic, remote = Counter(), []
    for kind, eid, im in c.all_images():
        lic[im.get("license")] += 1
        if str(im.get("thumb", "")).startswith("http"):
            remote.append(eid)
    noimg = Counter(w.get("image_status", "未说明")
                    for w in c.works.values() if not w.get("image"))
    if lic:
        rep("图像", f"共 {sum(lic.values())} 幅 · 许可 "
                    + "、".join(f"{k} {v}" for k, v in lic.most_common()))
    if noimg:
        rep("图像", f"无图作品 {sum(noimg.values())} 件 · "
                    + "、".join(f"{k} {v}" for k, v in noimg.items()))
    if remote:
        rep("图像", f"{len(remote)} 个条目的缩略图仍是外链，未落盘：{remote[:6]}")


def image_files(c, rep):
    """盘上的图与条目引用的图是否对得上。

    两个方向都要查，性质完全不同：

    **条目引用但盘上无** —— 这是伪造的出处。西洋库出过一次：字段齐全、格式全对、
    许可写 PD，但那个文件根本不存在，元数据是凭记忆生成的。schema.py 已把这条
    升为 ERROR（编出来的 URL 与真 URL 在 JSON 里是同一种字符串，只有文件在不在
    能证伪）。这里再报一次，便于一眼看总量。

    **盘上有但无人引用** —— 这是孤儿，多为取图时试错留下的，或条目改写后弃用的。
    不是错误，但会随时间累积成几百兆无用文件，且让人分不清哪张真在用。
    注意：批次进行中时，agent 已取图而条目未落盘也会显示为孤儿，不要急着删。
    """
    from pathlib import Path
    d = Path(__file__).parent / "static" / "img"
    if not d.exists():
        return
    have = {f.name for f in d.iterdir() if f.is_file()}
    used = set()
    for kind, eid, o in c.all_entities():
        for im in _images_in_obj(o):
            t = str(im.get("thumb", ""))
            if t.startswith("img/"):
                used.add(t[4:])
    missing = sorted(used - have)
    orphan = sorted(have - used)
    if missing:
        rep("图像", f"**条目引用但盘上无 {len(missing)} 个**——这类是编造的出处，"
                    f"schema.py 已报 ERROR：{missing[:6]}")
    if orphan:
        rep("图像", f"孤儿图 {len(orphan)} 个（盘上有、无条目引用）："
                    f"{orphan[:8]}{'…' if len(orphan) > 8 else ''}"
                    f"——批次进行中属正常，收工后再清")


def _images_in_obj(o):
    if isinstance(o, dict):
        if "thumb" in o and "source" in o:
            yield o
        for v in o.values():
            yield from _images_in_obj(v)
    elif isinstance(o, list):
        for v in o:
            yield from _images_in_obj(v)


# 照录馆方记录的字段名。这些行的值是引文，必须保留原文，译了就没法核对。
QUOTED_KEYS = ("原题", "馆方", "入藏", "藏品号", "credit", "原文", "英文题")


def _text_translatable(bs):
    """只取「本该是中文」的文本，跳过照录的馆方字段。

    误报实例：耀州窑那条的 basics 里，`原题`『Bowl with carved floral decoration』、
    `馆方文化断代`『China, probably from the Yaozhou kilns…』都是**引文**，
    照录才对——而且那句 probably 正是本库据以标「推定」而非确定归属的依据。
    把引文算作「未译」，等于罚一个做对了的条目。
    """
    out = []
    for b in bs or []:
        out += [b.get("text", ""), b.get("what", "")] + list(b.get("items", []))
        for row in b.get("rows", []):
            if isinstance(row, list) and len(row) == 2:
                if any(k in str(row[0]) for k in QUOTED_KEYS):
                    continue          # 照录字段，跳过
                out.append(row[1])
            elif isinstance(row, dict):
                out.append(row.get("text", ""))
    return "".join(str(x) for x in out)


def foreign(c, rep):
    """夹在中文里的成段英文，以及不该出现的西里尔／希腊字母。"""
    for kind, eid, o in c.all_entities():
        for sid, bs in o.get("sections", {}).items():
            # 西里尔／希腊一律要查，连引文里也不该有
            for run in CYR_GREEK.findall(_text(bs)):
                rep("外文", f"{kind}/{eid}.{sid} 混入西里尔/希腊字母：{run!r}")
            t = _text_translatable(bs)
            if not t or len(LATIN.findall(t)) <= len(t) * 0.4:
                continue
            found = {w for w in FUNCTION_WORDS if re.search(rf"\b{w}\b", t.lower())}
            if len(found) >= 4:
                rep("未译", f"{kind}/{eid}.{sid} 夹有成段英文（虚词 {sorted(found)[:5]}）")


ORDER = ("范围", "覆盖", "待编", "重复", "稀薄", "单源", "信源", "态度",
         "断代", "拓本", "重修", "核查", "图像", "外文", "未译")
ACTIONABLE = ("范围", "重复", "稀薄", "外文", "未译")


def main():
    c = Corpus()
    findings = defaultdict(list)

    def rep(kind, msg):
        findings[kind].append(msg)

    for check in (scope, dupes, thin, sourcing, epistemics, dating_health,
                  rubbing_health, relayer_health, verification, images,
                  image_files, foreign):
        check(c, rep)

    print(" · ".join(f"{k} {v}" for k, v in c.stats().items()) + "\n")
    for kind in ORDER:
        for m in findings.get(kind, []):
            print(f"  [{kind}] {m}")
    flagged = sum(len(v) for k, v in findings.items() if k in ACTIONABLE)
    print(f"\n需处理 {flagged} 条（{'/'.join(ACTIONABLE)}）；其余为分布报告，供判断，非缺陷。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

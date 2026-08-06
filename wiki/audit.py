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
        items = list(b.get("items") or [])
        # **靠列表的同质性判整张单子,而不是继续往判据里堆关键词。**
        # 剩余漏网的著录都是无作者或以机构为作者的形式（「Cleveland Museum of Art,
        # "Recent Acquisition Press Release," 1959, …Archives.」「Chinese Art:
        # An Exhibition… 1962. Northampton: Smith College…」），逐条认几乎认不完。
        # 但一张单子是同质的：过半条目已认作书目，整张就是书目。
        # 这比再加五个关键词稳，也不会顺带豁免掉夹在正文里的单句英文。
        # 阈值定在「三项以上且至少一条确凿书目」而非「过半」:
        # 书目单子本质同质，而无作者／以机构为作者的著录（「Cleveland Museum of Art,
        # 'Recent Acquisition Press Release,' 1960, …Archives.」《秘殿珠林石渠寶笈》
        # Taipei: National Palace Museum）认不出来的比例偏高，过半这条常不成立。
        # 混着一条书目与五句英文正文的列表，实际不存在。
        # **到此为止不再调这条规则**——审计放宽一次容易，放宽到失效也容易，
        # 而失效的审计比没有审计更坏：它还在报「0 条待处理」。
        if len(items) >= 3 and any(map(_is_citation, items)):
            items = []
        else:
            items = [i for i in items if not _is_citation(i)]
        out += [b.get("text", ""), b.get("what", "")] + items
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
    # 「有无条目」要算**全部六类实体**，不能只算艺术家与作品。
    # 曾经只算那两类，于是把已有 12 条事件的晚清民国、新中国前期、当代报成
    # 「尚无任何条目」——这种误报会驱使人去给已覆盖的分期硬造条目，比漏报更坏。
    covered = {a["period"] for a in c.artists.values() if a.get("period")}
    wcov = {w["period"] for w in c.works.values() if w.get("period")}
    anycov = {o["period"] for _, _, o in c.all_entities() if o.get("period")}
    anon = [p["name"] for p in c.periods_sorted()
            if p["id"] not in covered and p.get("anonymity")]
    todo = [p["name"] for p in c.periods_sorted()
            if p["id"] not in anycov and not p.get("anonymity")]
    rep("覆盖", f"{len(anycov)}/{len(c.periods)} 期有条目（不限实体类）；"
                f"其中艺术家 {len(covered)} 期、作品 {len(wcov)} 期")
    if anon:
        rep("覆盖", f'{len(anon)} 期无名可指，已写明署名状况（非欠账）：{"、".join(anon)}')
    if todo:
        rep("待编", f'{len(todo)} 期尚无任何条目：{"、".join(todo)}')
    else:
        rep("覆盖", "**十七期全部有条目**——本库在时间上已无空缺")


def dupes(c, rep):
    """去重：同一作者名下同名作品、同一张图被复用。

    按标题单独去重会误报——中国美术史撞名极多（历代都有《山水图》《墨竹图》），
    真正可疑的是同一作者名下重复。"""
    from schema import depth_of
    titles, thumbs = defaultdict(list), defaultdict(list)
    for w in c.works.values():
        # 著录级豁免同名检查：馆方题名极多重复（同一批里三件都叫
        # Bodhisattva Guanyin），而它们确是三件不同的物，藏品号已附在题名里作区分。
        # 报出来只会淹掉真正的重复。
        if depth_of(w) != "record":
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
    """信源多寡。**著录级条目豁免「单源」这一条**——

    它照录的就是某一家馆方的记录，引一个源正是它的性质；要求它引两个，
    只会逼出为凑数而添的第二个源。要求多源的理由（免于单一视角）
    只对做过判断的完整级条目成立。"""
    from schema import depth_of
    used = Counter()
    rec = 0
    for kind, eid, o in c.all_entities():
        refs = {s["ref"] for s in o.get("sources", []) if isinstance(s, dict) and s.get("ref")}
        used.update(refs)
        if depth_of(o) == "record":
            rec += 1
        elif len(refs) < MIN_SOURCES:
            rep("单源", f"{kind}/{eid} 只引了 {len(refs)} 个信源")
        for b in _blocks(o):
            if b.get("src"):
                used[b["src"]] += 1
    if rec:
        rep("信源", f"{rec} 条著录级条目按其性质只引馆方一个源，已豁免单源检查")
    never = sorted(c.src_ids - set(used))
    if never:
        rep("信源", f"{len(never)} 个已登记但从未被引用：{never}")
    if used:
        rep("信源", "引用最多：" + "、".join(f"{k}×{v}" for k, v in used.most_common(5)))


def epistemics(c, rep):
    """四态分布。**著录级豁免逐条点名**——

    它本来就不作阐释、不下四态判断，那是它的性质而非缺陷；
    对它报「全篇无争议也无旧说」等于要求一份照录变成一篇论述。
    实测不豁免时：3,355 行输出里 3,335 行（99.2%）是著录级的态度警告，
    **审计被自己淹掉，真正的问题一条也看不见。**
    与 `dupes`／`sourcing` 已有的豁免同理。
    分布统计仍把它们算进去——那是全库的实情，该看见。"""
    from schema import depth_of
    tot = Counter()
    for kind, eid, o in c.all_entities():
        s = Counter()
        for b in _blocks(o):
            if b.get("t") == "stmt":
                s[b.get("state")] += 1
            elif b.get("t") == "gap":
                s["gap"] += 1
        tot.update(s)
        if depth_of(o) == "record":
            continue
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
    # 遍历全部实体，不止 works——遗址条目（石窟、墓葬）同样带 dating 块，
    # 只数 works 会把整整一类断代漏掉，报出来的分布就不是全库分布。
    for kind, eid, o in c.all_entities():
        for b in _blocks(o):
            if b.get("t") != "dating":
                continue
            basis[b.get("basis")] += 1
            conf[b.get("conf")] += 1
            if b.get("basis") in ("风格比对", "文献著录") and b.get("conf") == "确证":
                weak_sure.append(f"{kind}/{eid}")
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
    # 同样遍历全部实体。书家条目里放拓本谱系是正当的——唐楷诸家的字迹本来就靠
    # 碑与拓传下来，谱系写在人的条目里比写在某一件碑上更贴实情。只数 works 时
    # 「宋拓」这一档整个消失，而健康度检查偏偏就看有没有早拓，等于自己蒙住眼。
    for kind, eid, o in c.all_entities():
        for b in _blocks(o):
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
# 照录馆方记录的字段键名。**凡此皆引文，照录才对，译了反而失真。**
# 原表七项漏掉了「材质／技法」「尺寸」「著录」这几个——它们的键名里不含「馆方」二字，
# 于是 404 条机器采集的著录级条目被整片报成「未译」，
# 而那些条目每一条都自带 pend 明说「照录馆方记录、本库尚未独立核校」。
# **误报比漏报更坏**：424 条待处理会让人以为库里有几百条烂账，
# 从而对真正那 20 条视而不见。
QUOTED_KEYS = ("原题", "馆方", "入藏", "藏品号", "credit", "原文", "英文题",
               "材质", "技法", "尺寸", "著录", "英译", "释文", "款识原文")


# 书目著录的判据。**不用「含年份／含 Museum」这类信号**——
# 「The scroll was exhibited at the Cleveland Museum of Art in 1968」也满足它，
# 而那句正是该报的未译英文。用书目特有的两个形状，二者任一即可：
#   一、以「姓, 名」开头（Lee, Sherman E. / Wang, Chi-ch'ien）——叙述句不会这样起头；
#   二、出现 pp./cat. no./vol. 加数字——页码与图录号只在著录里出现。
CIT_AUTHOR = re.compile(r"^[A-Z][A-Za-zÀ-ɏ'’-]+,\s+[A-Z]")
CIT_LOCUS = re.compile(r"(pp?\.|cat\.\s*no\.?|vol\.|fig\.|pl\.)\s*\d", re.I)


def _is_citation(t):
    """判一条 ul 项是不是书目著录。

    著录**本来就该保留原文**：译一个书名不但没有意义，还使它无法回查。
    但判据必须窄到不会顺带豁免掉真正没译的正文，否则这条检查就被掏空了——
    审计规则放宽一次容易，放宽到失效也容易，而失效的审计比没有审计更坏，
    因为它还在报「0 条待处理」。
    """
    t = str(t).strip()
    return bool(CIT_AUTHOR.match(t) or CIT_LOCUS.search(t))


def _text_translatable(bs):
    """只取「本该是中文」的文本，跳过照录的馆方字段。

    误报实例：耀州窑那条的 basics 里，`原题`『Bowl with carved floral decoration』、
    `馆方文化断代`『China, probably from the Yaozhou kilns…』都是**引文**，
    照录才对——而且那句 probably 正是本库据以标「推定」而非确定归属的依据。
    把引文算作「未译」，等于罚一个做对了的条目。

    `prov`／`exhib` 同理且更彻底：**原文本身就是那个事实**。
    「J. J. Klejman Gallery, New York」这类人名商号译了就错；展览的英文名就是
    那场展览的名字，译出来等于发明一个不存在的中文名。递藏链尤其如此——
    它记的是谁在何时经手，改写就不再是档案。
    """
    out = []
    for b in bs or []:
        if b.get("t") in ("prov", "exhib"):
            continue
        # 机器采集的 colophon：`by` 标「馆方著录」者，其正文含馆方英译，
        # 同属引文。人工撰写的 colophon（by 为具体题跋人）仍照查。
        if b.get("t") == "colophon" and "馆方" in str(b.get("by") or ""):
            continue
        items = list(b.get("items") or [])
        # **靠列表的同质性判整张单子,而不是继续往判据里堆关键词。**
        # 剩余漏网的著录都是无作者或以机构为作者的形式（「Cleveland Museum of Art,
        # "Recent Acquisition Press Release," 1959, …Archives.」「Chinese Art:
        # An Exhibition… 1962. Northampton: Smith College…」），逐条认几乎认不完。
        # 但一张单子是同质的：过半条目已认作书目，整张就是书目。
        # 这比再加五个关键词稳，也不会顺带豁免掉夹在正文里的单句英文。
        # 阈值定在「三项以上且至少一条确凿书目」而非「过半」:
        # 书目单子本质同质，而无作者／以机构为作者的著录（「Cleveland Museum of Art,
        # 'Recent Acquisition Press Release,' 1960, …Archives.」《秘殿珠林石渠寶笈》
        # Taipei: National Palace Museum）认不出来的比例偏高，过半这条常不成立。
        # 混着一条书目与五句英文正文的列表，实际不存在。
        # **到此为止不再调这条规则**——审计放宽一次容易，放宽到失效也容易，
        # 而失效的审计比没有审计更坏：它还在报「0 条待处理」。
        if len(items) >= 3 and any(map(_is_citation, items)):
            items = []
        else:
            items = [i for i in items if not _is_citation(i)]
        out += [b.get("text", ""), b.get("what", "")] + items
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
         "断代", "拓本", "重修", "核查", "图像", "外文", "未译", "深度")

def depth_mix(c, rep):
    """条目深度构成。**这一项存在的理由是防自欺。**

    要把本库做到数千条，绝大多数必然是著录级——只照录馆方字段、无人做过判断。
    那本身没问题，**但若不把比例摊在明面上，「本库每条断言都带认知标注」这句话
    就会从事实变成广告**。所以这里报绝对数与占比，并在完整级占比过低时明说。
    """
    from schema import depth_of
    import collections
    n = collections.Counter()
    for kind, eid, o in c.all_entities():
        if kind != "work":
            continue
        n[depth_of(o)] += 1
    tot = sum(n.values()) or 1
    rep("深度", "作品条目：" + "、".join(
        f"{k} {v}（{v*100//tot}%）" for k, v in n.most_common()))
    full = n.get("full", 0)
    if n.get("record") and full * 100 // tot < 20:
        rep("深度", f"完整级仅占 {full*100//tot}%——**本库的认知标注只覆盖了这一小部分**。"
                    f"著录级条目未经取舍，不可当作已审查过的内容引用；"
                    f"对外描述本库时须说明这个比例。")

ACTIONABLE = ("范围", "重复", "稀薄", "外文", "未译")


def main():
    c = Corpus()
    findings = defaultdict(list)

    def rep(kind, msg):
        findings[kind].append(msg)

    for check in (scope, dupes, thin, sourcing, epistemics, dating_health,
                  rubbing_health, relayer_health, verification, images,
                  image_files, foreign, depth_mix):
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

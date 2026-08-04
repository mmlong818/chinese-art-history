"""批量摄入著录级中国艺术家条目：从 Wikidata 取，写成 `depth: record`。

    python ingest_artists.py survey                       # 只统计：候选量、可摄入率、跳过分布
    python ingest_artists.py run --limit 1800 [--apply]    # 生成条目（不加 --apply 只演练）

—— 为什么另立一个脚本，不并进 ingest.py ——

`ingest.py` 那一路摄入的是「物」（work），字段结构、去重键（藏品号）、跳过判据
（朝代名冲突、器类判不出）都围着馆藏记录转。这一路摄入的是「人」（artist），
去重键是中文名而非藏品号，判据核心是生卒年而非创作年代，字段结构是
`ARTIST_SECTIONS`（`basics` 一维）而非 `WORK_SECTIONS`。硬凑进同一个文件，
两条摄入路径会在参数解析、`existing()`、`build_record()` 里互相绊脚。

—— 技术路径 ——

实测 WDQS（`query.wikidata.org/sparql`）限流到 1 请求/分钟，报错里明说是故障期
加的规则，此路不通。改走 CirrusSearch 枚举（`action=query&list=search&
srsearch=haswbstatement:...`）+ `wbgetentities` 取详情，两者都经
`www.wikidata.org/w/api.php`，不经 WDQS。

**过滤是否真生效必须用对照查验**：本项目已两次遇到「过滤没生效而返回全库总量」——
芝加哥 `place_of_origin=China` 返回 0 而不带过滤返回 132,634 之前，
一度以为 `haswbstatement` 返回的 12 万条是全库总数。**数字本身看不出过滤有没有
生效**，只能用一个不存在的 QID 反查、确认它返回 0，此处的 `control_check()` 即此。

—— 写脚本之前，先用真实 API 核对了任务描述里给的 QID，两处是错的 ——

- **Q329439 不是「书法家」，是「雕刻师／engraver」**。若照抄会摄入一批版画雕版匠，
  混进书法家词表。书法家的正确 QID 经 `wbsearchentities` 核实为 **Q3303330**。
- **Q7462 不是「明」，是「宋朝」**。明朝的正确 QID 核实为 **Q9903**。
  （Q8733「清」与任务描述相符，核对无误。）
这两处错误提示：**任务描述里的具体数值也要核，不能因为写在需求里就当作事实**——
与本项目一贯的「不要凭印象，要去查」是同一条纪律。

—— 三类系统性错误，本脚本专防（前两类详见 `recheck.py` 文档字符串，不重复例子）——

一、生卒不详的中国画家，Wikidata 常把「活动年代」渲染成生卒。
    这类错误在数值上往往**不冲突**（活动年代通常落在真实所属朝代范围内，
    与朝代声明核对不出矛盾），本脚本能可靠拦住的是其中数值本身可疑的一类：
    生卒同年、生卒为整百年、时间精度粗于年（precision<9，如「约十世纪」这类
    只精确到世纪的占位值——实测巨然、孙位皆属此类，且这一条比数值本身的
    整百/同年判断更早、更可靠地拦住了它们）。落在朝代范围内、数值本身
    不可疑的活动年代（如张萱 713–755），机械规则拦不住，这是本脚本自认的
    盲区，摄入前须知悉（见文末报告的「未能防住」一节）。
二、同名误指、占位假值：生卒同年、整百年、精度不足，一律不写入生卒字段，
    并使该条目因缺生卒而无法定分期——按第三条硬规矩直接跳过整条，
    不是「摄入但生卒留空」。
三、**职业声明本身有误**——写脚本时实测发现的、任务描述未提及的第三类陷阱：
    曹雪芹（Q182874，《红楼梦》作者，本业小说家）在 Wikidata 上被 P106 标了
    「画家」，若只信 P106 会把他当画家收进本库。`profession_conflicts()`
    用一条低成本规则挡它：cat 判定成立后，若该条目的 Wikidata 描述通篇是
    另一种职业（作家、政治家、官员……）且不见任何书画雕塑字样，判为职业
    声明冲突，不采。描述为空时不判冲突——没有信息不等于有矛盾。

—— 摄入的第一原则（与 ingest.py 一致）：判不出来就不摄入 ——

分期判不出、cat 判不出、生卒疑似占位、id 与本库已有 130 位重名——
一律跳过并计入报告。宁可少摄五百人，不能错断一百人。
"""

import argparse
import collections
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WIKI = pathlib.Path(__file__).parent
sys.path.insert(0, str(WIKI))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

WD_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
UA = "china-art-history-archive/0.1 (record-level artist ingest; contact via repo)"
WD_GAP = 3.0  # 秒。比 ingest.py 的 2.5 秒再放宽——这两天 Wikidata 限流更频繁。

# ── 职业 QID（写脚本前用 wbsearchentities / wbgetentities 逐一核对，非照抄）──
OCC = {
    "Q1028181": "画家",
    "Q3303330": "书法家",   # 任务描述给的 Q329439 实为「雕刻师/engraver」，已核正
    "Q1281618": "雕塑家",
}
# 篆刻家不单独作枚举源（任务只要求画家/书法家/雕塑家三支检索），
# 但若命中的人恰好也带此职业声明，参与 cat 判定。
OCC_EXTRA_CAT = {"Q89675973": "篆刻家"}
OCC_TO_CAT = {"画家": "绘画", "书法家": "书法", "雕塑家": "雕塑", "篆刻家": "篆刻"}

# ── 国籍/政权 QID（同样经核对；任务给的 Q7462 实为「宋朝」，非「明」）──────
NAT = {
    "Q148": "中华人民共和国",
    "Q29520": "中国（文明/地区，笼统国籍声明）",
    "Q13426199": "中华民国大陆时期",
    "Q9903": "明朝",
    "Q8733": "清朝",
    "Q7313": "元朝",
    "Q5066": "金朝（女真）",
    "Q4958": "辽朝",
    "Q242115": "五代十国",
    "Q7405": "隋朝",
    "Q7352": "晋朝",
    "Q7209": "汉朝",
    "Q9683": "唐朝",
    "Q319460": "北宋",
    "Q1147043": "南宋",
}

# 政权 QID → canon 分期 id 的**粗略提示**，只用于冲突检测（第二条硬规矩），
# 不用于直接定分期（分期只能来自生卒年——第三条硬规矩）。
# 值为 None 表示该政权跨越本库不止一个分期，提示意义不大，不参与冲突检测。
DYNASTY_PERIOD_HINT = {
    "Q148": None, "Q29520": None,
    "Q13426199": "late-qing-republic",
    "Q9903": "ming", "Q8733": "qing", "Q7313": "yuan",
    "Q5066": "liao-jin", "Q4958": "liao-jin",
    "Q242115": "five-dynasties", "Q7405": "sui-tang", "Q7352": "wei-jin-nanbei",
    "Q7209": "qin-han", "Q9683": "sui-tang",
    "Q319460": "northern-song", "Q1147043": "southern-song",
}


def _periods():
    c = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    out = {}
    for p in c["periods"]:
        s, e = p.get("span_start"), p.get("span_end")
        out[p["id"]] = (s, e if e is not None else 9999)
    return out


SPANS = _periods()


def period_by_year(y):
    fits = [pid for pid, (s, e) in SPANS.items()
            if s is not None and e is not None and s <= y <= e]
    return fits[0] if len(fits) == 1 else None


def _get(url, tries=7):
    """带退避重试。2→4→8→16→32→64 秒，遇 429 读 Retry-After。
    一次限流失败就放弃，会把「被限流」误报成「无此数据」——那是最坏的结论。"""
    delay = 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            wait = delay
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra and str(ra).isdigit():
                wait = max(delay, float(ra))
            time.sleep(wait)
            delay *= 2
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _wd(**kw):
    kw.setdefault("format", "json")
    return _get(WD_API + "?" + urllib.parse.urlencode(kw))


def control_check():
    """对照查验：不存在的 QID 必须返回 0 条，否则 haswbstatement 过滤没生效。"""
    d = _wd(action="query", list="search",
            srsearch="haswbstatement:P106=Q999999999", srnamespace=0, srlimit=1)
    n = d["query"]["searchinfo"]["totalhits"]
    if n != 0:
        sys.exit(f"对照查得 {n}（应为 0）——haswbstatement 过滤未生效，拒绝摄入")
    print("对照查验通过：不存在的 QID 返回 0 条，过滤生效。")


def enumerate_qids():
    """按 (职业, 政权) 组合枚举候选 QID，去重后返回集合。"""
    seen = set()
    for occ_q, occ_name in OCC.items():
        for nat_q, nat_name in NAT.items():
            query = f"haswbstatement:P106={occ_q} haswbstatement:P27={nat_q}"
            off = 0
            got_this_pair = 0
            while True:
                d = _wd(action="query", list="search", srsearch=query,
                        srnamespace=0, srlimit=50, sroffset=off)
                hits = d["query"]["search"]
                if not hits:
                    break
                for h in hits:
                    seen.add(h["title"])
                got_this_pair += len(hits)
                off += 50
                time.sleep(WD_GAP)
                if off >= 1000:  # 单一组合超过 1000 条，翻页到此为止，靠其他组合补
                    break
            if got_this_pair:
                print(f"   {occ_name} x {nat_name}: {got_this_pair} 条候选（累计去重 {len(seen)}）")
    return seen


# ── 详情解析 ──────────────────────────────────────────────────────────────

def _claim_years(cl, pid):
    """取某属性（P569/P570）的可用年份。**有冲突或精度不足一律返回 None**——
    多个不同数值且无 preferred 标注时，不猜哪个对。"""
    claims = [c for c in cl.get(pid, []) if c.get("rank") != "deprecated"]
    preferred = [c for c in claims if c.get("rank") == "preferred"]
    if preferred:
        claims = preferred
    years, has_ref = set(), False
    for c in claims:
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if not isinstance(v, dict) or not v.get("time"):
            continue
        if v.get("precision", 0) < 9:   # 精度粗于年（世纪/千年）不可用
            continue
        m = re.match(r"([+-])(\d{1,5})-", str(v["time"]))
        if not m:
            continue
        y = int(m.group(2))
        years.add(-y if m.group(1) == "-" else y)
        if c.get("references"):
            has_ref = True
    if len(years) != 1:
        return None, False
    return years.pop(), has_ref


def _qid(v):
    return v.get("id") if isinstance(v, dict) else None


def _all_qids(cl, pid):
    out = []
    for c in cl.get(pid, []):
        if c.get("rank") == "deprecated":
            continue
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        q = _qid(v)
        if q:
            out.append(q)
    return out


def pick_dates(cl, dynasty_hints):
    """返回 (birth, death, 生卒依据说明, 拦截原因或 None)。

    拦截规则（第二条硬规矩）：
      - 生卒同年 → 占位，拒
      - 生卒任一为整百年 → 占位，拒
      - 与唯一确定的朝代提示数值冲突（相差超过 30 年的缓冲）→ 拒
    """
    by, by_ref = _claim_years(cl, "P569")
    dy, dy_ref = _claim_years(cl, "P570")
    if by is None and dy is None:
        return None, None, "", "无可用生卒年（缺失、精度不足或多值冲突）"
    if by is not None and dy is not None:
        if by == dy:
            return None, None, "", f"生卒同年 {by}——判为占位假值，不采"
        if by % 100 == 0 or dy % 100 == 0:
            return None, None, "", f"生卒 {by}–{dy} 含整百年——判为占位假值，不采"
    hints = {h for h in dynasty_hints if h}
    if len(hints) == 1:
        hint_pid = hints.pop()
        s, e = SPANS.get(hint_pid, (None, None))
        if s is not None and e is not None:
            for y in (by, dy):
                if y is not None and (y < s - 30 or y > e + 30):
                    return (None, None, "",
                            f"生卒含 {y}，与所属政权对应分期 {hint_pid}（{s}–{e}）"
                            f"明显冲突——疑为活动年代或误年，不采")
    basis = []
    if by is not None:
        basis.append(f"生 {by}" + ("" if by_ref else "（P569 无引用来源）"))
    if dy is not None:
        basis.append(f"卒 {dy}" + ("" if dy_ref else "（P570 无引用来源）"))
    return by, dy, "、".join(basis), None


def pick_artist_period(by, dy):
    """生卒对照 canon 分期。**生卒跨分期（如跨朝代之交）本脚本不替使用者判断，
    一律跳过**——本库现有 130 位对这类跨代人物（如赵孟頫、赵佶）是人工取舍的，
    自动化不该替这种判断代劳。"""
    if by is not None and dy is not None:
        pb, pd = period_by_year(by), period_by_year(dy)
        if pb is None or pd is None:
            return None, f"生卒 {by}–{dy} 落入分期空窗或与多个分期重叠区间，判不出"
        if pb != pd:
            return None, f"生 {by} 属 {pb}、卒 {dy} 属 {pd}，跨分期人物，不替人工判断代劳，跳过"
        return pb, f"生卒 {by}–{dy} 唯一落入 {pb}"
    y = by if by is not None else dy
    p = period_by_year(y)
    if p is None:
        return None, f"仅一端生卒年 {y} 可用，且落入分期空窗或与多个分期重叠，判不出"
    tag = "生年" if by is not None else "卒年"
    return p, f"仅{tag} {y} 可用，唯一落入 {p}"


def pick_cat(cl):
    occs = _all_qids(cl, "P106")
    seen_cat = []
    for q in occs:
        name = OCC.get(q) or OCC_EXTRA_CAT.get(q)
        if name:
            cat = OCC_TO_CAT[name]
            if cat not in seen_cat:
                seen_cat.append(cat)
    if not seen_cat:
        return None
    return "、".join(seen_cat)


ART_KW = re.compile(r"画|書法|书法|篆刻|雕塑|艺术|藝術|美术|美術|工笔|山水|花鸟|人物画|书画|書畫")
NON_ART_KW = re.compile(r"作家|小说家|小說家|诗人|詩人|文学家|文學家|政治家|官员|官員|"
                        r"皇帝|将军|將軍|商人|演员|演員|歌手|运动员|運動員|科学家|科學家|"
                        r"工程师|工程師|哲学家|哲學家|历史学家|歷史學家|足球|建筑师|建築師")


def profession_conflicts(desc):
    """P106 声称画家/书法家/雕塑家，但 Wikidata 描述通篇是另一职业、只字不提书画雕塑，
    是写脚本时实测发现的**第三类**系统性错误——职业声明本身有误（如把曹雪芹这样的
    小说家标成『画家』）。描述完全空白时不判冲突（没有信息，不代表有冲突）。"""
    if not desc:
        return False
    if ART_KW.search(desc):
        return False
    return bool(NON_ART_KW.search(desc))


def _period_names():
    c = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    return {p["id"]: p.get("name", p["id"]) for p in c["periods"]}


PERIOD_NAME = _period_names()


def _label(e, langs=("zh", "zh-hans", "zh-hant", "en")):
    lab = e.get("labels", {})
    for l in langs:
        if l in lab:
            return lab[l]["value"]
    return None


def _desc(e, langs=("zh", "zh-hans", "zh-hant", "en")):
    d = e.get("descriptions", {})
    for l in langs:
        if l in d:
            return d[l]["value"]
    return None


def check_commons_license(filename):
    """查 Commons 该文件的实际许可（extmetadata.LicenseShortName），
    映射到本库的自由许可写法。查不到或非自由许可一律返回 None——
    **判准是有没有明确自由许可，不是猜的**。

    `thumb` 取 800px 缩略图（与本库其余图像的取图惯例一致，图示而非图版），
    `full` 保留原图 URL 供需要细看的读者点过去；两者都是远端 https URL，
    不落本地盘，故 schema.py 不会因「文件不存在」报错。"""
    try:
        d = _get(COMMONS_API + "?" + urllib.parse.urlencode({
            "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
            "iiprop": "extmetadata|size|url", "iiurlwidth": 800, "format": "json"}))
    except Exception:
        return None, None
    finally:
        time.sleep(1.5)   # commons.wikimedia.org 是独立主机，但仍克制请求频率
    pages = d.get("query", {}).get("pages", {})
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        thumb_url = ii.get("thumburl") or ii.get("url")
        w = ii.get("thumbwidth") or ii.get("width")
        h = ii.get("thumbheight") or ii.get("height")
        full_url = ii.get("url")
        mapped = None
        if re.search(r"public domain|PD-old|PD-US", lic, re.I):
            mapped = "PD"
        elif re.search(r"^CC0", lic, re.I):
            mapped = "CC0"
        else:
            m = re.match(r"^CC BY(-SA)? (\d\.\d)$", lic.strip())
            if m:
                mapped = lic.strip()
        if not (thumb_url and str(thumb_url).startswith("https://")):
            return None, None
        return mapped, {"w": w, "h": h, "thumb": thumb_url, "full": full_url, "raw_license": lic}
    return None, None


def build_record(e, label_cache_alt, existing_names, existing_ids, report):
    qid = e["id"]
    cl = e.get("claims", {})
    name = _label(e, ("zh", "zh-hans", "zh-hant"))
    if not name:
        return None, "无中文标签"
    if name in existing_names:
        report["dup_existing"] += 1
        return None, f"本库已有同名条目：{name}"
    wid = "wd-" + qid.lower()
    if wid in existing_ids:
        return None, "id 重复（本批已生成）"

    cat = pick_cat(cl)
    if not cat:
        return None, "职业判不出 ARTIST_CAT 词表内的门类"

    desc = _desc(e) or ""
    if profession_conflicts(desc):
        report["placeholder_or_conflict"] += 1
        return None, f"P106 声称{cat}，但 Wikidata 描述「{desc}」通篇是另一种职业、"\
                     f"不见任何书画/雕塑字样——疑似职业声明本身有误，不采"

    nat_qids = _all_qids(cl, "P27")
    hints = [DYNASTY_PERIOD_HINT.get(q) for q in nat_qids if q in DYNASTY_PERIOD_HINT]
    by, dy, dates_basis, block_reason = pick_dates(cl, hints)
    if block_reason:
        report["placeholder_or_conflict"] += 1
        return None, block_reason
    if by is None and dy is None:
        return None, "无可用生卒年"

    pid, why_p = pick_artist_period(by, dy)
    if not pid:
        return None, why_p

    alt_names = []
    for c in cl.get("P742", []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, str):
            alt_names.append(v)
        elif isinstance(v, dict) and v.get("text"):
            alt_names.append(v["text"])

    nat_names = [NAT.get(q, q) for q in nat_qids if q in NAT]

    p18 = None
    for c in cl.get("P18", []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, str):
            p18 = v
            break

    rows = [r for r in [
        ["Wikidata 标签", name],
        ["别号", "、".join(alt_names)] ,
        ["生年（Wikidata）", str(by) if by is not None else ""],
        ["卒年（Wikidata）", str(dy) if dy is not None else ""],
        ["职业（Wikidata）", cat],
        ["国籍/政权声明（Wikidata）", "、".join(nat_names)],
        ["Wikidata 描述", desc],
        ["QID", qid],
    ] if r[1]]

    period_label = PERIOD_NAME.get(pid, pid)
    one_line = (f"{period_label}{cat}。**本条为著录级**："
                f"照录 Wikidata 结构化记录，未作阐释。")

    d = {
        "schema": "artist/1", "id": wid, "depth": "record",
        "name": name,
        "cat": cat, "period": pid,
        "one_line": one_line,
        "verification": "wikidata",
        "updated": "2026-08-05",
        "sources": [{"ref": "wikidata", "note": f"结构化记录 {qid}"}],
        "sections": {
            "basics": [
                {"t": "kv", "rows": rows},
                {"t": "p", "text": f"Wikidata 条目：https://www.wikidata.org/wiki/{qid}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**以上照录 Wikidata 的结构化记录，"
                          "本库未作独立核校，亦未就生平、师承、风格作任何判断。"
                          f"分期归入依据仅为：{why_p}；生卒依据：{dates_basis or '无'}。"
                          "**Wikidata 的中国古代人物记录有两类已知系统性错误**："
                          "（一）生卒不详者的『活动年代』常被渲染成生卒——本条已用"
                          "整百年、生卒同年、与所属政权年代冲突三项规则过滤，"
                          "但落在朝代范围内、数值本身不可疑的活动年代（如史书『开元"
                          "天宝间供奉』一类记载）机械规则拦不住，仍可能混入；"
                          "（二）同名误指。引用本条前须回核原始文献或权威工具书。")},
            ],
        },
    }
    if by is not None:
        d["birth"] = by
    if dy is not None:
        d["death"] = dy
    if alt_names:
        d["name_alt"] = alt_names

    if p18:
        mapped, meta = check_commons_license(p18)
        report["portrait_checked"] += 1
        w = meta.get("w") if meta else None
        h = meta.get("h") if meta else None
        if mapped and isinstance(w, int) and w > 0 and isinstance(h, int) and h > 0:
            d["portrait"] = {
                "source": "wikimedia", "source_url": f"https://commons.wikimedia.org/wiki/File:{p18}",
                "credit": "Wikimedia Commons", "license": mapped,
                "thumb": meta["thumb"], "full": meta["full"], "w": w, "h": h,
            }
            report["portrait_license"][mapped] += 1
        else:
            report["portrait_license"]["未过许可/无法核实，未配图"] += 1

    return d, None


def existing():
    ids, names = set(), set()
    for f in (WIKI / "data" / "artists").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        ids.add(d.get("id"))
        if d.get("name"):
            names.add(d["name"])
    return ids, names


def survey(sample_pairs=None):
    control_check()
    print("枚举候选 QID（这一步会用掉大部分请求配额，只跑少量组合做估算）……")
    pairs = sample_pairs or [("Q1028181", "Q148"), ("Q1028181", "Q9903"),
                              ("Q3303330", "Q148"), ("Q1281618", "Q148")]
    ids, names = existing()
    for occ_q, nat_q in pairs:
        query = f"haswbstatement:P106={occ_q} haswbstatement:P27={nat_q}"
        d = _wd(action="query", list="search", srsearch=query, srnamespace=0, srlimit=1)
        n = d["query"]["searchinfo"]["totalhits"]
        print(f"   {OCC.get(occ_q, occ_q)} x {NAT.get(nat_q, nat_q)}: 共 {n} 条")
        time.sleep(WD_GAP)


def _bucket(why):
    """把变长的跳过说明归到固定几类，供报告聚合——原句仍完整保留在日志里，
    这里只影响统计分桶，不影响任何跳过判断本身。"""
    if "无中文标签" in why:
        return "无中文标签"
    if "本库已有同名条目" in why:
        return "本库已有同名条目（重合）"
    if "id 重复" in why:
        return "id 重复（本批内）"
    if "职业判不出" in why:
        return "职业判不出 ARTIST_CAT 词表"
    if "同年" in why:
        return "生卒同年——占位假值"
    if "整百年" in why:
        return "生卒含整百年——占位假值"
    if "冲突" in why:
        return "生卒与政权声明冲突——疑活动年代/误年"
    if "跨分期" in why:
        return "生卒跨分期人物——不代人工判断"
    if "空窗" in why or "重叠" in why:
        return "分期歧义（空窗或重叠区间）"
    if "无可用生卒年" in why:
        return "无可用生卒年（缺失/精度不足/多值冲突）"
    return "其他：" + why[:30]


def run(limit, apply_):
    control_check()
    ids, names = existing()
    print(f"本库已有艺术家 {len(names)} 位")
    print("枚举候选 QID（职业 x 政权组合，串行，间隔 %.1f 秒）……" % WD_GAP)
    qids = sorted(enumerate_qids())
    print(f"候选去重后共 {len(qids)} 个 QID")

    report = collections.Counter()
    report["placeholder_or_conflict"] = 0
    report["dup_existing"] = 0
    report["portrait_checked"] = 0
    report["portrait_license"] = collections.Counter()
    skip_why = collections.Counter()
    made = []
    period_dist = collections.Counter()
    cat_dist = collections.Counter()

    for i in range(0, len(qids), 40):
        if len(made) >= limit:
            break
        batch = qids[i:i + 40]
        d = _wd(action="wbgetentities", ids="|".join(batch),
                props="labels|descriptions|claims", languages="zh|zh-hans|zh-hant|en")
        ents = list((d.get("entities") or {}).values())
        for e in ents:
            if len(made) >= limit:
                break
            if not e.get("id") or e.get("missing") is not None:
                continue
            rec, why = build_record(e, None, names, ids, report)
            if not rec:
                skip_why[_bucket(why)] += 1
                continue
            ids.add(rec["id"])
            names.add(rec["name"])
            made.append(rec)
            period_dist[rec["period"]] += 1
            cat_dist[rec["cat"]] += 1
        time.sleep(WD_GAP)
        if (i // 40) % 5 == 0:
            print(f"   进度：已查验 {min(i + 40, len(qids))}/{len(qids)}，已摄入 {len(made)}")

    print(f"\n生成 {len(made)} 条著录级艺术家条目；跳过 {sum(skip_why.values())}")
    print("\n跳过原因分布：")
    for k, v in skip_why.most_common(20):
        print(f"   {v:>5}  {k}")
    print(f"\n其中疑似占位假值/活动年代/朝代冲突拦下：{report['placeholder_or_conflict']}")
    print(f"与本库已有 130 位重名：{report['dup_existing']}")
    print("\n分期分布：")
    for k, v in period_dist.most_common():
        print(f"   {v:>5}  {k}")
    print("\ncat 分布：")
    for k, v in cat_dist.most_common():
        print(f"   {v:>5}  {k}")
    print(f"\n核过许可的候选图像：{report['portrait_checked']}")
    for k, v in report["portrait_license"].most_common():
        print(f"   {v:>5}  {k}")

    if apply_:
        for d in made:
            (WIKI / "data" / "artists" / f'{d["id"]}.json').write_text(
                json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写入 data/artists/（{len(made)} 个文件）")
    else:
        print("\n（未写入。加 --apply 才落盘）")
        if made:
            print("样例：")
            print(json.dumps(made[0], ensure_ascii=False, indent=2)[:1200])
    return made


def main():
    ap = argparse.ArgumentParser(description="批量摄入著录级艺术家条目（判不出就不摄入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("survey")
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=1800)
    r.add_argument("--apply", action="store_true")
    r.add_argument("--dry", action="store_true", help="等价于不加 --apply；显式声明意图用")
    a = ap.parse_args()
    if a.cmd == "survey":
        survey()
    else:
        run(a.limit, a.apply)


if __name__ == "__main__":
    main()

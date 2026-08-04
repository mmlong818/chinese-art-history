"""批量摄入著录级条目：从馆方 API 与 Wikidata 取，写成 `depth: record`。

    python ingest.py survey                      # 只统计：可摄入量、映射得上多少、跳过多少
    python ingest.py cma --limit 50 [--apply]    # 克利夫兰中国部 → 作品条目
    python ingest.py artists --limit 100 [--apply]  # Wikidata 中国艺术家 → 艺术家条目

—— 为什么要有著录级 ——

本库的完整级条目每条四千至八千字、每个断言带四态、每处断代附依据与可靠度。
按那个深度做到数千条，在算术上不成立。而**若不分深度就批量灌入，
「本库每条断言都带认知标注」这句话会从事实变成广告**。
所以摄入一律写 `depth: record`：只照录来源实际载明的字段，不生成论述。
契约里 `DEPTH` 那段说明了这两级为何是两种体裁而非详略之别。

—— 摄入的第一原则：判不出来就不摄入 ——

**分期与器类判错，是实质错误，不是数据不全。**
调查时抽样 500 件，用朝代名字符串匹配，`"Qin" in culture` 把 211 件 **Qing（清）**
判成了秦——`Qin` 是 `Qing` 的子串。同类的坑本项目一天内踩了三次
（`FLAT_KEY` 的「绘画」匹配到顶层目录「AI绘画」；盲替换 `archive`→「本库」
弄坏了信源 id `beazley-archive`）。
所以这里的规则是**朝代名按词边界且长者优先，再用数值年代校验一致性；
两者冲突、或任一判不出，一律跳过并计入报告**。宁可少摄一千件，不能错断两百件。

器类同理：`Metalwork` 可能是青铜也可能是金银器，`Sculpture` 可能是石雕、木雕或
铜佛——**看材质字段能定就定，定不了就跳过**，不拿最常见的那个当默认。
"""

import argparse
import collections
import json
import pathlib
import re
import sys
import time
import unicodedata
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

CMA = "https://openaccess-api.clevelandart.org/api/artworks"
WDQS = "https://query.wikidata.org/sparql"
UA = "china-art-history-archive/0.1 (record-level ingest; contact via repo)"
GAP = 1.2

# ── 分期映射：朝代名词边界匹配，**长者必须排在短者之前** ────────────────────
# 正则的 alternation 是最左优先，所以 Qing 必须在 Qin 之前、
# Northern/Southern Song 必须在 Song 之前，否则就重演那个 211 件的错。
PERIOD_PATTERNS = [
    (r"Neolithic", "neolithic"),
    (r"Erlitou", "erlitou-shang"),
    (r"Shang dynasty", "erlitou-shang"),
    (r"Western Zhou", "western-zhou"),
    (r"Eastern Zhou|Spring and Autumn|Warring States", "spring-autumn-warring"),
    (r"Qin dynasty", "qin-han"),
    (r"Han dynasty|Western Han|Eastern Han", "qin-han"),
    (r"Six Dynasties|Northern Wei|Northern Qi|Northern Zhou|Eastern Wei|"
     r"Western Wei|Liu Song|Southern dynasties|Three Kingdoms", "wei-jin-nanbei"),
    (r"Sui dynasty", "sui-tang"),
    (r"Tang dynasty", "sui-tang"),
    (r"Five Dynasties", "five-dynasties"),
    (r"Northern Song", "northern-song"),
    (r"Southern Song", "southern-song"),
    (r"Liao dynasty", "liao-jin"),
    (r"Yuan dynasty", "yuan"),
    (r"Ming dynasty", "ming"),
    (r"Qing dynasty", "qing"),          # 必须在 Qin 之前被试到——见上
    (r"Republic(an)? (period|China)", "late-qing-republic"),
]
# 「Jin」「Song」「Zhou」单独出现时兼指两朝，只靠名字判不了，必须靠年代
AMBIGUOUS = re.compile(r"\bJin dynasty\b|\bSong dynasty\b|\bZhou dynasty\b")


def _periods():
    """canon 的 span_end 对「当代（1978 – 至今）」是 None，取一个远端值兜住。"""
    c = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    out = {}
    for p in c["periods"]:
        s, e = p.get("span_start"), p.get("span_end")
        out[p["id"]] = (s, e if e is not None else 9999)
    return out


SPANS = _periods()


def pick_period(culture, y0, y1):
    """返回 (period_id, 理由) 或 (None, 跳过原因)。**判不出就返回 None。**"""
    hay = str(culture or "")
    named = None
    for pat, pid in PERIOD_PATTERNS:
        if re.search(pat, hay, re.I):
            named = pid
            break
    if named:
        s, e = SPANS.get(named, (None, None))
        if s is not None and e is not None and y0 and y1 and (y1 < s - 80 or y0 > e + 80):
            return None, f"朝代名判为 {named}（{s}–{e}）而年代 {y0}–{y1} 相去甚远，冲突不摄"
        return named, f"朝代名 → {named}"
    if AMBIGUOUS.search(hay):
        return None, f"「{AMBIGUOUS.search(hay).group()}」兼指两朝，仅名不足判"
    if y0 and y1:
        fits = [pid for pid, (s, e) in SPANS.items()
                if s is not None and e is not None and s <= y0 and y1 <= e]
        if len(fits) == 1:
            return fits[0], f"年代 {y0}–{y1} 唯一落入 {fits[0]}"
        return None, f"年代 {y0}–{y1} 落入 {len(fits)} 个分期，不唯一"
    return None, "既无可识别朝代名，也无年代"


# ── 器类映射：定不了就跳过，不拿最常见的当默认 ────────────────────────────
BRONZE = re.compile(r"bronze", re.I)
GOLDSILVER = re.compile(r"\bgold\b|\bsilver\b|gilt", re.I)
STONE = re.compile(r"stone|marble|limestone|sandstone", re.I)
WOOD = re.compile(r"\bwood\b|lacquered wood", re.I)


def pick_kind(t, medium):
    m = str(medium or "")
    direct = {"Ceramic": "陶瓷", "Painting": "画作", "Calligraphy": "书法",
              "Jade": "玉器", "Lacquer": "漆器", "Print": "版画",
              "Enamel": "珐琅", "Stone": "石雕", "Silver": "金银器",
              "Ivory": "竹木牙角", "Wood": "木雕",
              "Furniture and woodwork": "家具", "Textile": "织绣"}
    if t in direct:
        return direct[t], f"type={t}"
    if t == "Metalwork":
        if BRONZE.search(m):
            return "青铜", "type=Metalwork + medium 含 bronze"
        if GOLDSILVER.search(m):
            return "金银器", "type=Metalwork + medium 含 gold/silver"
        return None, f"Metalwork 但材质判不出青铜或金银：{m[:44]!r}"
    if t == "Sculpture":
        if STONE.search(m):
            return "石雕", "Sculpture + 石质"
        if BRONZE.search(m) or GOLDSILVER.search(m):
            return "铜佛", "Sculpture + 铜／鎏金"
        if WOOD.search(m):
            return "木雕", "Sculpture + 木质"
        return None, f"Sculpture 但材质判不出：{m[:44]!r}"
    if t == "Jewelry":
        if GOLDSILVER.search(m):
            return "金银器", "Jewelry + 金银"
        if re.search(r"jade|nephrite", m, re.I):
            return "玉器", "Jewelry + 玉"
        return None, f"Jewelry 但材质判不出：{m[:44]!r}"
    return None, f"type={t!r} 无对应 kind"


def _get(url, tries=7):
    """带退避重试。**Wikidata 与 WDQS 都在限流**（WDQS 实测 1 请求/分钟，
    报错里明说该规则是故障期加的），所以退避要长、次数要多：
    2→4→8→16→32→64 秒，且遇 429 时读 Retry-After。
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


def _slug(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


def existing():
    ids, accs = set(), set()
    for f in (WIKI / "data").glob("*/*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        ids.add(d.get("id"))
        blob = json.dumps(d, ensure_ascii=False)
        for m in re.finditer(r"/art/([\d.]+)", blob):
            accs.add(m.group(1))
    return ids, accs


def cma_page(skip, limit=100):
    fields = ("id,accession_number,title,title_in_original_language,type,culture,"
              "creation_date,creation_date_earliest,creation_date_latest,technique,"
              "measurements,creditline,share_license_status,images,url,department,"
              "tombstone,find_spot")
    return _get(f"{CMA}/?department=Chinese%20Art&limit={limit}&skip={skip}"
                f"&fields={fields}")["data"]


def is_component(acc):
    """藏品号点分三段以上者是**部件**（1915.334.1 是 1915.334 的一件）。

    第一版用「id 结尾是字母或数字」这个模式判，结果 300 条全被误判——
    `cma-1914-567` 的结尾本来就是数字。**这是今天第四次同形状的错**：
    用一个宽模式去认结构，而那个模式在正常数据上也成立。
    要认的是藏品号的分段数，不是 id 的字面结尾。
    """
    return len(str(acc).split(".")) >= 3


def base_acc(acc):
    parts = str(acc).split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 else str(acc)


def build_record(o, seen_acc=()):
    """把一件馆方记录转成著录级条目，或返回跳过原因。"""
    cu = (o.get("culture") or [""])[0]
    if "china" not in cu.lower() and "chinese" not in cu.lower():
        return None, f"culture 未言明中国：{cu[:40]!r}"
    pid, why_p = pick_period(cu, o.get("creation_date_earliest"),
                             o.get("creation_date_latest"))
    if not pid:
        return None, why_p
    kind, why_k = pick_kind(o.get("type"), o.get("technique"))
    if not kind:
        return None, why_k
    acc = str(o.get("accession_number") or o.get("id"))
    # 母记录在同批里出现过，就不再为它的部件单独立目——否则一件东西成三条。
    if is_component(acc) and base_acc(acc) in seen_acc:
        return None, f"部件（母记录 {base_acc(acc)} 已摄）"
    wid = "cma-" + _slug(acc)
    en = str(o.get("title") or "").strip()
    zh = str(o.get("title_in_original_language") or "").strip()
    # **题名取中文原题（馆方给了 67%）**——这是中文库，英文题名只能作备录。
    # 并一律附藏品号：著录级条目的身份就是藏品号，而馆方题名极多重复
    # （同一批里三件都叫 Bodhisattva Guanyin），不附则读者在列表里分不开。
    base = zh or en or "（馆方未题名）"
    title = f"{base}（{acc}）"
    meas = str(o.get("measurements") or "").strip()
    d = {
        "schema": "work/1", "id": wid, "depth": "record",
        "title": title,
        "title_orig": en if zh else "",
        "kind": kind, "period": pid,
        "holder": f"克利夫兰美术馆（藏品号 {acc}）",
        "year": str(o.get("creation_date") or "").strip(),
        "size": meas,
        "medium": str(o.get("technique") or "").strip(),
        "one_line": (f"克利夫兰美术馆藏{kind}，馆方断代作「{o.get('creation_date') or '未载'}」。"
                     f"**本条为著录级**：照录馆方记录，未作阐释。"),
        "verification": "museum",
        "updated": "2026-08-05",
        "sources": [{"ref": "cleveland", "note": f"馆方藏品记录，藏品号 {acc}"}],
        "image_status": "pending",
        "sections": {
            "basics": [
                {"t": "kv", "rows": [r for r in [
                    ["馆方原题（英）", en],
                    ["馆方原题（中）", zh],
                    ["馆方文化断代", cu],
                    ["馆方年代", str(o.get("creation_date") or "")],
                    ["材质／技法", str(o.get("technique") or "")],
                    ["尺寸", meas],
                    ["入藏说明", str(o.get("creditline") or "")],
                    ["出土地（馆方所记）", str(o.get("find_spot") or "")],
                    ["藏品号", acc],
                ] if r[1]]},
                {"t": "p", "text": f"馆方藏品页：{o.get('url') or ''}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**以上各项照录克利夫兰美术馆的藏品记录，"
                          "字段名标明「馆方」者即其原文。本库尚未对其作独立核校，"
                          "亦未就断代、归属、风格作任何判断——"
                          f"分期归入依据仅为：{why_p}；器类归入依据仅为：{why_k}。")},
            ],
            "dating": [
                {"t": "gap",
                 "text": ("馆方给出了年代，**但未在公开记录中说明这个年代依据什么**——"
                          "是铭文、器形序列、共出器物还是风格比对，无从得知。"
                          "本库的断代十档因此无法填写：**填任何一档都是替馆方作了它没作的声明。**")},
            ],
        },
    }
    return d, None


def survey(pages=6):
    ids, accs = existing()
    n = ok = 0
    skip_why = collections.Counter()
    kinds, periods = collections.Counter(), collections.Counter()
    for i in range(pages):
        for o in cma_page(i * 100):
            n += 1
            if str(o.get("accession_number")) in accs:
                skip_why["已有条目引用该藏品号"] += 1
                continue
            d, why = build_record(o, accs)
            if not d:
                skip_why[re.sub(r"[：:].*", "", why)] += 1
                continue
            ok += 1
            kinds[d["kind"]] += 1
            periods[d["period"]] += 1
        time.sleep(GAP)
    print(f"抽查 {n} 件 → 可摄入 {ok} 件（{ok*100//max(n,1)}%）\n")
    print("跳过原因:")
    for k, v in skip_why.most_common():
        print(f"   {v:>4}  {k}")
    print("\n可摄入者的器类分布:")
    for k, v in kinds.most_common():
        print(f"   {v:>4}  {k}")
    print("\n可摄入者的分期分布:")
    for k, v in periods.most_common():
        print(f"   {v:>4}  {k}")


def acc_of(o):
    return str(o.get("accession_number") or o.get("id"))


def cma(limit=50, apply=False):
    ids, accs = existing()
    made, skipped = [], collections.Counter()
    skip = 0
    while len(made) < limit and skip < 2700:
        page = cma_page(skip)
        if not page:
            break
        for o in page:
            if len(made) >= limit:
                break
            if str(o.get("accession_number")) in accs:
                skipped["已有"] += 1
                continue
            d, why = build_record(o, accs)
            if not d:
                skipped[re.sub(r"[：:].*", "", why)] += 1
                continue
            accs.add(acc_of(o))
            if d["id"] in ids:
                skipped["id 重复"] += 1
                continue
            ids.add(d["id"])
            made.append(d)
        skip += 100
        time.sleep(GAP)
    print(f"生成 {len(made)} 条著录级条目；跳过 {sum(skipped.values())} 件")
    for k, v in skipped.most_common(8):
        print(f"   {v:>4}  {k}")
    if apply:
        for d in made:
            (WIKI / "data" / "works" / f'{d["id"]}.json').write_text(
                json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写入 data/works/（{len(made)} 个文件）")
    else:
        print("\n（未写入。加 --apply 才落盘）")
        if made:
            print("样例：")
            print(json.dumps(made[0], ensure_ascii=False, indent=2)[:900])



# ── Wikidata：国内各馆藏品 + 艺术家 ────────────────────────────────────────
#
# **这一路是为纠偏而加的。**若作品九成来自克利夫兰，本库实质上是「克利夫兰中国
# 藏品目录」冒充中国美术史；更坏的是，它会把本库自己反复讲的那个不对称——
# 流散文物数据齐备、国内重器无从核——**从一个被记录的事实变成一个被复制的结构**。
#
# Wikidata 里确有大量国内馆藏的结构化记录，且带故宫的真实藏品号
# （秋郊饮马图 故00005866、步辇图 新00119106、五牛图 新00087179）。
# 实测 150 条故宫藏品的字段覆盖：中文标签 99%、藏品号 98%、
# 创作者 21%、图像 20%、**年代仅 13%**。
# 年代是 `period` 的来源，所以摄入率受它限制——**没年代又没可用创作者的一律跳过**。
#
# 另：WDQS（SPARQL 端点）在故障期，实测限流到 1 请求/分钟；
# 故这里走 CirrusSearch（`haswbstatement:`）+ `wbgetentities`，两者都不经 WDQS。
# **过滤是否真生效必须用对照查验**：以不存在的 QID 查得 0 条才算生效——
# 本项目已有两次「过滤没生效而返回全库总量」的实例（芝加哥 place_of_origin、
# 我最初那次 haswbstatement 的怀疑）。

WD_API = "https://www.wikidata.org/w/api.php"
WD_GAP = 2.5   # 秒。比克利夫兰慢一倍——Wikidata 这两天在限流

# 现藏机构 QID → (本库信源 id, 馆名)。经检索 API 逐一核过，不是猜的。
WD_MUSEUMS = {
    "Q2047427": ("dpm", "故宫博物院"),
    "Q540668": ("npm-taipei", "国立故宫博物院（台北）"),
    "Q1051293": ("shanghai-museum", "上海博物馆"),
    "Q1074318": ("nmc", "中国国家博物馆"),
    "Q1278762": ("zhejiang-museum", "浙江省博物馆"),
    "Q4391403": ("hubei-museum", "湖北省博物馆"),
    "Q1151210": ("shaanxi-history-museum", "陕西历史博物馆"),
}

# P31 → kind。Q17362920 是「維基媒體重複頁面」标记项，**不是艺术品，必须排除**。
WD_KIND = {
    "Q3305213": "画作", "Q2026188": "画作", "Q22669850": "书法",
    "Q75837457": "版画", "Q11060274": "版画",
    "Q1348059": "画作",              # 手卷
    "Q98276829": "陶瓷", "Q17379525": "陶瓷", "Q14745": "家具",
}
# 明确拒绝映射的类型。**「太泛」与「未收录」是两种不同的拒绝理由，都要写下来**，
# 否则下一个人会以为只是漏了：
#   Q17362920 維基媒體重複頁面 —— 不是艺术品，是维护用的标记项
#   Q2342494  收藏品          —— 「有收藏价值之物」，材质与门类什么都没说
#   Q49848    文献            —— 映射成「书法」就是发明；它可能只是一份文书
#   Q1368     貨幣            —— 本库无「钱币」这一 kind；加一个是范围决定，不顺手做
WD_SKIP_TYPE = {"Q17362920", "Q2342494", "Q49848", "Q1368"}
# 「金属物体」太泛，须按材质判——与克利夫兰 Metalwork 同一处理
WD_METAL = "Q11646939"
WD_MATERIAL = {"Q34095": "青铜", "Q37756": "青铜", "Q897": "金银器",
               "Q1090": "金银器", "Q753": "金银器"}


def _wd(**kw):
    kw.setdefault("format", "json")
    return _get(WD_API + "?" + urllib.parse.urlencode(kw))


def wd_ids(qid, want, control=True):
    """枚举现藏该馆的条目 QID。**先用不存在的 QID 做对照,确认过滤真生效。**"""
    if control:
        c = _wd(action="query", list="search",
                srsearch="haswbstatement:P195=Q999999999", srnamespace=0, srlimit=1)
        if c["query"]["searchinfo"]["totalhits"] != 0:
            sys.exit("对照查得非 0——haswbstatement 过滤未生效，拒绝摄入")
    out, off = [], 0
    while len(out) < want:
        d = _wd(action="query", list="search", srsearch=f"haswbstatement:P195={qid}",
                srnamespace=0, srlimit=50, sroffset=off)
        got = [h["title"] for h in d["query"]["search"]]
        if not got:
            break
        out += got
        off += 50
        time.sleep(WD_GAP)
    return out[:want]


def _claim(cl, pid):
    for c in cl.get(pid, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            return v
    return None


def _qid(v):
    return v.get("id") if isinstance(v, dict) else None


def _year_of(v):
    if not isinstance(v, dict) or not v.get("time"):
        return None
    m = re.match(r"([+-])(\d{1,5})-", str(v["time"]))
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) == "-" else y


def _artist_index():
    """本库艺术家：中文名 → (id, period)。用于据创作者补出分期。"""
    idx = {}
    for f in (WIKI / "data" / "artists").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("name"):
            idx[d["name"]] = (d["id"], d.get("period"))
    return idx


def period_by_year(y):
    fits = [pid for pid, (s, e) in SPANS.items()
            if s is not None and e is not None and s <= y <= e]
    return fits[0] if len(fits) == 1 else None


def wd_build(e, mus_qid, artists, label_cache):
    qid = e["id"]
    cl = e.get("claims", {})
    t = _qid(_claim(cl, "P31"))
    if t in WD_SKIP_TYPE:
        return None, "维基媒体重复页面标记项，非艺术品"
    kind = WD_KIND.get(t)
    if not kind and t == WD_METAL:
        mat = _qid(_claim(cl, "P186"))
        kind = WD_MATERIAL.get(mat)
        if not kind:
            return None, f"金属物体但材质 {mat} 判不出青铜或金银"
    if not kind:
        return None, f"P31={t} 无对应 kind"
    lab = e.get("labels", {})
    name = ((lab.get("zh") or lab.get("zh-hans") or lab.get("zh-hant") or {}).get("value")
            or (lab.get("en") or {}).get("value"))
    if not name:
        return None, "无标签"

    # 分期：先用 P571 的年份；无则看创作者是否为本库已有艺术家，借其分期
    pid = None
    why = ""
    y = _year_of(_claim(cl, "P571"))
    if y is not None:
        pid = period_by_year(y)
        why = f"P571 年代 {y} → {pid}" if pid else ""
    if not pid:
        cq = _qid(_claim(cl, "P170"))
        cname = label_cache.get(cq)
        if cname and cname in artists:
            aid, ap = artists[cname]
            if ap:
                pid, why = ap, f"创作者「{cname}」为本库已有条目，借其分期 {ap}"
    if not pid:
        return None, "无年代且创作者不在本库，分期判不出"

    src_id, mus_name = WD_MUSEUMS[mus_qid]
    acc = _claim(cl, "P217")
    cq = _qid(_claim(cl, "P170"))
    cname = label_cache.get(cq)
    artist_id = artists.get(cname, (None, None))[0] if cname else None

    d = {
        "schema": "work/1", "id": "wd-" + qid.lower(), "depth": "record",
        "title": f"{name}（{acc}）" if acc else name,
        "kind": kind, "period": pid,
        "holder": f"{mus_name}" + (f"（藏品号 {acc}）" if acc else ""),
        "one_line": (f"{mus_name}藏{kind}。**本条为著录级**："
                     f"照录 Wikidata 的结构化记录，未作阐释。"),
        "verification": "wikidata",
        "updated": "2026-08-05",
        "sources": [{"ref": "wikidata", "note": f"结构化记录 {qid}"},
                    {"ref": src_id, "note": "现藏机构；官方藏品页须人工核对"}],
        "image_status": "no-free-image",
        "sections": {
            "basics": [
                {"t": "kv", "rows": [r for r in [
                    ["题名（Wikidata 标签）", name],
                    ["创作者", cname or ""],
                    ["现藏", mus_name],
                    ["藏品号", str(acc or "")],
                    ["Wikidata", qid],
                ] if r[1]]},
                {"t": "p", "text": f"Wikidata 条目：https://www.wikidata.org/wiki/{qid}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**以上照录 Wikidata 的结构化记录，"
                          "本库未作独立核校，亦未就断代、归属、真伪作任何判断。"
                          f"分期归入依据仅为：{why}。"
                          "**Wikidata 的错误率不低于本库**——已累计九例假值与误指"
                          "（占位生卒、活动年代被当生卒、同名误指），"
                          "引用本条前须回核馆方官方页。")},
            ],
            "dating": [
                {"t": "gap",
                 "text": ("Wikidata 未记该件断代所依据的证据类型。**填本库断代十档的任何一档，"
                          "都是替它作了它没作的声明**，故此处留空。")},
            ],
        },
    }
    if artist_id:
        d["artist"] = artist_id
    return d, None


def wd_works(quota, apply=False):
    """按馆分配额摄入，**不让任何一家占多数**。"""
    ids, _ = existing()
    artists = _artist_index()
    made, skipped = [], collections.Counter()
    for mq, (src_id, mus) in WD_MUSEUMS.items():
        want = quota.get(mq, 0)
        if not want:
            continue
        print(f"── {mus}：目标 {want} 条")
        qids = wd_ids(mq, want * 6)      # 摄入率约两成，故多取几倍候选
        got = 0
        # 先批量取标签，供「创作者是否在本库」判断
        label_cache = {}
        for i in range(0, len(qids), 40):
            if got >= want:
                break
            d = _wd(action="wbgetentities", ids="|".join(qids[i:i + 40]),
                    props="labels|claims", languages="zh|zh-hans|zh-hant|en")
            ents = list((d.get("entities") or {}).values())
            # 补取创作者标签
            creators = {_qid(_claim(e.get("claims", {}), "P170")) for e in ents}
            creators = [x for x in creators if x and x not in label_cache]
            for j in range(0, len(creators), 40):
                cd = _wd(action="wbgetentities", ids="|".join(creators[j:j + 40]),
                         props="labels", languages="zh|zh-hans|zh-hant|en")
                for cq, ce in (cd.get("entities") or {}).items():
                    cl_ = ce.get("labels", {})
                    label_cache[cq] = ((cl_.get("zh") or cl_.get("zh-hans")
                                        or cl_.get("zh-hant") or cl_.get("en")
                                        or {}).get("value"))
                time.sleep(WD_GAP)
            for e in ents:
                if got >= want:
                    break
                if not e.get("id"):
                    continue
                w, why = wd_build(e, mq, artists, label_cache)
                if not w:
                    skipped[re.sub(r"[：:].*", "", why)] += 1
                    continue
                if w["id"] in ids:
                    skipped["id 重复"] += 1
                    continue
                ids.add(w["id"])
                made.append(w)
                got += 1
            time.sleep(WD_GAP)
        print(f"   得 {got} 条")
    print(f"\n合计 {len(made)} 条；跳过 {sum(skipped.values())}")
    for k, v in skipped.most_common(8):
        print(f"   {v:>5}  {k}")
    if apply:
        for w in made:
            (WIKI / "data" / "works" / f'{w["id"]}.json').write_text(
                json.dumps(w, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {len(made)} 个文件")
    return made


def main():
    ap = argparse.ArgumentParser(description="批量摄入著录级条目（判不出就不摄入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey"); s.add_argument("--pages", type=int, default=6)
    c = sub.add_parser("cma")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--apply", action="store_true")
    w = sub.add_parser("wd")
    w.add_argument("--per", type=int, default=100,
                   help="每馆配额；分散来源，不让任何一家占多数")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.cmd == "survey":
        survey(a.pages)
    elif a.cmd == "wd":
        wd_works({q: a.per for q in WD_MUSEUMS}, a.apply)
    else:
        cma(a.limit, a.apply)


if __name__ == "__main__":
    main()

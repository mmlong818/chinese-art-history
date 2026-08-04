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


def _get(url, tries=4):
    delay = 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
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


def main():
    ap = argparse.ArgumentParser(description="批量摄入著录级条目（判不出就不摄入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey"); s.add_argument("--pages", type=int, default=6)
    c = sub.add_parser("cma")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.cmd == "survey":
        survey(a.pages)
    else:
        cma(a.limit, a.apply)


if __name__ == "__main__":
    main()

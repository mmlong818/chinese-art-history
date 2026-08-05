"""国内馆藏专路：从 Wikidata 摄入现藏中国境内各馆的作品为著录级条目。

    python ingest_cn.py survey                       # 各馆可得量与摄入率抽查
    python ingest_cn.py run --per 400 [--apply]      # 按馆配额摄入

—— 为什么要单独一路，而且要跑得比海外那几路更狠 ——

**若作品九成来自西方馆，本库就是「西方馆中国藏品目录」冒充中国美术史。**
把它摊到克利夫兰、芝加哥、大都会三家，并不解决这件事——
**分散在西方馆之间只是让偏斜不那么显眼。**

更要紧的是：本库有几十条条目在讲那个不对称（流散文物数据齐备、国内重器无从核），
若数据本身九成是流散品，**那就是用数据重演了自己批评的结构**。
写了那么多条讲这件事，然后自己做一遍，比不讲更糟。

—— 与 ingest.py 里那一路的三处改进 ——

一、**分期以馆方题名的朝代前缀为主来源。**此前用 P571 年代，而实测 150 条故宫藏品里
   **只有 13% 有 P571**，题名却有 98%——而台北故宫的题名开头正写着朝代
   （「元倪瓚容膝齋圖　軸」）。**馆方的朝代归属是它自己的断代声明，
   强于据裸年份的推算**；尤其朝代交界处，数值判法必错（容膝斋图作于 1372 年，
   按年数落入明，画史归元末）。
   但这个办法自身有失效方式：「元明人畫山水集景」的前缀是**两朝连写**，
   按「元」判就错（实际作者是明文嘉），故加复合前缀防护。

二、**类型映射按实测跳过统计补齐**，尤其补上「拓片」——本库有 `rubbing` 块、
   README 明写「研究对象常是拓本而非原石」，**而承载它的 kind 一直缺**。

三、**逐条落盘，不再跑完才写。**此前艺术家那一路是「全有或全无」，
   跑两小时若中途失败就全丢。
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

API = "https://www.wikidata.org/w/api.php"
UA = "china-art-history-archive/0.1 (record-level ingest; contact via repo)"
GAP = 2.2

# 现藏机构 QID → (本库信源 id, 馆名)。QID 经 wbsearchentities 逐一核过。
MUSEUMS = {
    "Q2047427": ("dpm", "故宫博物院"),
    "Q540668": ("npm-taipei", "国立故宫博物院（台北）"),
    "Q1051293": ("shanghai-museum", "上海博物馆"),
    "Q1074318": ("nmc", "中国国家博物馆"),
    "Q1278762": ("zhejiang-museum", "浙江省博物馆"),
    "Q3330767": ("liaoning-museum", "辽宁省博物馆"),
    "Q4391403": ("hubei-museum", "湖北省博物馆"),
    "Q1151210": ("shaanxi-history-museum", "陕西历史博物馆"),
}

KIND = {
    "Q3305213": "画作", "Q2026188": "画作", "Q1348059": "画作",
    "Q22669850": "书法",
    "Q75837457": "版画", "Q11060274": "版画",
    "Q98276829": "陶瓷", "Q17379525": "陶瓷",
    "Q14745": "家具",
}
# 太泛，须按材质（P186）判
BY_MATERIAL = {
    "Q860861": {"Q34095": "石雕", "Q22731": "石雕", "Q23757": "石雕",
                "Q287": "铜造像", "Q37756": "铜造像", "Q753": "铜造像",
                "Q287993": "木雕", "Q1088": "铜造像"},
    "Q11646939": {"Q34095": "青铜", "Q37756": "青铜", "Q897": "金银器",
                  "Q1090": "金银器", "Q753": "金银器"},
}
# 明确拒绝，且**拒绝理由要分清「太泛」与「不属美术史」**
SKIP = {
    # 拓本不单立条目——它并入碑，作为碑的拓本谱系（见 schema.py 的 WORK_KIND_GROUP）。
    # 此前为它立过 kind 并映射了这个 QID，实测从未落成条目。
    "Q112310161": "拓本并入碑，作为碑的补充，不单立条目",
    "Q17362920": "维基媒体重复页面标记项，不是艺术品",
    "Q2342494": "「收藏品」——材质与门类什么都没说",
    "Q49848": "「文献」——映射成书法就是发明",
    "Q1368": "货币；本库无「钱币」kind，加一个是范围决定",
    "Q610038": "银圆；同上",
    "Q571": "图书；不属本库范围",
    "Q2431196": "视听作品；不属本库范围",
    "Q12876": "坦克——国博藏品里确有，但不属美术史范围",
}

DYN = [("五代", "five-dynasties"), ("南唐", "five-dynasties"),
       ("北宋", "northern-song"), ("南宋", "southern-song"),
       ("元", "yuan"), ("明", "ming"), ("清", "qing"),
       ("唐", "sui-tang"), ("隋", "sui-tang"),
       ("晉", "wei-jin-nanbei"), ("晋", "wei-jin-nanbei"),
       ("漢", "qin-han"), ("汉", "qin-han"),
       ("金", "liao-jin"), ("遼", "liao-jin"), ("辽", "liao-jin"),
       ("宋", None)]      # 单「宋」不分南北，判不出
NEXT_DYN = set("五南北元明清唐隋晉晋金遼辽宋代漢汉")


def dyn_of_title(t):
    """题名开头的朝代。**复合前缀一律返回 None**——
    「元明人畫山水集景」的前缀是「元明」两朝连写，按「元」判就错。"""
    t = re.sub(r"^(傳|传)", "", str(t or ""))
    for zh, pid in DYN:
        if t.startswith(zh):
            rest = t[len(zh):]
            if rest and rest[0] in NEXT_DYN:
                return None, f"复合前缀「{zh}{rest[0]}」，不据题名判"
            if pid is None:
                return None, f"「{zh}」不分南北，仅名不足判"
            return pid, f"馆方题名以「{zh}」起"
    return None, "题名无朝代前缀"


def _spans():
    c = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    return {p["id"]: (p.get("span_start"),
                      p.get("span_end") if p.get("span_end") is not None else 9999)
            for p in c["periods"]}


SPANS = _spans()


def _get(url, tries=7):
    delay = 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            ra = e.headers.get("Retry-After") if e.headers else None
            time.sleep(max(delay, float(ra)) if ra and str(ra).isdigit() else delay)
            delay *= 2
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def api(**kw):
    kw.setdefault("format", "json")
    return _get(API + "?" + urllib.parse.urlencode(kw))


def control_ok():
    """**过滤是否真生效，只能靠对照查验。**用绝不存在的 QID 查，必须得 0。
    本项目已两次遇到「过滤没生效而返回全库总量」。"""
    d = api(action="query", list="search",
            srsearch="haswbstatement:P195=Q999999999", srnamespace=0, srlimit=1)
    return d["query"]["searchinfo"]["totalhits"] == 0


def enum_qids(mus_qid, want):
    out, off = [], 0
    while len(out) < want:
        d = api(action="query", list="search",
                srsearch=f"haswbstatement:P195={mus_qid}",
                srnamespace=0, srlimit=50, sroffset=off)
        got = [h["title"] for h in d["query"]["search"]]
        if not got:
            break
        out += got
        off += 50
        time.sleep(GAP)
    return out[:want]


def claim(cl, pid):
    for c in cl.get(pid, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            return v
    return None


def qid_of(v):
    return v.get("id") if isinstance(v, dict) else None


def year_of(v):
    if not isinstance(v, dict) or not v.get("time"):
        return None
    m = re.match(r"([+-])(\d{1,5})-", str(v["time"]))
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) == "-" else y


def period_by_year(y):
    fits = [p for p, (s, e) in SPANS.items()
            if s is not None and s <= y <= e]
    return fits[0] if len(fits) == 1 else None


def artist_index():
    idx = {}
    for f in (WIKI / "data" / "artists").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("name"):
            idx[d["name"]] = (d["id"], d.get("period"))
    return idx


def existing_ids():
    return {p.stem for p in (WIKI / "data" / "works").glob("*.json")}


def build(e, mus_qid, artists, creator_labels):
    cl = e.get("claims", {})
    t = qid_of(claim(cl, "P31"))
    if t in SKIP:
        return None, SKIP[t]
    lab = e.get("labels", {})
    name = ((lab.get("zh") or lab.get("zh-hans") or lab.get("zh-hant") or {}).get("value")
            or (lab.get("en") or {}).get("value"))
    if not name:
        return None, "无标签"

    kind = KIND.get(t)
    if not kind and t in BY_MATERIAL:
        mat = qid_of(claim(cl, "P186"))
        kind = BY_MATERIAL[t].get(mat)
        if not kind:
            return None, f"{t} 太泛而材质 {mat} 判不出"
    if not kind:
        return None, f"P31={t} 无对应 kind"

    # 分期：题名朝代前缀为主 → P571 → 创作者
    pid, why = dyn_of_title(name)
    if not pid:
        y = year_of(claim(cl, "P571"))
        if y is not None:
            pid = period_by_year(y)
            if pid:
                why = f"P571 年代 {y} → {pid}（题名无朝代前缀）"
    cq = qid_of(claim(cl, "P170"))
    cname = creator_labels.get(cq)
    if not pid and cname and cname in artists and artists[cname][1]:
        pid = artists[cname][1]
        why = f"创作者「{cname}」为本库已有条目，借其分期"
    if not pid:
        return None, "分期判不出（题名无前缀、无年代、创作者不在本库）"

    src_id, mus = MUSEUMS[mus_qid]
    acc = claim(cl, "P217")
    aid = artists.get(cname, (None, None))[0] if cname else None
    qid = e["id"]

    d = {
        "schema": "work/1", "id": "cn-" + qid.lower(), "depth": "record",
        "title": f"{name}（{acc}）" if acc else name,
        "kind": kind, "period": pid,
        "holder": mus + (f"（藏品号 {acc}）" if acc else ""),
        "one_line": (f"{mus}藏{kind}。**本条为著录级**："
                     f"照录 Wikidata 结构化记录，未作阐释。"),
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
                    ["现藏", mus],
                    ["藏品号", str(acc or "")],
                    ["Wikidata", qid],
                ] if r[1]]},
                {"t": "p", "text": f"Wikidata 条目：https://www.wikidata.org/wiki/{qid}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**照录 Wikidata 的结构化记录，"
                          "本库未作独立核校，未就断代、归属、真伪作任何判断。"
                          f"分期归入依据仅为：{why}。"
                          "**Wikidata 的错误率不低于本库**——已累计九例假值与误指"
                          "（占位生卒、活动年代被当生卒、同名误指、错藏地错尺寸），"
                          "引用本条前须回核馆方官方页。")},
            ],
            "dating": [
                {"t": "gap",
                 "text": ("Wikidata 未记该件断代所依据的证据类型。**填本库断代十档的任何一档，"
                          "都是替它作了它没作的声明**，故此处留空。")},
            ],
        },
    }
    if aid:
        d["artist"] = aid
    if kind in ("碑刻", "摩崖", "画像石", "经幢", "拓片"):
        d["sections"]["rubbings"] = [
            {"t": "gap", "text": "Wikidata 未记拓本谱系。**碑刻与拓片的研究对象常是拓本而非原石，"
                                 "而早拓与近拓所记的字口已不同**——未注明所据本者，字口存损无从判断。"}]
    return d, None


def run(per, apply_):
    if not control_ok():
        sys.exit("对照查验非 0——haswbstatement 过滤未生效，拒绝摄入")
    print("对照查验通过（不存在的 QID 查得 0 条）\n")
    artists = artist_index()
    ids = existing_ids()
    total, skipped = 0, collections.Counter()
    for mq, (src_id, mus) in MUSEUMS.items():
        print(f"── {mus}：目标 {per} 条")
        qids = [q for q in enum_qids(mq, per * 8)]
        got = 0
        creator_labels = {}
        for i in range(0, len(qids), 40):
            if got >= per:
                break
            d = api(action="wbgetentities", ids="|".join(qids[i:i + 40]),
                    props="labels|claims", languages="zh|zh-hans|zh-hant|en")
            ents = [e for e in (d.get("entities") or {}).values() if e.get("id")]
            need = {qid_of(claim(e.get("claims", {}), "P170")) for e in ents}
            need = [x for x in need if x and x not in creator_labels]
            for j in range(0, len(need), 40):
                cd = api(action="wbgetentities", ids="|".join(need[j:j + 40]),
                         props="labels", languages="zh|zh-hans|zh-hant|en")
                for cq, ce in (cd.get("entities") or {}).items():
                    L = ce.get("labels", {})
                    creator_labels[cq] = ((L.get("zh") or L.get("zh-hans")
                                           or L.get("zh-hant") or L.get("en")
                                           or {}).get("value"))
                time.sleep(GAP)
            for e in ents:
                if got >= per:
                    break
                w, why = build(e, mq, artists, creator_labels)
                if not w:
                    skipped[re.sub(r"[（(：:].*", "", why)] += 1
                    continue
                if w["id"] in ids:
                    skipped["已有"] += 1
                    continue
                ids.add(w["id"])
                got += 1
                total += 1
                if apply_:
                    # **逐条落盘**：不再「全有或全无」
                    (WIKI / "data" / "works" / f'{w["id"]}.json').write_text(
                        json.dumps(w, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
                if got % 25 == 0:
                    print(f"   [{got}/{per}] {w['id']}  {w['kind']}  {w['period']}",
                          flush=True)
            time.sleep(GAP)
        print(f"   得 {got} 条")
    print(f"\n合计 {total} 条；跳过 {sum(skipped.values())}")
    for k, v in skipped.most_common(12):
        print(f"   {v:>5}  {k}")


def survey():
    if not control_ok():
        sys.exit("对照查验非 0，拒绝")
    for mq, (_, mus) in MUSEUMS.items():
        d = api(action="query", list="search", srsearch=f"haswbstatement:P195={mq}",
                srnamespace=0, srlimit=1)
        print(f"   {d['query']['searchinfo']['totalhits']:>7}  {mus}")
        time.sleep(GAP)


def main():
    ap = argparse.ArgumentParser(description="国内馆藏专路（判不出就不摄入，逐条落盘）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("survey")
    r = sub.add_parser("run")
    r.add_argument("--per", type=int, default=300)
    r.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    survey() if a.cmd == "survey" else run(a.per, a.apply)


if __name__ == "__main__":
    main()

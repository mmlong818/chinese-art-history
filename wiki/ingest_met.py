"""大都会艺术博物馆中国藏品 → 著录级条目批量摄入。

    python ingest_met.py --limit 30                 # 试运行，不落盘，看样例与统计
    python ingest_met.py --limit 800 --apply         # 落盘

—— 与 ingest.py 的关系 ——

本文件是 ingest.py 摄入范式在大都会这一条路上的延伸，**不修改 ingest.py**，
而是直接导入其已验证过的部件重用：

    ingest.pick_period   —— 朝代名词边界匹配 + 数值年代一致性校验，
                            「Qin/Qing 子串坑」「Northern/Southern Song 坑」已在那边踩过一次
    ingest.BRONZE/GOLDSILVER/STONE/WOOD —— 器类材质判据的同一套正则
    ingest._get           —— 带指数退避重试的请求封装

以及 fetchimg.py 的：

    fetchimg._is_chinese  —— 大都会「亚洲部」含日韩泰印，产地判断已写好、照用
    fetchimg._save / _jpeg_size —— 落盘与真实尺寸读取（大都会 API 不返回宽高）

本文件新增的只是**大都会特有的两处判据**：
一、器类——大都会 classification 词表（Ceramics/Paintings/Metalwork/Sculpture…）
    与克利夫兰的 type 词表（Ceramic/Painting…）不同，需要独立的 pick_kind_met()；
二、部件去重——大都会同一实物的「甲／乙」两半有时各开一个独立 objectID
   （实测：70728「1981.369.1a」与 70729「1981.369.1b」两个 objectID 同题
    "Rank Badge with Lion of Asia"），不能靠 ingest.is_component() 的
    「藏品号点分三段」判法（大都会用字母后缀而非数字分段），故另写
    is_component_met()：藏品号以单字母（含逗号并列的多字母，如 "a, b"）结尾者
    视为部件，同一去掉字母后缀的藏品号只留首见的一条。

—— 摄入第一原则不变：判不出就不摄入 ——

产地非中国、分期判不出、器类判不出，一律跳过并计入报告，不拿默认值顶上。
"""

import argparse
import collections
import json
import pathlib
import re
import sys
import time

WIKI = pathlib.Path(__file__).parent
sys.path.insert(0, str(WIKI))

import ingest as ing        # noqa: E402  pick_period / 材质正则 / 带退避的 _get，照用不改
import fetchimg as fi        # noqa: E402  _is_chinese / _save / _jpeg_size，照用不改

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

MET_API = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_DEPT = 6           # 亚洲部——涵盖全亚洲，产地判断见 fetchimg._is_chinese
GAP = 1.3              # 秒。大都会未见严格限流，但实测无间隔连续请求会遇 403（见下）
UA_NOTE = (
    "实测字段陷阱：无间隔连续请求 /objects/{id} 会偶发 403 Forbidden（不止 429），"
    "与 fetchimg.py 注释里说的「未见严格限流」不矛盾——那是指配额，"
    "但连续突发仍会被瞬时限速拦一下，靠 ing._get 的指数退避吸收。"
)

# ── 器类：大都会 classification 词表，与 CMA 的 type 词表不同，需单独判 ──────
# 直接可判的——classification 本身已消歧
DIRECT_KIND = {
    "Ceramics": "陶瓷", "Tomb Pottery": "陶瓷",
    "Paintings": "画作",
    "Calligraphy": "书法",
    "Prints": "版画", "Woodblock Prints": "版画", "Illustrated Books": "版画",
    "Jade": "玉器",
    "Lacquer": "漆器",
    "Enamels": "珐琅", "Cloisonné": "珐琅",
    "Furniture": "家具",
    "Bamboo": "竹木牙角", "Ivories": "竹木牙角", "Bone": "竹木牙角", "Horn": "竹木牙角",
    "Textiles-Woven": "织绣", "Textiles-Embroidered": "织绣",
    "Textiles-Tapestries": "织绣", "Textiles-Costumes": "织绣",
    "Textiles-Trimmings": "织绣", "Textiles-Velvets": "织绣",
    "Costumes": "织绣", "Costumes-Printed and Painted": "织绣",
    "Costumes-Woven": "织绣", "Costumes-Embroidered": "织绣",
    "Main dress-Womenswear": "织绣",
}
# 材质待消歧的——同一 classification 下混着不同材质，定不了就跳过，不拿最常见的当默认
JADE_RE = re.compile(r"jade|nephrite|jadeite", re.I)
ENAMEL_RE = re.compile(r"enamel", re.I)
PORCELAIN_RE = re.compile(r"porcelain", re.I)


def pick_kind_met(classification, medium, object_name):
    """返回 (kind, 理由) 或 (None, 跳过原因)。判不出就返回 None——同 ingest.pick_kind 的约定。"""
    c = str(classification or "")
    m = str(medium or "")
    if c in DIRECT_KIND:
        return DIRECT_KIND[c], f"classification={c}"
    if c == "Metalwork":
        if ENAMEL_RE.search(m):
            return "珐琅", "Metalwork + medium 含 enamel"
        if ing.BRONZE.search(m):
            return "青铜", "Metalwork + medium 含 bronze"
        if ing.GOLDSILVER.search(m):
            return "金银器", "Metalwork + medium 含 gold/silver/gilt"
        return None, f"Metalwork 但材质判不出青铜/金银/珐琅：{m[:44]!r}"
    if c == "Sculpture":
        if ing.STONE.search(m):
            return "石雕", "Sculpture + 石质"
        if ing.BRONZE.search(m) or ing.GOLDSILVER.search(m):
            return "铜造像", "Sculpture + 铜／鎏金"
        if ing.WOOD.search(m):
            return "木雕", "Sculpture + 木质"
        return None, f"Sculpture 但材质判不出：{m[:44]!r}"
    if c == "Mirrors":
        if ing.BRONZE.search(m):
            return "青铜", "Mirrors + medium 含 bronze"
        return None, f"Mirrors 但材质非青铜：{m[:44]!r}"
    if c in ("Jewelry", "Seals"):
        if ing.GOLDSILVER.search(m):
            return "金银器", f"{c} + 金银"
        if JADE_RE.search(m):
            return "玉器", f"{c} + 玉"
        if ing.BRONZE.search(m):
            return "青铜", f"{c} + 青铜"
        if ing.WOOD.search(m) or re.search(r"ivory|bamboo|horn", m, re.I):
            return "竹木牙角", f"{c} + 竹木牙角类材质"
        return None, f"{c} 但材质判不出：{m[:44]!r}"
    if c == "Snuff Bottles":
        if PORCELAIN_RE.search(m):
            return "陶瓷", "Snuff Bottles + 瓷质"
        if JADE_RE.search(m):
            return "玉器", "Snuff Bottles + 玉质"
        return None, f"Snuff Bottles 但材质判不出：{m[:44]!r}"
    if c == "Hardstone":
        if JADE_RE.search(m):
            return "玉器", "Hardstone + 玉质"
        return None, f"Hardstone 非玉质（本库玉器不含玛瑙水晶等其他硬石）：{m[:44]!r}"
    return None, f"classification={c!r} 无对应 kind"


# ── 部件去重：大都会用字母后缀（"a, b"／单字母 "b"），不是 CMA 的点分段 ──────
_LETTER_SUFFIX = re.compile(r"^(?P<base>.+\d)(?P<suf>[a-zA-Z])(,\s*[a-zA-Z])*\s*$")


def is_component_met(acc):
    """藏品号以（单个或并列的）字母结尾者，是同一实物的部件。

    实测：objectID 70728「1981.369.1a」与 70729「1981.369.1b」是两个独立
    objectID、同题「Rank Badge with Lion of Asia」——若不查，一件补服就立两条。
    返回 (是否部件形态, 去掉字母后缀的 base)。「是否部件形态」只影响是否需要
    查重，base 本身也用作**首见去重键**——纯数字藏品号自身撞号时同样要挡。
    """
    m = _LETTER_SUFFIX.match(str(acc or "").strip())
    if m:
        return True, m.group("base")
    return False, str(acc or "").strip()


def existing_met():
    """扫 data/works/ 建已有大都会 objectID 集合：met_id 字段 + objectURL 里出现过的号。"""
    ids, objids = set(), set()
    url_pat = re.compile(r"metmuseum\.org/art/collection/search/(\d+)")
    for f in (WIKI / "data" / "works").glob("*.json"):
        try:
            txt = f.read_text(encoding="utf-8")
            d = json.loads(txt)
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("id"):
            ids.add(d["id"])
        if d.get("met_id"):
            try:
                objids.add(int(d["met_id"]))
            except (TypeError, ValueError):
                pass
        for mm in url_pat.finditer(txt):
            objids.add(int(mm.group(1)))
    return ids, objids


def fetch_image(o, wid):
    """按 fetchimg.from_met 的同等写法取图，但不重复请求 /objects/{id}——
    该 JSON 我们已经拿到手了，直接复用，省一次网络往返。"""
    url = o.get("primaryImage") or o.get("primaryImageSmall")
    if not url:
        return None
    thumb, size = fi._save(o.get("primaryImageSmall") or url, wid)
    w, h = fi._jpeg_size(fi.IMGDIR / pathlib.Path(thumb).name)
    return {
        "source": "met",
        "source_url": o.get("objectURL")
                     or f"https://www.metmuseum.org/art/collection/search/{o.get('objectID')}",
        "credit": (o.get("creditLine") or "The Metropolitan Museum of Art")[:110],
        "license": "PD",
        "thumb": thumb,
        "full": url,
        "w": w, "h": h,
        "alt": o.get("title") or "",
    }, size


def _txt(v):
    """归一馆方字段文本：去首尾空白，把 \\r\\n／\\n 压成「 / 」。

    实测字段陷阱：大都会 dimensions 字段常见嵌入 \\r\\n（如一件带铭文座的造像，
    「像身」「刻铭座」两截尺寸分行给出），原样入库会把裸控制符写进 JSON 字符串，
    渲染层未必按此断行，故统一压平，不改动文字内容本身。
    """
    s = str(v or "").strip()
    return re.sub(r"\r\n|\r|\n", " / ", s)


def build_record(o, seen_acc):
    """把一件大都会记录转成著录级条目，或返回跳过原因。返回 (record|None, why|None, base_acc)。"""
    if not fi._is_chinese(o):
        return None, (f"产地非中国：culture={o.get('culture')!r} dynasty={o.get('dynasty')!r} "
                      f"period={o.get('period')!r} country={o.get('country')!r}"), None

    acc = _txt(o.get("accessionNumber") or o.get("objectID") or "")
    is_part, base = is_component_met(acc)
    if is_part and base in seen_acc:
        return None, f"部件（母记录 {base} 已摄）", base

    hay = " ".join(_txt(o.get(k)) for k in ("dynasty", "period", "reign"))
    pid, why_p = ing.pick_period(hay, o.get("objectBeginDate"), o.get("objectEndDate"))
    if not pid:
        return None, f"分期判不出：{why_p}", base

    kind, why_k = pick_kind_met(o.get("classification"), o.get("medium"), o.get("objectName"))
    if not kind:
        return None, f"器类判不出：{why_k}", base

    oid = o.get("objectID")
    wid = f"met-{oid}"
    title_en = _txt(o.get("title")) or "（馆方未题名）"
    # 大都会不给中文原题（不同于克利夫兰的 title_in_original_language），
    # 著录级不作翻译——翻译本身是判断，不是照录。
    title = f"{title_en}（{acc}）" if acc else title_en
    is_pd = bool(o.get("isPublicDomain"))
    origin = " ".join(x for x in (_txt(o.get("geographyType")), _txt(o.get("country")),
                                  _txt(o.get("region"))) if x)

    d = {
        "schema": "work/1", "id": wid, "depth": "record",
        "title": title,
        "title_orig": title_en,
        "kind": kind, "period": pid,
        "holder": f"大都会艺术博物馆（藏品号 {acc}）" if acc else "大都会艺术博物馆",
        "met_id": oid,
        "year": _txt(o.get("objectDate")),
        "medium": _txt(o.get("medium")),
        "size": _txt(o.get("dimensions")),
        "one_line": (f"大都会艺术博物馆藏{kind}，馆方断代作「{_txt(o.get('objectDate')) or '未载'}」。"
                     f"**本条为著录级**：照录馆方记录，未作阐释。"),
        "verification": "museum",
        "updated": "2026-08-05",
        "sources": [{"ref": "met", "note": f"馆方藏品记录，objectID {oid}，藏品号 {acc}"}],
        "sections": {
            "basics": [
                {"t": "kv", "rows": [r for r in [
                    ["馆方原题", title_en],
                    ["馆方文化断代", _txt(o.get("culture"))],
                    ["馆方朝代", _txt(o.get("dynasty"))],
                    ["馆方时期", _txt(o.get("period"))],
                    ["馆方年号", _txt(o.get("reign"))],
                    ["馆方年代", _txt(o.get("objectDate"))],
                    ["馆方器类", _txt(o.get("classification"))],
                    ["馆方器名", _txt(o.get("objectName"))],
                    ["馆方材质／技法", _txt(o.get("medium"))],
                    ["馆方尺寸", _txt(o.get("dimensions"))],
                    ["馆方产地", origin],
                    ["馆方出土信息", _txt(o.get("excavation"))],
                    ["馆方作者", _txt(o.get("artistDisplayName"))],
                    ["馆方入藏说明", _txt(o.get("creditLine"))],
                    ["馆方藏品号", acc],
                    ["馆方公版状态", "是" if is_pd else "否"],
                ] if r[1]]},
                {"t": "p", "text": f"馆方藏品页：{o.get('objectURL') or ''}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**以上各项照录大都会艺术博物馆开放数据 API 的"
                          "藏品记录，字段名标明「馆方」者即其原文。本库尚未对其作独立核校，"
                          "亦未就断代、归属、风格作任何判断——"
                          f"分期归入依据仅为：{why_p}；器类归入依据仅为：{why_k}。")},
            ],
            "dating": [
                {"t": "gap",
                 "text": ("馆方给出了年代，**但未在公开记录中说明这个年代依据什么**——"
                          "是铭文、器形序列、共出器物还是风格比对，无从得知。"
                          "本库的断代十档因此无法填写：填任何一档都是替馆方作了它没作的声明。")},
            ],
        },
    }
    if is_pd:
        d["image_status"] = "pending"   # 摄入阶段占位；apply 时若取到图会被覆盖
    else:
        d["image_status"] = "copyright"
    return d, None, base


def run(target=800, scan_cap=20000, apply=False, gap=GAP):
    ids, objids = existing_met()
    print(f"既有库中已引用的大都会 objectID：{len(objids)} 个（met_id 字段 + objectURL 提取）\n")

    search = ing._get(f"{MET_API}/search?departmentId={MET_DEPT}&q=China")
    all_ids = search.get("objectIDs") or []
    print(f"检索命中 {len(all_ids)} 件（departmentId=6, q=China）\n")

    made = []
    skip_why = collections.Counter()
    kinds, periods = collections.Counter(), collections.Counter()
    pd_count = 0
    img_count = 0
    img_license = collections.Counter()
    seen_acc = set()
    scanned = 0
    api_fail = 0

    for oid in all_ids:
        if len(made) >= target or scanned >= scan_cap:
            break
        if oid in objids:
            skip_why["已有条目引用该 objectID"] += 1
            continue
        scanned += 1
        try:
            o = ing._get(f"{MET_API}/objects/{oid}")
        except Exception as ex:
            skip_why[f"API 请求失败（{type(ex).__name__}）"] += 1
            api_fail += 1
            time.sleep(gap)
            continue

        d, why, base = build_record(o, seen_acc)
        if not d:
            skip_why[re.sub(r"[：:].*", "", why)] += 1
            time.sleep(gap)
            continue
        if d["id"] in ids:
            skip_why["id 重复"] += 1
            time.sleep(gap)
            continue

        is_pd = bool(o.get("isPublicDomain"))
        if is_pd:
            pd_count += 1
            if apply:
                try:
                    got = fetch_image(o, d["id"])
                except Exception:
                    got = None
                if got:
                    img, _size = got
                    d["image"] = img
                    d.pop("image_status", None)
                    img_count += 1
                    img_license[img["license"]] += 1
                else:
                    d["image_status"] = "no-free-image"

        ids.add(d["id"])
        objids.add(oid)
        seen_acc.add(base)
        made.append(d)
        kinds[d["kind"]] += 1
        periods[d["period"]] += 1
        time.sleep(gap)

    print(f"扫描 {scanned} 件（含 API 失败 {api_fail} 件）→ 摄入 {len(made)} 件\n")
    print("跳过原因分布：")
    for k, v in skip_why.most_common():
        print(f"   {v:>5}  {k}")
    print("\n器类分布：")
    for k, v in kinds.most_common():
        print(f"   {v:>5}  {k}")
    print("\n分期分布：")
    for k, v in periods.most_common():
        print(f"   {v:>5}  {k}")
    print(f"\n公版（isPublicDomain=true）：{pd_count} / {len(made)}"
          f"（{pd_count * 100 // max(len(made), 1)}%）")
    if apply:
        print(f"实取图像：{img_count} 张；许可分布：{dict(img_license)}")
    print(f"\n{UA_NOTE}")

    if apply:
        for d in made:
            path = WIKI / "data" / "works" / f'{d["id"]}.json'
            path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写入 data/works/（{len(made)} 个文件）")
    else:
        print("\n（未写入。加 --apply 才落盘）")
        if made:
            print("\n样例：")
            print(json.dumps(made[0], ensure_ascii=False, indent=2))
    return made


def main():
    ap = argparse.ArgumentParser(description="大都会中国藏品 → 著录级条目批量摄入")
    ap.add_argument("--limit", type=int, default=30, help="目标摄入件数")
    ap.add_argument("--scan-cap", type=int, default=20000, help="最多扫描的候选件数上限")
    ap.add_argument("--apply", action="store_true", help="落盘并实取图像；默认只试运行")
    ap.add_argument("--gap", type=float, default=GAP, help="每次请求间隔秒数")
    a = ap.parse_args()
    run(target=a.limit, scan_cap=a.scan_cap, apply=a.apply, gap=a.gap)


if __name__ == "__main__":
    main()

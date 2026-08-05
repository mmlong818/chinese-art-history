"""批量摄入芝加哥艺术博物馆（Art Institute of Chicago）中国藏品为著录级条目。

    python ingest_artic.py survey [--pool 2000]              # 只统计：过滤是否生效、可摄入率、跳过分布
    python ingest_artic.py run --limit 450 [--apply] [--no-images]

—— 这个工具复用了什么，不重造什么 ——

分期判断（朝代名词边界匹配 + 数值年代校验）直接复用 `ingest.py` 的 `pick_period()`——
它已在克利夫兰数据上验证过「Qing 必须排在 Qin 之前」这条教训，芝加哥的
`date_display`（如 "Qing dynasty (1644-1911)"）与克利夫兰的 `culture` 字段是同构的
自由文本，同一套正则直接适用，没有理由另写一遍再踩一次同形状的坑。

递藏与展览史转块直接复用 `museum.py` 的 `artic_blocks()`——它已经处理好了
「provenance_text 是整段自由文本，不代它切分」这条规则，搜索端点返回的字段
与 `museum.py` 里 `ARTIC_FIELDS` 取的是同一批字段，可以原样喂给它。

只有器类判断（`pick_kind`）是本文件独有的：克利夫兰的 `type` 字段本身就分得较细
（Ceramic/Jade/Lacquer 直接可用），芝加哥的 `artwork_type_title` 粗得多
（一个 "Vessel" 底下混着瓷、漆、玉、青铜），必须另外合判 `medium_display` 与
`classification_titles` 才能定，所以在这里单写。

—— 产地过滤：芝加哥这条路唯一没有先例的地方 ——

`?query[term][place_of_origin]=China` 实测返回 0（连 Japan 也 0——语法本身不对，
不是「恰好没有」）。逐一试过的写法与结果：

    query[term][place_of_origin]=China              → 0（错，Japan 也 0，语法无效）
    query[match][place_of_origin]=China              → 3872（有效，但 match 是分词匹配，
                                                        对 Narnia 也正确返回 0，可用但不够精确）
    query[term][place_of_origin.keyword]=China       → 3869（有效，且更精确——.keyword 取
                                                        未分词的原始字符串，逐条核对 100 件
                                                        样本 place_of_origin 全等于 'China'）
    query[bool][filter][term][place_of_origin]=China → 0（同第一种，bool.filter 不救语法错）

**采用 `query[term][place_of_origin.keyword]=China`**，对照查验：place_of_origin.keyword=Narnia
→ 0；本文件 `_control_check()` 在 `survey`/`run` 开跑前都会先自动做这次查验，
不为 0 就直接 `sys.exit`，不产出任何条目。

—— 深度分页限制：文档写的 10,000，实测卡在 1,000 ——

`GET .../search?...&limit=100&page=11`（offset=1000）返回 **403**：
`{"status":403,"error":"Invalid number of results","detail":"You have requested too
many results. Please refine your parameters."}`——与官方文档「10,000」的说法不符，
反复核实排除了限流因素（单次冷启动请求、无并发，依旧 403）。
**规避办法：不用 offset 分页，改用 id 游标**——按 `sort=id` 排序，每页取完后
以本页最后一个 id 作 `query[bool][must][...][range][id][gt]=<游标>`，下一页永远从
该游标之后的第一条起算，offset 恒为 0，不受这个 1,000 的硬顶约束。
"""

import argparse
import collections
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WIKI = Path(__file__).parent
sys.path.insert(0, str(WIKI))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from ingest import pick_period            # noqa: E402  分期判断——原样复用，不重写
from museum import artic_blocks           # noqa: E402  递藏/展览史转块——原样复用

ARTIC_API = "https://api.artic.edu/api/v1/artworks"
UA = "china-art-history-archive/0.1 (record-level ingest; contact via repo)"
GAP = 1.5   # 秒。串行，任何两次请求（含取图）之间都不少于这个间隔

STATIC_IMG = WIKI / "static" / "img"

FIELDS = ("id,title,artist_display,date_display,date_start,date_end,place_of_origin,"
          "artwork_type_title,medium_display,classification_titles,dimensions,"
          "credit_line,main_reference_number,provenance_text,exhibition_history,"
          "publication_history,is_public_domain,image_id,copyright_notice")


def _get(url, tries=5):
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


def _get_raw(url, tries=5):
    delay = 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _search(cursor, limit=100):
    u = (f"{ARTIC_API}/search"
         f"?query[bool][must][0][term][place_of_origin.keyword]=China"
         f"&query[bool][must][1][range][id][gt]={cursor}"
         f"&sort=id&limit={limit}&fields={urllib.parse.quote(FIELDS, safe=',')}")
    return _get(u)


def _control_check():
    """对照查验：用绝不存在的产地值查，必须为 0，否则说明过滤没生效。"""
    u = (f"{ARTIC_API}/search"
         f"?query[bool][must][0][term][place_of_origin.keyword]=Narnia"
         f"&query[bool][must][1][range][id][gt]=0"
         f"&sort=id&limit=1&fields=id")
    total = _get(u)["pagination"]["total"]
    if total != 0:
        sys.exit(f"对照查验失败：place_of_origin.keyword=Narnia 应返回 0，实得 {total}——"
                  f"过滤未生效，拒绝摄入")
    print(f"对照查验通过：place_of_origin.keyword=Narnia → 0（过滤生效）")


def iter_china_items():
    """按 id 游标遍历全部产地为 China 的藏品，绕开 1,000 条的深度分页顶。"""
    cursor = 0
    while True:
        d = _search(cursor)
        data = d["data"]
        if not data:
            return
        yield from data
        cursor = data[-1]["id"]
        time.sleep(GAP)


# ── 器类判断：芝加哥独有——type 粗，须合判 medium 与 classification ─────────

BRONZE = re.compile(r"bronze", re.I)
GOLDSILVER = re.compile(r"\bgold\b|\bsilver\b|gilt", re.I)
WOOD = re.compile(r"\bwood\b", re.I)
CERAMIC = re.compile(r"porcelain|stoneware|earthenware|terracotta", re.I)
CERAMIC_CLS = {"porcelain", "stoneware", "earthenware", "ceramics", "terracotta"}
LACQUER = re.compile(r"lacquer", re.I)
# 用词边界防「stoneware」被当作「stone」——瓷器与石雕材质字段常同页出现「stone」子串。
STONE = re.compile(r"\bstone\b|\bmarble\b|\blimestone\b|\bsandstone\b|\bchlorite\b|\bgranite\b", re.I)
JADE = re.compile(r"\bjade\b|nephrite", re.I)
# 只认 cloisonne/champleve——「enamel」单独出现时多半是瓷器釉上彩的描述
# （如 "Porcelain painted in overglaze famille rose enamels"），那是瓷器不是珐琅器；
# 实测若不加限定，159 件粉彩瓷会被错判成珐琅——与本项目「Qin/Qing」同一形状的坑。
ENAMEL = re.compile(r"cloisonn|champlev", re.I)
IVORY_HORN = re.compile(r"\bivory\b|rhinoceros horn|\bhorn\b|\bbamboo\b", re.I)
TEXTILE_MEDIUM = re.compile(r"silk|embroider|weave|gauze|satin|tapestry|damask", re.I)

# 这些 artwork_type_title 本身太粗，必须配合材质才能定 kind——与 CMA 的
# Metalwork/Sculpture/Jewelry 同一处理方式，只是芝加哥这边粗类更多。
MATERIAL_TYPES = ("Vessel", "Metalwork", "Sculpture", "Equipment", "Funerary Object",
                  "Religious/Ritual Object", "Architectural fragment",
                  "Decorative Arts", "Furnishings")

# 明确无对应 kind 的类型，写下来避免下一个人以为是漏判：
#   Arms        兵器——本库无此 kind
#   Photograph  摄影——本库无此 kind（且与「绘画」在 classification 里常混标，不可据此转正）
NO_KIND_TYPES = {"Arms", "Photograph"}


def pick_kind(type_title, medium, classes):
    """返回 (kind, 理由) 或 (None, 跳过原因)。判不出就返回 None——不拿最常见的当默认。"""
    m = str(medium or "")
    cls = set(str(c).lower() for c in (classes or []))
    t = type_title or ""

    if "rubbing" in cls:
        # 芝加哥把碑刻拓片归在 Print 底下，但拓片是纸上墨拓，既不是本库「版画」
        # （年画/书籍插图一类木刻），也不是碑刻原石本身——本库暂无对应 kind。
        return None, "分类含 rubbing（拓片），非版画亦非碑刻原石，本库暂无对应 kind"
    if t in NO_KIND_TYPES:
        return None, f"{t} 无对应 kind"

    if t == "Painting" or ("painting" in cls and re.search(
            r"ink|colou?r on (silk|paper)|scroll|album leaf", m, re.I)):
        return "画作", f"type={t} + medium 含绘画材料"
    if t == "Calligraphy" or "calligraphy" in cls:
        return "书法", f"type={t}/classification 含 calligraphy"
    if t == "Print" or "print" in cls:
        return "版画", f"type={t}"

    if t in ("Textile", "Costume and Accessories") or ("textile" in cls or "weaving" in cls):
        if TEXTILE_MEDIUM.search(m):
            return "织绣", f"type={t} + medium 含织物材料"
        return None, f"{t} 但材质判不出织绣：{m[:44]!r}"

    if t == "Furniture":
        return "家具", "type=Furniture"
    if t == "Ceramics":
        return "陶瓷", "type=Ceramics"

    if t in MATERIAL_TYPES:
        # 「归类含 sculpture」才按雕塑向（铜造像/石雕/木雕）判，否则按器物向——
        # 与 CMA pick_kind 的 Sculpture 分支同一约定，判断的是形制不是芝加哥的粗类目。
        is_sculptural = "sculpture" in cls or t == "Sculpture"
        if ENAMEL.search(m):
            return "珐琅", f"{t} + cloisonne/champleve"
        if BRONZE.search(m):
            return ("铜造像" if is_sculptural else "青铜"), \
                   f"{t} + bronze（{'雕塑' if is_sculptural else '器物'}向）"
        if GOLDSILVER.search(m):
            return "金银器", f"{t} + gold/silver/gilt"
        # 只认「;」前的主材质段——"Purple amethyst; jade stopper" 主料是紫晶，
        # 玉只是塞子，若对全字符串搜 jade 会把紫晶瓶误判成玉器（实测样本 17976）。
        if JADE.search(m.split(";")[0]):
            return "玉器", f"{t} + 主材质段含 jade/nephrite"
        if CERAMIC.search(m) or (CERAMIC_CLS & cls):
            if is_sculptural:
                # 陶质俑像一类——本库雕塑组只有石雕/木雕/铜造像三档，没有「陶塑」，
                # 硬塞进任何一档都是编造材质，宁可跳过。
                return None, f"{t}+陶质但归类含 sculpture——本库雕塑组无陶塑一档，判不出"
            return "陶瓷", f"{t} + 材质/分类含瓷器"
        if LACQUER.search(m):
            return "漆器", f"{t} + lacquer"
        if IVORY_HORN.search(m):
            return "竹木牙角", f"{t} + ivory/horn/bamboo"
        if STONE.search(m):
            if is_sculptural:
                return "石雕", f"{t} + stone（雕塑向）"
            return None, f"{t}+石质但未归类 sculpture，判不出属石雕抑或其他"
        if WOOD.search(m):
            if is_sculptural:
                return "木雕", f"{t} + wood（雕塑向）"
            return None, f"{t}+木质但未归类 sculpture，判不出属木雕抑或家具"
        return None, f"{t} 材质判不出：{m[:44]!r}"

    return None, f"type={t!r} 无对应 kind"


# ── 落盘去重 ────────────────────────────────────────────────────────────────


def existing_ids():
    ids = set()
    for f in (WIKI / "data").glob("*/*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("id"):
            ids.add(d["id"])
    return ids


# ── 取图：is_public_domain 为真者可取，w/h 从落盘文件直读 ──────────────────


def _jpeg_size(path):
    """从落盘 JPEG 里读真实宽高，不引第三方库（与 fetchimg.py 同一份逻辑，
    本文件自包含一份而不去 import fetchimg，避免牵扯它当前可能被并行进程占用）。"""
    try:
        b = path.read_bytes()
    except Exception:
        return 0, 0
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    i = 2
    while i < len(b) - 9:
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return (int.from_bytes(b[i + 7:i + 9], "big"),
                    int.from_bytes(b[i + 5:i + 7], "big"))
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    return 0, 0


def fetch_image(image_id, oid, wid, credit):
    """下载 IIIF 843px 图，落盘后从文件读真实宽高。失败返回 None（不编造）。"""
    url = f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
    data = _get_raw(url)
    STATIC_IMG.mkdir(parents=True, exist_ok=True)
    path = STATIC_IMG / f"{wid}-843.jpg"
    path.write_bytes(data)
    w, h = _jpeg_size(path)
    if not (w and h):
        path.unlink(missing_ok=True)
        return None
    return {
        "source": "other",   # schema.py 的 IMAGE_SOURCES 白名单里没有专属的 artic 项，
                              # 不改 schema.py，就地用「other」，source_url 指回原页。
        "source_url": f"https://www.artic.edu/artworks/{oid}",
        "credit": (credit or "Art Institute of Chicago")[:110],
        "license": "PD",
        "thumb": f"img/{path.name}",
        "full": f"https://www.artic.edu/iiif/2/{image_id}/full/full/0/default.jpg",
        "w": w, "h": h,
    }


# ── 建条目 ────────────────────────────────────────────────────────────────


def build_record(o):
    """把一件芝加哥藏品记录转成著录级条目，或返回 (None, 跳过原因)。"""
    origin = str(o.get("place_of_origin") or "").strip()
    if origin != "China":
        return None, f"产地非确切 China：{origin[:30]!r}"

    pid, why_p = pick_period(o.get("date_display"), o.get("date_start"), o.get("date_end"))
    if not pid:
        return None, why_p

    kind, why_k = pick_kind(o.get("artwork_type_title"), o.get("medium_display"),
                             o.get("classification_titles"))
    if not kind:
        return None, why_k

    oid = o["id"]
    wid = f"artic-{oid}"
    acc = str(o.get("main_reference_number") or oid)
    en = str(o.get("title") or "").strip() or "（馆方未题名）"
    title = f"{en}（{acc}）"
    artist = str(o.get("artist_display") or "").replace("\n", "；").strip()
    meas = str(o.get("dimensions") or "").strip()
    date_disp = re.sub(r"\s*\n\s*", "，", str(o.get("date_display") or "")).strip()
    url = f"https://www.artic.edu/artworks/{oid}"

    d = {
        "schema": "work/1", "id": wid, "depth": "record",
        "title": title,
        "kind": kind, "period": pid,
        "holder": f"芝加哥艺术博物馆（藏品号 {acc}）",
        "year": date_disp,
        "size": meas,
        "medium": str(o.get("medium_display") or "").strip(),
        "one_line": (f"芝加哥艺术博物馆藏{kind}，馆方断代作「{date_disp or '未载'}」。"
                     f"**本条为著录级**：照录馆方记录，未作阐释。"),
        "verification": "museum",
        "updated": "2026-08-05",
        "sources": [{"ref": "artic", "note": f"馆方藏品记录，藏品号 {acc}"}],
        "sections": {
            "basics": [
                {"t": "kv", "rows": [r for r in [
                    ["馆方原题", en],
                    ["创作者（馆方记）", artist],
                    ["馆方文化断代", date_disp],
                    ["馆方器类", str(o.get("artwork_type_title") or "")],
                    ["材质／技法", str(o.get("medium_display") or "")],
                    ["尺寸", meas],
                    ["入藏说明", str(o.get("credit_line") or "")],
                    ["产地（馆方记）", origin],
                    ["藏品号", acc],
                ] if r[1]]},
                {"t": "p", "text": f"馆方藏品页：{url}"},
                {"t": "stmt", "state": "pend",
                 "text": ("**本条为著录级条目。**以上各项照录芝加哥艺术博物馆的藏品记录，"
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

    archive = artic_blocks(o, src="artic")
    if archive:
        d["sections"]["provenance"] = archive

    return d, None


def run(limit=50, apply=False, take_images=True, pool_cap=8000):
    _control_check()
    ids = existing_ids()
    made, skipped = [], collections.Counter()
    n_scanned = 0
    for o in iter_china_items():
        n_scanned += 1
        if n_scanned > pool_cap or len(made) >= limit:
            break
        wid = f"artic-{o['id']}"
        if wid in ids:
            skipped["已有条目"] += 1
            continue
        d, why = build_record(o)
        if not d:
            skipped[re.sub(r"[：:].*", "", why)] += 1
            continue
        ids.add(wid)
        img_note = "无图（is_public_domain=false）"
        if not apply:
            # 干跑不落地任何文件——包括图像。取图与写 JSON 一样只在 --apply 时发生，
            # 否则 --dry 预览也会在磁盘上留下真实下载的图片，那不是「干跑」了。
            if take_images and o.get("is_public_domain") and o.get("image_id"):
                img_note = "（干跑，未取图；--apply 时将下载）"
            elif o.get("is_public_domain"):
                img_note = "公版但无 image_id（--apply 时标 no-free-image）"
            else:
                img_note = "非公版（--apply 时标 copyright）"
        elif take_images and o.get("is_public_domain") and o.get("image_id"):
            try:
                img = fetch_image(o["image_id"], o["id"], wid, o.get("credit_line"))
                if img:
                    d["image"] = img
                    img_note = f"取图 {img['w']}×{img['h']}"
                else:
                    d["image_status"] = "pending"
                    img_note = "取图失败（文件无法解出宽高），标 pending"
                time.sleep(GAP)
            except Exception as ex:
                d["image_status"] = "pending"
                img_note = f"取图异常 {type(ex).__name__}，标 pending"
        elif o.get("is_public_domain"):
            d["image_status"] = "no-free-image"
            img_note = "公版但无 image_id"
        else:
            d["image_status"] = "copyright"
        made.append(d)
        print(f"  [{len(made):>4}/{limit}] {wid}  {d['kind']:4} {d['period']:20}  {img_note}")

    print(f"\n扫描 {n_scanned} 件（含已跳过与已有）→ 生成 {len(made)} 条著录级条目；"
          f"跳过 {sum(skipped.values())} 件")
    for k, v in skipped.most_common(12):
        print(f"   {v:>4}  {k}")

    kinds, periods = collections.Counter(d["kind"] for d in made), collections.Counter(d["period"] for d in made)
    n_img = sum(1 for d in made if d.get("image"))
    n_prov = sum(1 for d in made if any(b.get("t") == "prov"
                 for b in d.get("sections", {}).get("provenance", [])))
    n_exhib = sum(1 for d in made if any(b.get("t") == "exhib"
                  for b in d.get("sections", {}).get("provenance", [])))
    print(f"\n器类分布:")
    for k, v in kinds.most_common():
        print(f"   {v:>4}  {k}")
    print(f"\n分期分布:")
    for k, v in periods.most_common():
        print(f"   {v:>4}  {k}")
    print(f"\n取图 {n_img} 件 · 挂 provenance 块 {n_prov} 件 · 挂 exhibition 块 {n_exhib} 件")

    if apply:
        out_dir = WIKI / "data" / "works"
        for d in made:
            (out_dir / f'{d["id"]}.json').write_text(
                json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写入 data/works/（{len(made)} 个文件）")
    else:
        print("\n（未写入。加 --apply 才落盘）")
        if made:
            print("样例：")
            print(json.dumps(made[0], ensure_ascii=False, indent=2)[:1200])
    return made


def survey(pool=2000):
    _control_check()
    ids = existing_ids()
    n = 0
    skip_why = collections.Counter()
    kinds, periods = collections.Counter(), collections.Counter()
    pd_count = 0
    for o in iter_china_items():
        n += 1
        if n > pool:
            break
        if f"artic-{o['id']}" in ids:
            skip_why["已有条目"] += 1
            continue
        if o.get("is_public_domain"):
            pd_count += 1
        d, why = build_record(o)
        if not d:
            skip_why[re.sub(r"[：:].*", "", why)] += 1
            continue
        kinds[d["kind"]] += 1
        periods[d["period"]] += 1
    print(f"\n抽查 {n} 件 → 可摄入 {sum(kinds.values())} 件（{sum(kinds.values())*100//max(n,1)}%）"
          f" · 其中公版（is_public_domain）{pd_count} 件\n")
    print("跳过原因:")
    for k, v in skip_why.most_common(15):
        print(f"   {v:>4}  {k}")
    print("\n可摄入者的器类分布:")
    for k, v in kinds.most_common():
        print(f"   {v:>4}  {k}")
    print("\n可摄入者的分期分布:")
    for k, v in periods.most_common():
        print(f"   {v:>4}  {k}")


def main():
    ap = argparse.ArgumentParser(description="批量摄入芝加哥艺术博物馆中国藏品（判不出就不摄入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey")
    s.add_argument("--pool", type=int, default=2000)
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--apply", action="store_true")
    r.add_argument("--no-images", action="store_true")
    r.add_argument("--pool-cap", type=int, default=8000)
    a = ap.parse_args()
    if a.cmd == "survey":
        survey(a.pool)
    else:
        run(a.limit, a.apply, take_images=not a.no_images, pool_cap=a.pool_cap)


if __name__ == "__main__":
    main()

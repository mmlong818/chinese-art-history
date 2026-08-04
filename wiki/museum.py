"""馆方深层档案采集：展览史、递藏、著录、题识、策展文字。

    python museum.py scan                        # 哪些条目可挂接，已挂了没有
    python museum.py show 1957.40                # 打印该件的全部深层字段，供人工过目
    python museum.py write 1957.40 ru-yao-cma    # 写入条目的「来源与流转」维度
    python museum.py write-all                   # 扫全部可挂接条目，串行写

—— 为什么单独做一个工具，不塞进 fetchimg.py ——

`fetchimg.py` 管的是图像与判权，一次调用对应一张图。这里管的是**档案文本**，
一次调用对应一件物的整段来历。两者的失败方式也不同：取图失败是「没有图」，
取档案失败是「不知道来历」，后者绝不能用占位值填上。分开才不会互相牵连。

—— 一个必须先讲清的不对称，比图像那个更极端 ——

    克利夫兰美术馆   exhibitions／provenance／citations／inscriptions／description
                    全部走 API，CC0，61 个字段。**这条路可编程。**
    大都会博物馆     57 个字段里**一个都没有**（实测 2026-08-04：无 exhibition、
                    无 provenance、无 citation、无 inscription、无 description）。
                    它的网页有这几栏，API 不给——只能抓 HTML。

**此处原写「国内各馆无 API」，是错的**（2026-08-04 普查复测推翻）：
南京博物院 `njmuseum.com/api/exhibition/list` 实测返回 12 KB JSON，
含展览名、展厅位置与会期，无需鉴权。它是该馆网页自用的公开接口，
读它等同于读其公开页面；但**无文档、随时可变**，不可当稳定依赖。
另经复测可编程取到的还有芝加哥艺术博物馆（`provenance_text`／`exhibition_history`，
无需 key）与史密森尼国立亚洲艺术博物馆（递藏逐条带日期与档案编号，全 CC0，须申请 key，
字段详尽度胜过克利夫兰）。逐家实测细节见 `MUSEUMS.md`。

**已接两家**：克利夫兰（`scan`/`show`/`write`/`write-all`）与芝加哥
（`artic-find`/`artic-show`/`artic-write`，实测无需 API key）。
史密森尼数据质量最好（递藏逐条带日期与档案编号、全 CC0），**但卡在一把免费
API 密钥上**：DEMO_KEY 为共享演示键，实测连续 429，配额已被用光；
正式密钥须以邮箱在 api.data.gov 注册，非本工具能自行取得。待密钥到手再接。

—— 展览史为什么是一等证据 ——

一件物参加过哪些展览、何时何地、谁主办，是有年月、有图录可查的外部记录，
证据强度远高于本库承认的最弱一档「风格比对」。而且展览史就是接受史：
1935 年伦敦中国艺术国际展览会挑走的那批东西，此后八十年一直被当作中国艺术的
代表作——**「名作」名单有相当部分是展览造出来的，不是自古如此。**
"""

import argparse
import glob
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


DATA = Path(__file__).parent / "data"
CMA_API = "https://openaccess-api.clevelandart.org/api/artworks"
ARTIC_API = "https://api.artic.edu/api/v1/artworks"
ARTIC_FIELDS = ("id,title,artist_display,date_display,place_of_origin,medium_display,"
                "dimensions,credit_line,provenance_text,exhibition_history,"
                "publication_history,is_public_domain,department_title,inscriptions")
UA = "china-art-history-archive/0.1 (archival metadata; contact via repo)"
GAP = 1.5   # 秒。串行，宁可慢——并发把核查通道压死过一次，不再犯

TAG = re.compile(r"<[^>]+>")


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


def _clean(s):
    """去 HTML 标签并归一空白。克利夫兰的展览 description 里带 <i> 斜体标记。"""
    return re.sub(r"\s+", " ", TAG.sub("", str(s or ""))).strip()


def fetch(acc):
    """按入藏号取单件。克利夫兰的 accession_number 可直接作路径。"""
    return _get(f"{CMA_API}/{urllib.parse.quote(str(acc))}")["data"]


def _exhibitions(d):
    """展览史。克利夫兰把展览分 current／past 两键，且实测 current 里也装历史展览，
    所以两键都收再按开幕日排序去重——只读一键会漏掉大半。"""
    seen, out = set(), []
    ex = d.get("exhibitions") or {}
    for key in ("current", "past"):
        for e in (ex.get(key) or []):
            if isinstance(e, dict) and e.get("id") not in seen:
                seen.add(e.get("id"))
                out.append(e)
    out.sort(key=lambda e: str(e.get("opening_date") or ""))
    return out


def _year(s):
    m = re.match(r"(\d{4})", str(s or ""))
    return m.group(1) if m else ""


def blocks_from(d, src="cleveland"):
    """把馆方档案转成本库的块。**空字段一律不产出块**——
    「不知道来历」必须看起来就是不知道，不能用占位值填成看起来知道。

    每个块都带上该件的藏品页 `url`：`src` 只说「出自克利夫兰」，
    读者要核这一条得自己去馆里搜。留下原始链接是署名义务的一部分
    （CC BY-SA 要求署名，「适当引用」要求指明出处），也是本库能被人复核的前提。
    """
    out = []
    url = d.get("url") or ""

    for e in _exhibitions(d):
        title = _clean(e.get("title"))
        desc = _clean(e.get("description"))
        if not title:
            continue
        # 克利夫兰的 description 以斜体标题开头，而标题已单独成栏——
        # 留着就是同一句话显示两遍。剥掉开头的标题，只留场馆与会期，那才是新信息。
        if desc.startswith(title):
            desc = desc[len(title):].lstrip(" .。·—-")
        out.append({"t": "exhib", "when": _year(e.get("opening_date")),
                    "title": title, "text": desc, "src": src, "url": url})

    for p in (d.get("provenance") or []):
        text = _clean(p.get("description"))
        when = _clean(p.get("date"))
        if not text:
            continue
        out.append({"t": "prov", "when": when or "年份不详",
                    "text": text, "src": src, "url": url})

    return out


def _inscriptions(d):
    """题识／款印。

    克利夫兰用**三个**字段记铭文：`inscription`（原文）、
    `inscription_translation`（译文）、`inscription_remark`（附注）。
    实测 1938.13（父戊尊）只有译文一项、原文为 null——
    原先只读 `inscription` 的写法把这件明明有铭的器物报成「题识 0 条」。
    **「有铭而报无铭」比报错更坏**：撰写者会据此判断该器无铭，
    而断代依据的等级恰恰系于此。三个字段都要收，并标明取的是哪一项。
    """
    ins = []
    for i in (d.get("inscriptions") or []):
        if not isinstance(i, dict):
            if _clean(i):
                ins.append(_clean(i))
            continue
        orig = _clean(i.get("inscription"))
        trans = _clean(i.get("inscription_translation"))
        remark = _clean(i.get("inscription_remark"))
        parts = []
        if orig:
            parts.append(orig)
        if trans:
            parts.append(f"［馆方译文］{trans}")
        if remark:
            parts.append(f"［馆方附注］{remark}")
        if parts:
            ins.append(" ".join(parts))
    return ins


def show(acc):
    d = fetch(acc)
    print(f"{d.get('title')}  |  {d.get('creation_date')}  |  {d.get('accession_number')}")
    print(f"{d.get('url')}")
    print(f"部门 {d.get('department')!r} · 文化 {d.get('culture')} · 判权 {d.get('share_license_status')}")
    print()

    ex = _exhibitions(d)
    print(f"── 展览史 {len(ex)} 次 " + "─" * 40)
    for e in ex:
        print(f"  {_year(e.get('opening_date')) or '????'}  {_clean(e.get('title'))}")
        if e.get("description"):
            print(f"        {_clean(e['description'])[:150]}")

    pv = d.get("provenance") or []
    print(f"\n── 递藏 {len(pv)} 段 " + "─" * 42)
    for p in pv:
        print(f"  {_clean(p.get('date')) or '????':<14} {_clean(p.get('description'))[:120]}")

    ins = _inscriptions(d)
    print(f"\n── 题识／款印 {len(ins)} 条 " + "─" * 36)
    for t in ins[:12]:
        print(f"  {t[:150]}")

    ct = d.get("citations") or []
    print(f"\n── 著录 {len(ct)} 条 " + "─" * 42)
    for c in ct[:8]:
        print(f"  {_clean(c.get('citation') if isinstance(c, dict) else c)[:150]}")
    if len(ct) > 8:
        print(f"  …另 {len(ct)-8} 条")

    print(f"\n── 策展文字 " + "─" * 44)
    for k in ("description", "did_you_know", "tombstone", "find_spot"):
        if d.get(k):
            print(f"  [{k}] {_clean(d[k])[:400]}")
    return d


def _entry_path(eid):
    for sub in ("works", "sites", "artists", "classes", "treatises", "events"):
        p = DATA / sub / f"{eid}.json"
        if p.exists():
            return p, sub
    return None, None


def scan():
    """列出图版来自克利夫兰的条目，及其是否已挂档案。"""
    rows = []
    for f in glob.glob(str(DATA / "*/*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        acc, has_ex, has_pv = None, False, False

        def walk(v):
            nonlocal acc, has_ex, has_pv
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, dict):
                if v.get("source") == "cleveland":
                    m = re.search(r"/art/([\d.]+)", str(v.get("source_url") or ""))
                    if m:
                        acc = m.group(1)
                if v.get("t") == "exhib":
                    has_ex = True
                if v.get("t") == "prov":
                    has_pv = True
                for x in v.values():
                    walk(x)

        walk(d)
        if acc:
            rows.append((d["id"], acc, has_ex, has_pv))
    rows.sort()
    print(f"克利夫兰可挂接条目 {len(rows)} 件：")
    for i, a, e, p in rows:
        mark = "".join(("展" if e else "·", "藏" if p else "·"))
        print(f"  [{mark}] {i:<38} {a}")
    todo = [r for r in rows if not (r[2] and r[3])]
    print(f"\n尚未挂接或只挂了一半：{len(todo)} 件")
    return rows


SECTION_ORDER = ("provenance", "excavation", "record", "basics")


def write(acc, eid, dry=False):
    p, kind = _entry_path(eid)
    if p is None:
        sys.exit(f"找不到条目 {eid}")
    # 只有 work 才有「一件物的来历」。类目（鬲、钟）、艺术家、遗址条目里之所以
    # 也出现克利夫兰入藏号，是因为它们借了某一件藏品作图例——
    # 把那一件的递藏与展览史挂到「鬲」这个类目上是张冠李戴。这里是正确的拒绝，
    # 不是工具做不到。
    if kind != "works":
        print(f"{eid}: 是 {kind} 条目，只借了藏品作图例，不挂该件的递藏与展览史")
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))

    # 防错挂：条目里必须真的引用了这个入藏号，才允许把该馆记录写进去。
    # 这是 L10 那条教训的同一形状——每条外部引用都要有个只有真做过才成立的凭据，
    # 否则「字段齐全、格式合法、内容却挂错了物件」不会被任何校验拦住。
    if acc not in json.dumps(d, ensure_ascii=False):
        sys.exit(f"{eid} 里没有出现入藏号 {acc}，拒绝写入——"
                 f"先确认这两者确指同一件物，不要凭 id 相似就挂")

    md = fetch(acc)
    blocks = blocks_from(md)
    if not blocks:
        print(f"{eid}: 馆方无展览史与递藏记录，不写入（这本身是事实，不是失败）")
        return 0

    secs = d.setdefault("sections", {})
    key = next((k for k in SECTION_ORDER if k in secs), None)
    if key is None:
        sys.exit(f"{eid} 无可承接的维度（找过 {SECTION_ORDER}）")

    have = {(b.get("t"), b.get("title") or b.get("text"))
            for b in secs[key] if isinstance(b, dict)}
    fresh = [b for b in blocks
             if (b["t"], b.get("title") or b.get("text")) not in have]
    if not fresh:
        print(f"{eid}: 已挂接，无新增")
        return 0

    n_ex = sum(1 for b in fresh if b["t"] == "exhib")
    n_pv = sum(1 for b in fresh if b["t"] == "prov")
    print(f"{eid} ← {acc}  展览 {n_ex} · 递藏 {n_pv}  → 维度「{key}」")
    if dry:
        return len(fresh)

    secs[key].extend(fresh)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(fresh)


def write_all(dry=False):
    rows = scan()
    print()
    total = 0
    for i, a, has_ex, has_pv in rows:
        if has_ex and has_pv:
            continue
        try:
            total += write(a, i, dry=dry)
        except SystemExit as e:
            print(f"  跳过 {i}：{e}")
        except Exception as e:
            print(f"  取失败 {i}（{a}）：{e}")
        time.sleep(GAP)
    print(f"\n共写入 {total} 个块")



# ── 芝加哥艺术博物馆：第二条可编程的档案通道 ────────────────────────────────
#
# 实测 2026-08-05：无需 API key，返回 provenance_text／exhibition_history／
# publication_history 三栏，另有 place_of_origin 可直接筛产地。
# **与克利夫兰的关键差别**：那边 provenance 是分段列表（每段带 date 与
# description），这边是**整段自由文本**。所以本库不代它切分——
# 切错比不切更坏，递藏链的分段本身就是史料判断，不是字符串处理。


def artic_fetch(oid):
    return _get(f"{ARTIC_API}/{oid}?fields={ARTIC_FIELDS}")["data"]


ARTIC_YEAR = re.compile(r"\b(1[6-9]\d{2}|20[0-2]\d)\b")


def artic_blocks(d, src="artic"):
    """转块。exhibition_history 是多行自由文本，按行切；provenance_text 不切。"""
    out = []
    url = f"https://www.artic.edu/artworks/{d.get('id')}"

    for line in str(d.get("exhibition_history") or "").splitlines():
        t = _clean(line)
        if len(t) < 8:
            continue
        m = ARTIC_YEAR.search(t)
        out.append({"t": "exhib", "when": m.group(1) if m else "",
                    "title": t[:160], "text": "" if len(t) <= 160 else t,
                    "src": src, "url": url})

    pv = _clean(d.get("provenance_text"))
    if pv:
        out.append({"t": "prov", "when": "见馆方档案原文（未分段）",
                    "text": pv, "src": src, "url": url})
    return out


def artic_show(oid):
    d = artic_fetch(oid)
    print(f"{d.get('title')}  |  {d.get('date_display')}  |  {d.get('id')}")
    print(f"https://www.artic.edu/artworks/{d.get('id')}")
    print(f"产地 {d.get('place_of_origin')!r} · 部门 {d.get('department_title')!r} · "
          f"公版 {d.get('is_public_domain')}")
    if str(d.get("place_of_origin") or "").strip().lower() != "china":
        print("  ⚠ 产地非 China——本库范围是中国美术史，取用前须自行判断是否越界")
    for k, label in (("provenance_text", "递藏（整段）"),
                     ("exhibition_history", "展览史"),
                     ("publication_history", "著录"),
                     ("inscriptions", "题识")):
        v = _clean(d.get(k))
        print(f"\n── {label} " + "─" * 44)
        print("  " + (v[:1200] if v else "—"))
    return d


def artic_write(oid, eid, dry=False):
    p, kind = _entry_path(eid)
    if p is None:
        sys.exit(f"找不到条目 {eid}")
    if kind != "works":
        print(f"{eid}: 是 {kind} 条目，不挂单件的递藏与展览史")
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    md = artic_fetch(oid)
    # 防错挂：与克利夫兰同一形状的闸——条目里必须真的出现这个馆藏号或 artic id
    if str(oid) not in json.dumps(d, ensure_ascii=False):
        sys.exit(f"{eid} 里没有出现 artic id {oid}，拒绝写入——"
                 f"先确认两者确指同一件物，不要凭 id 相似就挂")
    blocks = artic_blocks(md)
    if not blocks:
        print(f"{eid}: 芝加哥无递藏与展览史记录，不写入（这本身是事实）")
        return 0
    secs = d.setdefault("sections", {})
    key = next((k for k in SECTION_ORDER if k in secs), None)
    if key is None:
        sys.exit(f"{eid} 无可承接的维度")
    have = {(b.get("t"), b.get("title") or b.get("text"))
            for b in secs[key] if isinstance(b, dict)}
    fresh = [b for b in blocks
             if (b["t"], b.get("title") or b.get("text")) not in have]
    if not fresh:
        print(f"{eid}: 已挂接，无新增")
        return 0
    print(f"{eid} ← artic {oid}  "
          f"展览 {sum(1 for b in fresh if b['t']=='exhib')} · "
          f"递藏 {sum(1 for b in fresh if b['t']=='prov')}  → 维度「{key}」")
    if not dry:
        secs[key].extend(fresh)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(fresh)


def artic_find(q, limit=8):
    """只列**产地为中国、公版、且三栏至少有一栏有货**的件——否则挂不上东西。"""
    url = (f"{ARTIC_API}/search?q={urllib.parse.quote(q)}&limit=40"
           f"&fields={ARTIC_FIELDS}")
    data = _get(url).get("data", [])
    hits = 0
    print(f"芝加哥检索「{q}」——只列产地中国、公版、且有档案可挂的件\n")
    for o in data:
        if str(o.get("place_of_origin") or "").strip().lower() != "china":
            continue
        if not o.get("is_public_domain"):
            continue
        has = [k for k in ("provenance_text", "exhibition_history", "publication_history")
               if _clean(o.get(k))]
        if not has:
            continue
        hits += 1
        print(f"  {o['id']:>7}  {_clean(o.get('title'))[:52]}")
        print(f"           {_clean(o.get('date_display'))[:44]} · 有 {'／'.join(has)}")
        if hits >= limit:
            break
    if not hits:
        print("  无符合条件者（或该批全无档案字段）")


def main():
    ap = argparse.ArgumentParser(description="馆方深层档案采集（目前仅克利夫兰）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    s = sub.add_parser("show"); s.add_argument("acc")
    w = sub.add_parser("write"); w.add_argument("acc"); w.add_argument("eid")
    w.add_argument("--dry", action="store_true")
    wa = sub.add_parser("write-all"); wa.add_argument("--dry", action="store_true")
    af = sub.add_parser("artic-find"); af.add_argument("q")
    af.add_argument("--limit", type=int, default=8)
    ash = sub.add_parser("artic-show"); ash.add_argument("oid")
    aw = sub.add_parser("artic-write"); aw.add_argument("oid"); aw.add_argument("eid")
    aw.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if a.cmd == "scan":
        scan()
    elif a.cmd == "show":
        show(a.acc)
    elif a.cmd == "write":
        write(a.acc, a.eid, dry=a.dry)
    elif a.cmd == "artic-find":
        artic_find(a.q, a.limit)
    elif a.cmd == "artic-show":
        artic_show(a.oid)
    elif a.cmd == "artic-write":
        artic_write(a.oid, a.eid, dry=a.dry)
    else:
        write_all(dry=a.dry)


if __name__ == "__main__":
    main()

"""内容提纲：合并、校验、对账。

    python outline.py merge      # 把 plan/units-*.json 并入 data/outline.json
    python outline.py check      # 校验提纲自身（id 唯一、期与门类存在、na 须有理由…）
    python outline.py coverage   # **对账：每个格子该有什么、本库有什么、差多少**

—— 提纲是分母 ——

`canon.json` 的 17 期是时间轴，`WORK_KIND_GROUP` 是材质轴，
而提纲回答的是**「该写哪些题目」**。没有它，覆盖率算不出来、缺什么也说不清。

本库此前正是缺这一层：填充顺序由 API 可得性决定，于是库的形状变成了
开放数据计划的形状——2,482 件作品里 83.4% 出自克利夫兰、芝加哥、大都会三家，
而国内馆那 218 件里画作占 207，器物／石刻／窟寺／雕塑四组的国内来源合计 2 件。

**分母的用处不在于让数字好看，在于让「缺什么」变成可算的。**
`audit.scope()` 曾报「十七期全部有条目——本库在时间上已无空缺」，
那是拿 17 这个太粗的分母算出来的；换成提纲的单元数，同一个库立刻显出洞在哪。

—— 为什么合并要单独做一步 ——

五路代理各写一份 `plan/units-*.json`，**而不是并发追加同一个数组**。
我最初布的就是并发追加，那防不住丢写——本会话已两次撞上同类竞争
（`sources.json` 被两批同时追加、恽寿平被两批同时写，两次都是撞运气没丢东西）。
分文件写、单点合并，是把竞争条件从设计里去掉，而不是靠叮嘱。
"""

import argparse
import collections
import json
import pathlib
import sys

WIKI = pathlib.Path(__file__).parent
OUTLINE = WIKI / "data" / "outline.json"
CONF = {"确知", "记得", "待核"}
ENTITY_KEYS = ("artist", "work", "site", "class", "treatise", "event")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def load_outline():
    return json.loads(OUTLINE.read_text(encoding="utf-8"))


def save_outline(o):
    OUTLINE.write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")


def merge():
    o = load_outline()
    have = {u["id"] for u in o.get("units", [])}
    added, dup, files = 0, 0, 0
    for f in sorted((WIKI / "plan").glob("units-*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        items = data.get("items") or []
        files += 1
        n = 0
        for u in items:
            if not isinstance(u, dict) or not u.get("id"):
                continue
            if u["id"] in have:
                dup += 1
                continue
            have.add(u["id"])
            o.setdefault("units", []).append(u)
            n += 1
            added += 1
        print(f"  {f.name:<34} {len(items):>4} 项 → 并入 {n}")
    o["units"].sort(key=lambda u: (u.get("period", ""), u.get("domain", ""), u["id"]))
    save_outline(o)
    print(f"\n合并 {files} 份，新增 {added} 个单元，跳过重复 id {dup} 个；"
          f"提纲现有 {len(o.get('units', []))} 个单元")


def check():
    o = load_outline()
    canon = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    periods = {p["id"] for p in canon["periods"]}
    domains = {d["id"] for d in o["domains"]}
    errs, warns = [], []

    seen = set()
    for c in o["cells"]:
        if c["period"] not in periods:
            errs.append(f"格子的期 {c['period']!r} 不在 canon 中")
        if c["domain"] not in domains:
            errs.append(f"格子的门类 {c['domain']!r} 未定义")
        if c["status"] == "na" and not c.get("why"):
            # **不给理由的 na 不许写**——否则它与「懒得写」无从分辨。
            errs.append(f"{c['period']}×{c['domain']} 标 na 而无理由")

    cell_of = {(c["period"], c["domain"]): c["status"] for c in o["cells"]}
    per_cell = collections.Counter()
    for u in o.get("units", []):
        uid = u.get("id", "")
        if uid in seen:
            errs.append(f"单元 id 重复：{uid}")
        seen.add(uid)
        if u.get("period") not in periods:
            errs.append(f"{uid}：期 {u.get('period')!r} 不在 canon 中")
        if u.get("domain") not in domains:
            errs.append(f"{uid}：门类 {u.get('domain')!r} 未定义")
        st = cell_of.get((u.get("period"), u.get("domain")))
        if st == "na":
            # 提纲自己说这格本无可写，却又写了单元——两者必有一错，要人判。
            errs.append(f"{uid}：落在标为 na 的格子上（{u.get('period')}×{u.get('domain')}）")
        if u.get("confidence") not in CONF:
            warns.append(f"{uid}：confidence {u.get('confidence')!r} 不在 {sorted(CONF)}")
        if not u.get("basis"):
            # 追不到依据的题目，是凭印象想出来的题目。
            warns.append(f"{uid}：无 basis——追不到依据的题目不该进提纲")
        if not (u.get("expect") or {}):
            warns.append(f"{uid}：expect 为空——不声明期望条目就不成其为分母")
        per_cell[(u.get("period"), u.get("domain"))] += 1

    for (p, d), st in cell_of.items():
        if st == "core" and per_cell[(p, d)] < 4:
            warns.append(f"{p}×{d} 为 core 而仅 {per_cell[(p,d)]} 个单元")
        if st == "present" and per_cell[(p, d)] == 0:
            warns.append(f"{p}×{d} 为 present 而无单元")

    for e in errs:
        print("ERROR " + e)
    for w in warns[:40]:
        print("WARN  " + w)
    if len(warns) > 40:
        print(f"      …另 {len(warns)-40} 条 WARN")
    print(f"\n单元 {len(o.get('units', []))} · 格子 {len(o['cells'])} · "
          f"ERROR {len(errs)} · WARN {len(warns)}")
    return 1 if errs else 0


def _entities():
    idx = collections.defaultdict(dict)
    for sub, kind in (("artists", "artist"), ("works", "work"), ("sites", "site"),
                      ("classes", "class"), ("treatises", "treatise"),
                      ("events", "event")):
        d = WIKI / "data" / sub
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            e = json.loads(p.read_text(encoding="utf-8"))
            nm = e.get("name") or e.get("title") or ""
            idx[kind][nm] = (e["id"], e.get("depth") or "full")
    return idx


def coverage(limit=24):
    """对账：提纲点名的实体，本库有几个、其中几个是完整级。

    **区分「有条目」与「有完整级条目」是这份对账的要点**：
    著录级只是照录，未作判断；正典位上摆一条照录，等于用清单欺骗清单。
    """
    o = load_outline()
    idx = _entities()
    name_of = {d["id"]: d["name"] for d in o["domains"]}
    canon = json.loads((WIKI / "data" / "canon.json").read_text(encoding="utf-8"))
    pname = {p["id"]: p["name"] for p in canon["periods"]}

    tot = collections.Counter()
    rows = []
    for u in o.get("units", []):
        want = miss = rec = 0
        missing = []
        for k in ENTITY_KEYS:
            for nm in (u.get("expect") or {}).get(k, []) or []:
                want += 1
                hit = idx[k].get(nm)
                if not hit:
                    miss += 1
                    missing.append(f"{k}:{nm}")
                elif hit[1] == "record":
                    rec += 1
        tot["want"] += want
        tot["miss"] += miss
        tot["record"] += rec
        rows.append((u["id"], u.get("name", ""), want, miss, rec, missing))

    got = tot["want"] - tot["miss"]
    full = got - tot["record"]
    print(f"提纲点名实体 {tot['want']} 个：本库已有 {got}（{got*100//max(tot['want'],1)}%），"
          f"其中完整级 {full}、著录级 {tot['record']}；缺 {tot['miss']}")
    print("**「有条目」不等于「有完整级条目」**——正典位上摆一条照录，"
          "等于用清单欺骗清单。\n")

    bycell = collections.defaultdict(lambda: [0, 0])
    for u in o.get("units", []):
        k = (u.get("period"), u.get("domain"))
        for kk in ENTITY_KEYS:
            for nm in (u.get("expect") or {}).get(kk, []) or []:
                bycell[k][0] += 1
                if idx[kk].get(nm):
                    bycell[k][1] += 1
    worst = sorted(bycell.items(), key=lambda kv: (kv[1][1] / max(kv[1][0], 1), -kv[1][0]))
    print("覆盖率最低的格子（分母≥3 者）：")
    shown = 0
    for (p, d), (w, g) in worst:
        if w < 3:
            continue
        print(f"   {pname.get(p, p):<12}{name_of.get(d, d):<16} {g:>3}/{w:<3} "
              f"{g*100//w:>3}%")
        shown += 1
        if shown >= limit:
            break


def main():
    ap = argparse.ArgumentParser(description="内容提纲：合并·校验·对账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("merge")
    sub.add_parser("check")
    cv = sub.add_parser("coverage")
    cv.add_argument("--limit", type=int, default=24)
    a = ap.parse_args()
    if a.cmd == "merge":
        merge()
    elif a.cmd == "check":
        sys.exit(check())
    else:
        coverage(a.limit)


if __name__ == "__main__":
    main()

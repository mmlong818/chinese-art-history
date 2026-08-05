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
import re
import sys

WIKI = pathlib.Path(__file__).parent
OUTLINE = WIKI / "data" / "outline.json"
CONF = {"确知", "记得", "待核"}
BROKEN = []   # 读不动的 JSON，末尾报出，见 _entities()
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
    """从 plan/units-*.json **整体重建** units，而不是往里追加。

    起初这里是追加，代价当场付了：我在 `data/outline.json` 里把两个单元改归新设的
    寺观壁画门，却没改源文件 `plan/units-song-yuan.json`，**下一次合并把原样又加了回来**
    ——永乐宫与岩山寺各成了两条。

    根因不是漏改一处，是**派生数据被当成了可手改的数据**。所以改为整体重建：
    手改 units 不再是「会被悄悄回退」，而是**下次合并直接检出并报错**。
    把错从设计里去掉，比记得每次两处都改可靠。
    """
    o = load_outline()
    prev = {u["id"] for u in o.get("units", []) if isinstance(u, dict) and u.get("id")}

    units, seen, files, dup = [], {}, 0, 0
    for f in sorted((WIKI / "plan").glob("units-*.json")):
        items = (json.loads(f.read_text(encoding="utf-8")).get("items")) or []
        files += 1
        n = 0
        for u in items:
            if not isinstance(u, dict) or not u.get("id"):
                continue
            if u["id"] in seen:
                # 同一 id 出现在两份源文件里——两路代理撞题，须人判该留哪条。
                print(f"  ! id 撞车：{u['id']} 同时见于 {seen[u['id']]} 与 {f.name}")
                dup += 1
                continue
            seen[u["id"]] = f.name
            units.append(u)
            n += 1
        print(f"  {f.name:<34} {len(items):>4} 项 → 取 {n}")

    now = set(seen)
    orphan = sorted(prev - now)
    if orphan:
        print(f"\n  ! 提纲里有 {len(orphan)} 个单元在任何源文件里都找不到——"
              f"**这是有人手改了派生数据**，改动应回到 plan/ 下的源文件：")
        for i in orphan[:10]:
            print(f"      {i}")

    units.sort(key=lambda u: (u.get("period", ""), u.get("domain", ""), u["id"]))
    o["units"] = units
    save_outline(o)
    print(f"\n重建 {files} 份 → {len(units)} 个单元"
          f"（id 撞车 {dup} · 孤儿 {len(orphan)}）")


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


# 通名后缀。提纲写「牛河梁」，本库条目名作「牛河梁遗址」——**按名比对在后缀上失效**。
# 这条规则是在实体还没有 `alias` 字段时加的权宜之计，**现在 alias 已经有了，
# 它退为兜底**：能写 alias 的就去写 alias（那是撰写者认定的等同关系），
# 靠后缀猜是次一等的办法。规则必有失效点（「良渚」既可指良渚遗址也可指良渚古城），
# **所以把失效点做成可见的**：命中多于一个候选时一律不计，单列为待判，交人处置。
SUFFIX = ("遗址", "墓地", "墓", "石窟", "石刻", "古城", "窑址", "窑", "塔", "寺", "祠",
          "画派", "文化")


def _entities():
    idx = collections.defaultdict(dict)
    for sub, kind in (("artists", "artist"), ("works", "work"), ("sites", "site"),
                      ("classes", "class"), ("treatises", "treatise"),
                      ("events", "event")):
        d = WIKI / "data" / sub
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # 坏 JSON 不该掀翻一份报告，**但也不该悄悄跳过**：
                # 它可能是正在写入的瞬时状态，也可能是真的坏了。
                # 记下来在末尾报出，交由 schema.py 去判定性质。
                BROKEN.append(str(p.relative_to(WIKI)))
                continue
            v = (e["id"], e.get("depth") or "full")
            nm = e.get("name") or e.get("title") or ""
            if nm:
                idx[kind][nm] = v
            # **别名**。同一个东西两种写法而对账算它缺，本项目已撞四次：
            # 「牛河梁」对「牛河梁遗址」（通名后缀）、
            # 「范寬」对「范宽」（繁简）、
            # 「越窑」对「越窑青瓷」（窑口与产品）、
            # 「殷墟发掘」对「殷墟十五次发掘」（题名详略）。
            # 前两类曾靠后缀规则与罗马名各自绕过，**而绕法本身各有失效点**；
            # 加一个显式的 alias 字段，是把「这两个名字指同一物」变成可写下来的事实，
            # 而不是每次靠一条新规则去猜。L11 的补充规则说可靠证据是稳定标识符
            # 而非字形——alias 是撰写者亲手认定的等同关系，比任何字形规则都硬。
            for a in (e.get("alias") or []):
                if a and a not in idx[kind]:
                    idx[kind][a] = v
    return idx


def _sp(s):
    """去掉空白后比对。**空格在中文名里不表意**——
    本库条目名作「莫高窟第 45 窟」而提纲写「莫高窟第45窟」，按名比对因此漏掉。
    这是第五种失效方式（前四种：通名后缀、繁简、窑口与产品、题名详略），
    但它与前四种不同：**归一化空白不会把两件不同的东西合成一件**，
    所以它可以安全地做成通用规则，不必靠 alias 一条条认。"""
    return re.sub(r"[\s　]+", "", s or "")


def _lookup(idx, kind, nm):
    """→ (hit, how)：how ∈ exact | space | suffix | ambig | miss。**歧义不算命中。**"""
    if nm in idx[kind]:
        return idx[kind][nm], "exact"
    k = _sp(nm)
    for kk, vv in idx[kind].items():
        if _sp(kk) == k:
            return vv, "space"
    cands = [v for k, v in idx[kind].items()
             if k != nm and k.startswith(nm) and k[len(nm):] in SUFFIX]
    if len(cands) == 1:
        return cands[0], "suffix"
    if len(cands) > 1:
        return None, "ambig"
    return None, "miss"


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
    ambig = []
    for u in o.get("units", []):
        for k in ENTITY_KEYS:
            for nm in (u.get("expect") or {}).get(k, []) or []:
                tot["want"] += 1
                hit, how = _lookup(idx, k, nm)
                tot[how] += 1
                if how == "ambig":
                    ambig.append(f"{k}:{nm}")
                if hit and hit[1] == "record":
                    tot["record"] += 1

    got = tot["exact"] + tot["space"] + tot["suffix"]
    full = got - tot["record"]
    print(f"提纲点名实体 {tot['want']} 个：本库已有 {got}"
          f"（{got*100//max(tot['want'],1)}%）——严格同名 {tot['exact']}、"
          f"忽略空白 {tot['space']}、通名后缀 {tot['suffix']}；其中完整级 {full}、著录级 {tot['record']}")
    print(f"缺 {tot['miss']}，另有 {tot['ambig']} 个名字**后缀规则命中多个候选、"
          f"一律不计**（按名比对必有失效点，把它做成可见的而不是猜）")
    if ambig:
        print("   待判：" + "、".join(sorted(set(ambig))[:12]))
    print("**「有条目」不等于「有完整级条目」**——正典位上摆一条照录，"
          "等于用清单欺骗清单。\n")

    bycell = collections.defaultdict(lambda: [0, 0])
    for u in o.get("units", []):
        k = (u.get("period"), u.get("domain"))
        for kk in ENTITY_KEYS:
            for nm in (u.get("expect") or {}).get(kk, []) or []:
                bycell[k][0] += 1
                if _lookup(idx, kk, nm)[0]:
                    bycell[k][1] += 1
    if BROKEN:
        print(f"！有 {len(BROKEN)} 个文件 JSON 读不动，本次对账未计入"
              f"（可能是正在写入，也可能真坏了——跑 schema.py 判定）：")
        for b in BROKEN[:6]:
            print("   " + b)
        print()

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


def gaps(kind=None, top=0):
    """缺口工作清单：提纲点了名而本库没有的实体，按类与被点次数排。

    **被多个单元点到的缺口优先**——那不是一个单元的偏好，是多处都要用到的东西。
    家具材质那几个类目正是这么暴露的：黄花梨在三个互不相干的单元里各自浮现。
    """
    o = load_outline()
    idx = _entities()
    want = collections.defaultdict(lambda: collections.Counter())
    where = collections.defaultdict(set)
    for u in o.get("units", []):
        for k in ENTITY_KEYS:
            for nm in (u.get("expect") or {}).get(k, []) or []:
                if _lookup(idx, k, nm)[0]:
                    continue
                want[k][nm] += 1
                where[(k, nm)].add(f"{u['period']}×{u['domain']}")
    total = sum(sum(c.values()) for c in want.values())
    print(f"缺口 {sum(len(c) for c in want.values())} 个不同实体（被点 {total} 次）\n")
    for k in ENTITY_KEYS:
        if kind and k != kind:
            continue
        c = want[k]
        if not c:
            continue
        print(f"── {k}　{len(c)} 个 ──")
        for nm, n in c.most_common(top or None):
            cells = "、".join(sorted(where[(k, nm)])[:3])
            print(f"   {n}×  {nm:<22}{cells}")
        print()


def main():
    ap = argparse.ArgumentParser(description="内容提纲：合并·校验·对账·缺口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("merge")
    sub.add_parser("check")
    cv = sub.add_parser("coverage")
    cv.add_argument("--limit", type=int, default=24)
    gp = sub.add_parser("gaps")
    gp.add_argument("--kind", choices=ENTITY_KEYS)
    gp.add_argument("--top", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "merge":
        merge()
    elif a.cmd == "check":
        sys.exit(check())
    elif a.cmd == "gaps":
        gaps(a.kind, a.top)
    else:
        coverage(a.limit)


if __name__ == "__main__":
    main()

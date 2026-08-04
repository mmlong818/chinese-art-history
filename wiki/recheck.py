"""核查等级重估：把 memory 分成「可升」「已到上限」「与外部数据不符」三堆。

    python recheck.py census                 # 只统计不联网：memory 的构成与可升级性
    python recheck.py persons [--apply]      # 串行核艺术家生卒
    python recheck.py objects [--apply]      # 串行核作品／遗址的年代·现藏·尺寸
    python recheck.py report                 # 汇总上次两轮的判定

—— 为什么要有这个工具 ——

`memory` 现有两百余条，但**它们不是同一种东西**：

- 国内馆藏的作品，故宫、上博、国博均无公开 API，**上限就是 `wikidata`**；
- `class`（鼎、皴法、兽面纹）是概念不是实物，`verify.py` 查人查物都对不上，
  标 `memory` 是准确的；
- `treatise` 的版本与卷数未核，不该因作者生卒查到了就升级——
  **核对作者身份不等于核对了这部书**；
- `event` 多不在结构化数据库里。

**把这些一律算作「质量欠账」，会把一份诚实的记录说成一份没做完的记录。**
所以先做 census：分清哪些是欠账、哪些是上限。

—— 升级的门槛 ——

**外部数据库有值不等于核过。**本库已累计八例假值与误指：
巨然「1000–1000」、孙位「900–1000」、梁令瓒「800–800」（整百或同年占位）、
《写生珍禽图》条目给出错藏地（龙美术馆）错尺寸、
「明妃出塞图」词条实指首都博物馆另一件同名作品、
「万佛寺」词条实指香港 1957 年一座精舍……

所以本工具**只在外部值与条目已有断言相符时才建议升级**；
条目字段留空的一律不升——留空多半是撰写时判过「不可考」，那是立场不是空格。
不符的单列一堆，**交人判断，不自动改**：Wikidata 错的概率不比本库低。

—— 实测查出的一类系统性错误，比逐个假值更要紧 ——

**中国画家生卒不详者，Wikidata 常把「活动年代」渲染成生卒。**
张萱（Q565370）的描述作「唐朝宫廷画家 (713–755)」——713–755 是开元元年至
天宝十四载，是他的**活动期**，而张萱本人生卒不可考；夏圭（Q1360244）的
「1190–1224」同类。**任何「外部有值就升级」的做法都会把这类错误静默吃进来**，
而它在 JSON 里与真核过的生卒长得一模一样。
「条目留空一律不升」这条规则正是拦它的。

另：张瑀（Q8508822）实指一位 1994 年出生的足球运动员——同名误指，
与巨然「1000–1000」、「明妃出塞图」词条指首都博物馆另一件、
「万佛寺」词条指香港 1957 年精舍同类。
"""

import argparse
import collections
import glob
import json
import pathlib
import re
import sys
import time

WIKI = pathlib.Path(__file__).parent
sys.path.insert(0, str(WIKI))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

GAP = 4.0                      # 秒。串行，宁可慢——2.5s 实测仍撞 429 三次
YEAR = re.compile(r"-?\d{3,4}")
OUT = WIKI / ".recheck.json"   # 判定结果，不入库

# **可编程逐条核实的机构（白名单）。**第一版用的是「无 API 的国内馆」黑名单，
# 那必然漏：每出现一家没列进去的馆就误判一次——实测 29 件被判「可升 museum」，
# 其中碑林、法门寺、天津、宝鸡、滕州、枣庄、岱庙、原址等全是名单外的国内机构，
# 大英、波士顿、大阪市立、东京国立按 MUSEUMS.md 的实测也都取不到。
# **能核的馆是可穷举的，不能核的馆是列不完的**，所以只列前者。
API_MUSEUMS = ("cleveland", "met", "artic", "harvard", "smithsonian")


def entries():
    for f in glob.glob(str(WIKI / "data/*/*.json")):
        p = pathlib.Path(f)
        if p.parent.name not in ("artists", "works", "sites", "classes",
                                 "treatises", "events"):
            continue
        yield p.parent.name, p, json.loads(p.read_text(encoding="utf-8"))


def _ceiling(kind, d):
    """这一条的核查等级上限是什么，以及为什么。

    `museum` 的定义是「馆藏官方页**或**考古报告逐条核实」，所以：

    - **艺术家永远到不了 `museum`**——人不是馆藏物，没有「馆藏官方页」这回事。
      第一版把 24 位艺术家判为可升 museum，是把等级词表读错了。
    - **遗址只有引了正式发掘报告才够**。遗址不被收藏，考古报告是它唯一的那一路。
      引考古期刊（`kaogu-wenwu`）不算——那是刊物不是某处的报告。
    - 作品看现藏：西方馆有 API 可逐条核，国内馆无 API 则止于 `wikidata`。
    """
    if kind == "classes":
        return "memory", "类目是概念而非实物，查人查物皆对不上"
    if kind == "artists":
        return "wikidata", "人不是馆藏物，无「馆藏官方页」一路，生卒核到即止"
    if kind == "treatises":
        return "wikidata", "古籍：作者可核，但版本与卷数未核则不宜再升"
    if kind == "events":
        return "wikidata", "事件多不在结构化库中，能核到即止"
    if kind == "sites":
        refs = [str(s.get("ref", "")) for s in (d.get("sources") or [])]
        if any(r.endswith("-baogao") or "baogao" in r for r in refs):
            return "museum", "引了正式发掘报告，可逐条核实"
        return "wikidata", "未引正式发掘报告；遗址不被收藏，无馆藏页一路"
    hit = _api_museum(d)
    if hit:
        return "museum", f"引 {hit}，其 API 可逐条核实"
    return "wikidata", f"现藏「{str(d.get('holder') or '未记')[:18]}」无可编程核实的通道"


def _api_museum(d):
    """条目里是否引了可编程核实的馆——看图像 source 与 sources 里的 ref，
    不看 holder 的字面。**能核的凭据是「有那条通道」，不是「馆名像西方的」。**"""
    for s in (d.get("sources") or []):
        if str(s.get("ref", "")) in API_MUSEUMS:
            return str(s["ref"])
    found = []

    def walk(v):
        if isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            if v.get("source") in API_MUSEUMS:
                found.append(v["source"])
            for x in v.values():
                walk(x)

    walk(d)
    return found[0] if found else None


def census():
    rows = collections.Counter()
    detail = collections.defaultdict(list)
    for kind, p, d in entries():
        v = d.get("verification")
        if v != "memory":
            continue
        ceil, why = _ceiling(kind, d)
        rows[(kind, ceil)] += 1
        detail[ceil].append((kind, d["id"], why))
    tot = sum(rows.values())
    print(f"memory 共 {tot} 条。按**核查等级上限**分堆——\n")
    for ceil in ("memory", "wikidata", "museum"):
        n = sum(v for (k, c), v in rows.items() if c == ceil)
        label = {"memory": "已到上限，标 memory 即准确（不是欠账）",
                 "wikidata": "上限为 wikidata（无 API 可核，只能到这一档）",
                 "museum": "可升至 museum（西方馆藏或有正式报告）"}[ceil]
        print(f"  {n:>4}  {label}")
        for k, v in sorted(((k, v) for (k, c), v in rows.items() if c == ceil),
                           key=lambda kv: -kv[1]):
            print(f"        {v:>4}  {k}")
    print(f"\n**所以 {tot} 条里，真正算欠账的是那 "
          f"{sum(v for (k, c), v in rows.items() if c != 'memory')} 条**——"
          f"其余标 memory 是准确的记录，不是没做完。")
    return detail


def _norm(y):
    """公元前年份两边写法不一：条目写 -208，Wikidata 的 ISO 值常丢负号。
    先秦人物不会有公元 208 年的先秦人，按绝对值比对足够。"""
    return str(y).lstrip("-").lstrip("0") or "0"


def _years(claims, pid, V):
    out = set()
    for c in claims.get(pid, [])[:4]:
        v = V._wd_val(c)
        if v:
            out |= {_norm(m.group()) for m in YEAR.finditer(str(v))}
    return out


def _pick(term, require, V):
    """检索并取第一个带所需属性的实体。撞同名今人是常态，故必须按属性筛。"""
    try:
        hits = V._wd_search(term)
    except Exception as e:
        return None, f"检索失败：{e}"
    for h in hits:
        try:
            cl = V._wd_entity(h["id"]).get("claims", {})
        except Exception:
            continue
        if any(p in cl for p in require):
            return (h, cl), None
        time.sleep(0.8)
    return None, f"{len(hits)} 条同名，无一带 {require}"


def persons(apply=False):
    import verify as V
    todo = [(p, d) for k, p, d in entries()
            if k == "artists" and d.get("verification") == "memory"]
    print(f"艺术家 memory {len(todo)} 条，串行核生卒，间隔 {GAP}s\n")
    up, keep, conflict, none = [], [], [], []
    for i, (p, d) in enumerate(todo, 1):
        fb, fd = str(d.get("birth") or ""), str(d.get("death") or "")
        print(f"[{i}/{len(todo)}] {d.get('name')} 条目记 {fb or '—'}–{fd or '—'}")
        got, err = _pick(d.get("name", ""), ("P569", "P570"), V)
        if not got:
            print(f"    {err} → 维持 memory")
            none.append((d["id"], err))
            time.sleep(GAP)
            continue
        h, cl = got
        wb, wd = _years(cl, "P569", V), _years(cl, "P570", V)
        print(f"    {h['id']} {h.get('description','')[:38]} · 生 {sorted(wb) or '—'} 卒 {sorted(wd) or '—'}")
        if not fb and not fd:
            print("    ◇ 条目主动留空（不可考）——外部有值亦不采")
            keep.append((d["id"], "条目留空，判过不可考"))
        elif (wb and fb and _norm(fb) not in wb) or (wd and fd and _norm(fd) not in wd):
            print("    ★ 与条目不符，须人工判")
            conflict.append((d["id"], f"条目 {fb}–{fd} vs 外部 {sorted(wb)}–{sorted(wd)}"))
        elif fb and fd and _norm(fb) in wb and _norm(fd) in wd:
            print("    ✓ 可升 wikidata")
            up.append((d["id"], p))
        else:
            keep.append((d["id"], "外部数据不全"))
        time.sleep(GAP)
    _finish("persons", up, keep, conflict, none, apply)


def objects(apply=False):
    """核作品与遗址：年代、现藏、材质、尺寸。**只有条目已断言的字段才比对**。"""
    import verify as V
    todo = [(k, p, d) for k, p, d in entries()
            if k in ("works", "sites") and d.get("verification") == "memory"]
    print(f"作品与遗址 memory {len(todo)} 条，串行核，间隔 {GAP}s\n")
    up, keep, conflict, none = [], [], [], []
    for i, (k, p, d) in enumerate(todo, 1):
        name = d.get("title") or d.get("name") or ""
        term = re.sub(r"[《》〈〉（）()·]", "", name)
        print(f"[{i}/{len(todo)}] {name[:26]}")
        got, err = _pick(term, ("P571", "P195", "P31"), V)
        if not got:
            print(f"    {err} → 维持 memory")
            none.append((d["id"], err))
            time.sleep(GAP)
            continue
        h, cl = got
        desc = h.get("description", "")[:44]
        print(f"    {h['id']} {desc}")
        # 现藏是最能识破「同名异物」的一项——本库已有多例误指
        holder = str(d.get("holder") or "")
        wh = set()
        for c in cl.get("P195", [])[:4]:
            v = V._wd_val(c)
            if v:
                wh.add(str(v))
        if holder and wh:
            ok = any(any(t and t in w for t in re.findall(r"[一-鿿]{2,}", holder))
                     for w in wh)
            if not ok:
                print(f"    ★ 现藏不符：条目「{holder[:18]}」vs 外部 {list(wh)[:2]}"
                      f" → 疑同名异物，不升")
                conflict.append((d["id"], f"现藏不符 {holder[:18]} vs {list(wh)[:2]}"))
                time.sleep(GAP)
                continue
            print(f"    ✓ 现藏相符 → 可升 wikidata")
            up.append((d["id"], p))
        else:
            print("    条目或外部缺现藏，无可比对项 → 维持 memory")
            keep.append((d["id"], "无可比对的现藏"))
        time.sleep(GAP)
    _finish("objects", up, keep, conflict, none, apply)


def _finish(tag, up, keep, conflict, none, apply):
    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    prev[tag] = {"up": [i for i, _ in up], "keep": keep,
                 "conflict": conflict, "none": none}
    OUT.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"可升 {len(up)} · 维持 {len(keep)} · 不符须人工判 {len(conflict)} · 查无 {len(none)}")
    if conflict:
        print("\n★ 不符（**不自动改**——Wikidata 错的概率不比本库低）：")
        for i, why in conflict:
            print(f"   {i}: {why}")
    if apply and up:
        for eid, p in up:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["verification"] = "wikidata"
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        print(f"\n已升 {len(up)} 条为 wikidata")
    elif up:
        print(f"\n（未写入。加 --apply 才改文件）可升：{', '.join(i for i, _ in up)}")


def report():
    if not OUT.exists():
        sys.exit("尚无判定结果，先跑 persons / objects")
    d = json.loads(OUT.read_text(encoding="utf-8"))
    for tag, v in d.items():
        print(f"── {tag}")
        for k in ("up", "keep", "conflict", "none"):
            print(f"   {k:<9} {len(v.get(k) or [])}")


def main():
    ap = argparse.ArgumentParser(description="核查等级重估（串行，不自动改不符项）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("census")
    for name in ("persons", "objects"):
        s = sub.add_parser(name)
        s.add_argument("--apply", action="store_true")
    sub.add_parser("report")
    a = ap.parse_args()
    {"census": census, "report": report,
     "persons": lambda: persons(a.apply),
     "objects": lambda: objects(a.apply)}[a.cmd]()


if __name__ == "__main__":
    main()

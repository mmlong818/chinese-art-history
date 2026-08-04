"""缺口排查：把「权威名单上有而本库没有」变成可跑的清单。

    python gap.py artists          # 权威艺术家名单 × 本库条目 × 本库正文
    python gap.py orphan-artist    # 完整级作品 artist 字段为空而可补者
    python gap.py outline          # 与 plan/*-outline.json 各份清单对账

—— 为什么要有这个工具 ——

本库的空缺不是随机分布的，**它们恰好落在「写了论述却没建条目」的位置上**：

- 黄筌条目里写了「黄家富贵、徐熙野逸」的对举，并论及「徐熙无可靠真迹传世」——
  **论述在，被论述的那一半（徐熙）不在。**
- 顾恺之条目讲「名与物的对应极不可靠」，那是六朝三杰共有的处境——
  而陆探微、张僧繇没有条目。
- 《江山楼观图卷》在库里，`artist` 字段是 `None`。

—— 方向：拿名单比对文本，不从文本发现名字 ——

**第一版走错了方向**：从正文里提取所有 2–4 字汉字串当人名候选，
结果 6,290 项里前几十全是本库自己的套语（「现藏」「本库不作」「一节」「本库并陈」）。
**那是拿宽模式认结构（见 tasks/lessons.md L11）——那个模式在正常文本上大量命中。**

正确的方向反过来：**以 `plan/artists-outline.json` 这类权威名单为白名单，
去比对本库的条目与正文。**名单自带确信度，故「该有而没有」可判定，而不是靠猜。
**能穷举的一侧当白名单**，这正是 L11 的规则。

—— 这个工具不代人取舍 ——

它只分堆并排序。名单上有而本库无，可能是漏，也可能是本库有意不收
（`canon.json` 的 `anonymity` 字段就是为「本无其人可指」而设，与「尚未写」不是一回事）。
**清单自身标「待核」的条目不能当基准用**——拿记忆产物去判本库缺什么，
等于把别人的记忆错误固化成本库的骨架。
"""

import argparse
import json
import pathlib
import sys

WIKI = pathlib.Path(__file__).parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def entries():
    for sub in ("artists", "works", "sites", "classes", "treatises", "events"):
        d = WIKI / "data" / sub
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            yield sub, p, json.loads(p.read_text(encoding="utf-8"))


def text_of(d):
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(d.get("sections") or {})
    out.append(str(d.get("one_line") or ""))
    return " ".join(out)


def load_outline(name):
    p = WIKI / "plan" / name
    if not p.exists():
        sys.exit("缺 plan/" + name + "——先让相应的参照那一路交付")
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    return [x for x in (items or []) if isinstance(x, dict)]


def field(it, *keys):
    for k in keys:
        v = it.get(k)
        if v:
            return str(v).strip()
    return ""


def artists(limit=28):
    items = load_outline("artists-outline.json")
    full, stub = {}, {}
    for sub, p, d in entries():
        if sub != "artists":
            continue
        target = stub if d.get("depth") == "record" else full
        target[d.get("name", "")] = d["id"]

    prose = [(d["id"], text_of(d)) for sub, p, d in entries()
             if d.get("depth") != "record"]

    hanging, queued, upgrade = [], [], []
    for it in items:
        nm = field(it, "姓名", "name")
        if not nm or nm in full:
            continue
        lvl = field(it, "级别", "tier") or "?"
        conf = field(it, "确信度", "confidence") or "?"
        hits = [eid for eid, t in prose if nm in t]
        if nm in stub:
            upgrade.append((nm, lvl, conf, stub[nm], len(hits)))
        elif hits:
            hanging.append((nm, lvl, conf, hits))
        else:
            queued.append((nm, lvl, conf))

    print("权威名单 " + str(len(items)) + " 项 × 本库 " + str(len(full))
          + " 条完整级 + " + str(len(stub)) + " 条著录级")
    print()
    print("【甲】名单有、本库无条目、而正文已在讲他 —— " + str(len(hanging)) + " 位")
    print("      这一堆最该先补：**论述在而被论述者不在，是悬空。**")
    for nm, lvl, conf, hits in sorted(hanging, key=lambda r: -len(r[3]))[:limit]:
        print("      " + nm.ljust(8) + " 级" + lvl.ljust(3) + " " + conf.ljust(10)
              + " 被 " + str(len(hits)).rjust(2) + " 条目提到（如 " + hits[0] + "）")
    print()
    print("【乙】名单有、本库无条目、正文亦未提 —— " + str(len(queued)) + " 位，按级别排队")
    for nm, lvl, conf in sorted(queued, key=lambda r: r[1])[:limit]:
        print("      " + nm.ljust(8) + " 级" + lvl.ljust(3) + " " + conf)
    if len(queued) > limit:
        print("      …另 " + str(len(queued) - limit) + " 位")
    print()
    print("【丙】本库已有著录级 stub、该升为完整级 —— " + str(len(upgrade)) + " 位")
    print("      **这不是新建，是深化。**")
    for nm, lvl, conf, sid, h in sorted(upgrade, key=lambda r: -r[4])[:limit]:
        print("      " + nm.ljust(8) + " 级" + lvl.ljust(3) + " " + sid.ljust(16)
              + " 正文提及 " + str(h) + " 处")


def orphan_artist():
    names = {}
    for sub, p, d in entries():
        if sub == "artists" and d.get("name"):
            names[d["name"]] = d["id"]
    rows = []
    for sub, p, d in entries():
        if sub != "works" or d.get("artist") or d.get("depth") == "record":
            continue
        blob = str(d.get("title", "")) + " " + str(d.get("one_line", ""))
        hit = [n for n in names if n and n in blob]
        rows.append((d["id"], str(d.get("title", ""))[:26], hit))
    linkable = [r for r in rows if r[2]]
    print("完整级作品中 artist 为空者 " + str(len(rows)) + " 件；其中题名或一句话里"
          "出现本库已有艺术家之名的 " + str(len(linkable)) + " 件：")
    print()
    for i, t, hit in linkable[:40]:
        print("   " + i.ljust(42) + " " + t.ljust(28) + " 可链 " + names[hit[0]])
    print()
    print("其余 " + str(len(rows) - len(linkable)) + " 件的作者或不在本库、"
          "或本无作者可指——**后者是事实而非欠账**（见 canon.json 的 anonymity）。")


def outline():
    have = set()
    for _, _, d in entries():
        have.add(d.get("name") or d.get("title"))
    plan = WIKI / "plan"
    if not plan.exists():
        sys.exit("尚无 plan/ 目录")
    for f in sorted(plan.glob("*-outline.json")):
        items = load_outline(f.name)
        miss, low = [], 0
        for it in items:
            nm = field(it, "姓名", "名称", "书名", "卷", "编", "name")
            conf = field(it, "确信度", "confidence")
            if "待核" in conf:
                low += 1
            if nm and nm not in have:
                miss.append((nm, conf))
        print("── " + f.name + "：" + str(len(items)) + " 项，本库无对应条目 "
              + str(len(miss)) + " 项，清单自身标「待核」" + str(low) + " 项")
        for nm, conf in miss[:16]:
            print("      " + nm[:34].ljust(36) + " " + conf)
        if len(miss) > 16:
            print("      …另 " + str(len(miss) - 16) + " 项")
        print()
    print("**清单自身标「待核」的那些不能当基准用**——"
          "拿记忆产物去判本库缺什么，等于把错误固化成骨架。")


def main():
    ap = argparse.ArgumentParser(description="缺口排查（只分堆，不代人取舍）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("artists")
    a.add_argument("--limit", type=int, default=28)
    sub.add_parser("orphan-artist")
    sub.add_parser("outline")
    ns = ap.parse_args()
    if ns.cmd == "artists":
        artists(ns.limit)
    elif ns.cmd == "orphan-artist":
        orphan_artist()
    else:
        outline()


if __name__ == "__main__":
    main()

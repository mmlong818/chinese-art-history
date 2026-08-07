"""故宫博物院藏品总目索引（zm-digicol.dpm.org.cn）——有硬预算的抽样，不做全量。

    python dpm.py probe                  # 探接口现状与成本，只发 1 次请求
    python dpm.py sample --pages 60      # 抽样，默认 60 页（12000 条），可改
    python dpm.py stats                  # 统计已抽到的本地缓存

—— 为什么是抽样而不是全量 ——

实测 2026-08-04：POST `/cultural/queryList` 可用，返回的字段正是本库所需
（name／culturalRelicNo 藏品号／suggestCategoryName／dynastyName／hasImage／
dbgUrl 官方详情页）。但：

  一、**它忽略一切过滤参数。**category／categoryId／keyword／k／name／
      searchText／conditions 等十余种都试过，`recordcount` 恒为 1,860,076，
      返回内容一模一样。**「过滤条件写错就静默返回全部」是最危险的行为**——
      不报错，只是悄悄给了错的东西，所以不能凑合着用。
  二、**pageSize 封顶 200**（传 500／1000／2000 均只回 200 条）。
  三、类目在返回顺序里交错（第一页就混着珐琅、铜器、绘画、金银器），
      **无法提前停**。

三条合起来：要取全部绘画就得扫完全库 = **9,301 次请求、约 716 MB**。
那不是「一次礼貌的抓取」，是实打实的负担，本库不做。

—— 这个工具做什么 ——

在**硬预算内**抽样，用途有三，都不需要完整性：
  1. 量出真实的类目与朝代分布，据此估算故宫书画的总量级；
  2. 把抽到的书画（Paintings／Calligraphy／Rubbings）连同藏品号与官方页 URL
     落到本地缓存，供本库条目**人工核对**（verification 由人核过再标，不由抓取标）；
  3. 验证这条通道确实可用——以便日后若站方开放导出或过滤，能立刻接上。

**完整名单的正路是站方自己的导出功能**：客户端代码里有 `/cultural/exportData`，
说明站方本就设计了批量取数。用它得在浏览器里点，那是使用者自己的访问，
一次导出即可，胜过本工具跑一万次。

—— 礼貌约束（写死，不给调低的口子）——

  · 串行，请求间隔不低于 SLEEP 秒
  · 具名 User-Agent，说明用途与非商业性质
  · 只取元数据，**不下载任何图像**——图像权属另论，且本库范围已限定境内书画
  · 遇 429／403 立即停止并退出，不重试硬闯
  · 页数上限由 --pages 给出，默认保守；缓存落地后不重复请求
"""

import argparse
import json
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


BASE = "https://zm-digicol.dpm.org.cn"
API = BASE + "/cultural/queryList"
CACHE = Path(__file__).parent / ".dpm-index.json"   # 含本机缓存，不入库
PAGE_SIZE = 200          # 实测上限，传更大也只回 200
SLEEP = 2.0              # 秒。串行且不低于此值
UA = ("china-art-history-archive/0.1 (non-commercial scholarly index of Chinese art "
      "history; metadata only, no image mirroring; contact via repository)")

# —— 藏品号前缀本身是一条递藏线索，别浪费它 ——
#
# 故宫藏品号分两系: **「故」字号 = 1925 年建院时接收的清宫旧藏；
# 「新」字号 = 1949 年以后征集、调拨、捐赠入藏。**
#
# 用处很实在: 新字号大概率**不见于《石渠宝笈》《大观录》《江村销夏录》**一类
# 清代著录（那些著录的是清宫与清代私家所藏），所以查不到递藏不等于漏查，
# 往往就是它本不在那批文献里。**据此可以诚实地写「无清宫著录线索」而不必编，
# 也不必为凑 prov 块去攀附。**
# 反之，故字号若查不到清宫著录，那才是真该继续查的信号。

# 本库重心是境内书画。故宫总目的类目名是英文，这三项对应绘画／法书／碑帖。
SHUHUA = {"Paintings", "Calligraphy", "Rubbings"}


def _post(payload):
    req = urllib.request.Request(
        API, data=urllib.parse.urlencode(payload).encode(),
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                 "Referer": BASE + "/cultural/list",
                 "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def probe():
    """只发一次请求，报接口现状与全量成本。"""
    d = _post({"page": 1, "pageSize": PAGE_SIZE})
    rows = d.get("rows") or []
    total, pages = d.get("recordcount"), d.get("pagecount")
    print(f"接口可用。recordcount={total:,} · pagecount={pages:,} · 本页 {len(rows)} 条")
    print(f"全量成本估算：{pages:,} 次请求 · 约 {pages * 77 / 1024:.0f} MB"
          f" · 按 {SLEEP}s 间隔约 {pages * SLEEP / 3600:.1f} 小时")
    print("**本库不做全量。**完整名单的正路是站方自带的导出功能（/cultural/exportData）。\n")
    print("首页样本：")
    for r in rows[:6]:
        print(f"  [{r.get('suggestCategoryName',''):<26}] {r.get('name','')[:26]:<28} "
              f"{r.get('culturalRelicNo',''):<12} 影像={r.get('hasImage')}")
    return d


def sample(pages):
    """抽样。串行、限速、遇限流即停。"""
    got, seen = [], set()
    if CACHE.exists():
        old = json.loads(CACHE.read_text(encoding="utf-8"))
        got = old.get("rows") or []
        seen = {r.get("uuid") for r in got}
        print(f"已有缓存 {len(got)} 条，续抽\n")
    start_page = (len(got) // PAGE_SIZE) + 1
    for i in range(pages):
        p = start_page + i
        try:
            d = _post({"page": p, "pageSize": PAGE_SIZE})
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                print(f"第 {p} 页遇 HTTP {e.code}——**立即停止，不重试硬闯**")
                break
            print(f"第 {p} 页 HTTP {e.code}，停")
            break
        except Exception as e:
            print(f"第 {p} 页 {type(e).__name__}，停")
            break
        rows = d.get("rows") or []
        if not rows:
            print(f"第 {p} 页无数据，抽样结束")
            break
        for r in rows:
            if r.get("uuid") not in seen:
                seen.add(r.get("uuid"))
                got.append(r)
        if (i + 1) % 10 == 0:
            print(f"  …{i+1}/{pages} 页，累计 {len(got)} 条")
        time.sleep(SLEEP)
    CACHE.write_text(json.dumps({"fetched": len(got), "page_size": PAGE_SIZE,
                                 "rows": got}, ensure_ascii=False), encoding="utf-8")
    print(f"\n缓存 {len(got)} 条 → {CACHE.name}")
    stats()


def stats():
    if not CACHE.exists():
        sys.exit("尚无缓存，先跑 python dpm.py sample")
    rows = json.loads(CACHE.read_text(encoding="utf-8"))["rows"]
    import collections
    cat = collections.Counter(r.get("suggestCategoryName") or "?" for r in rows)
    print(f"\n已抽 {len(rows)} 条 · 占全库 {len(rows)/1860076*100:.2f}%")
    print("\n类目分布（前 14）：")
    for k, v in cat.most_common(14):
        mark = " ←本库重心" if k in SHUHUA else ""
        print(f"   {v:>6}  {k}{mark}")
    sh = [r for r in rows if (r.get("suggestCategoryName") or "") in SHUHUA]
    with_img = sum(1 for r in sh if r.get("hasImage"))
    print(f"\n书画（Paintings／Calligraphy／Rubbings）{len(sh)} 条，其中有影像 {with_img} 条")
    if rows:
        # **不据此外推总量。**抽的是连续页码，不是随机样本——实测 8000 条里
        # 6525 条是陶瓷，说明返回顺序按类聚簇。用聚簇样本的比例去乘全库，
        # 得出的数字看起来精确而毫无依据，正是本库反复禁止的那种假精确。
        print(f"书画占本次抽样的 {len(sh)/len(rows)*100:.1f}%。"
              f"**不据此外推故宫书画总量**：抽的是连续页码而非随机样本，"
              f"返回顺序按类聚簇（本次 {cat.most_common(1)[0][1]} 条集中在"
              f"「{cat.most_common(1)[0][0]}」一类），聚簇样本的比例乘全库只会得出"
              f"看似精确而无依据的数字。要总量请查馆方公布口径。")
    print("\n书画样本（供人工核对）：")
    for r in sh[:12]:
        print(f"   {r.get('name','')[:30]:<32} {r.get('culturalRelicNo',''):<12} "
              f"{r.get('dynastyName',''):<10} {r.get('dbgUrl','') or '（无官方页）'}")


def main():
    ap = argparse.ArgumentParser(description="故宫藏品总目抽样索引（不做全量）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    s = sub.add_parser("sample"); s.add_argument("--pages", type=int, default=60)
    sub.add_parser("stats")
    a = ap.parse_args()
    {"probe": probe, "sample": lambda: sample(a.pages), "stats": stats}[a.cmd]()


if __name__ == "__main__":
    main()

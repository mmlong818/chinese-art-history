"""故宫博物院藏品总目索引（zm-digicol.dpm.org.cn）——有硬预算的抽样，不做全量。

    python dpm.py probe                      # 探接口现状与成本，只发 1 次请求
    python dpm.py map --step 400             # 画全域类目分布图（24 次请求）
    python dpm.py sweep --from 40000 --to 48000 # 只扫指定记录区间（偏移，非页号）
    python dpm.py sample --pages 60          # 顺序抽样（map 之前的旧办法）
    python dpm.py stats                      # 统计本地缓存

**正当用法是先 map 后 sweep。**直接 sample 全域等于没做 map。

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

起初据此判断「取全部绘画须扫完全库 = 9,301 次请求、约 700 MB」，因而只做抽样。
**那个判断错了，错在第三条: 类目不是均匀交错，而是按类聚簇。**

—— map 改变了成本结构，但第一次做错了 ——

修正后的实测（2026-08-04，以**记录偏移**为单位，全库 1,860,076 条）:

    偏移       0–16,000    陶瓷／铜器为主，夹杂书画（5–9/20）
    偏移  40,000–48,000    **绘画 20/20**
    偏移 112,000–120,000   **绘画 20/20**
    偏移 160,000–184,000   **碑帖 20/20**
    偏移 560,000–1,840,000 几乎全是陶瓷（约 130 万条）
    其余                   织绣、古籍、金银器、玉器、宗教文物…书画 0/20

**所以书画只住在三条带里，约 200 次请求覆盖约 4 万条**，而非盲扫 9,300 次。

**第一次做错了，错在单位。**首版 map 用 pageSize=20 探、sweep 用 pageSize=200 扫，
而 `page` 的含义取决于 `pageSize`——同一个「第 1900 页」相差十倍偏移。
于是扫回 60,200 条而书画 0 条，**看着像数据矛盾，实则是我扫错了地方**。
第二层错: map 的循环上界写 9301（pageSize=200 的总页数），在 pageSize=20 下
共 93,004 页，故首版 map 只看了全库前 10%。
现已改为对外一律用记录偏移说话（见 `_at()`），单位不可能再混。

—— 这个工具做什么 ——

  1. `map` 画类目分布，据此把成本从 9,301 次压到几百次；
  2. `sweep` 只扫书画页段，取 name／藏品号／类目／朝代／hasImage／官方页 URL；
  3. 落到本地缓存，供本库条目**按藏品号对账**——藏品号是稳定标识符，
     标题字形不是（实测「顾恺之／宋人洛神图卷」「临／仿韦偃牧放图」
     「锦鸡／芙蓉锦鸡图」三处都是靠藏品号才认出同物的）。

—— 礼貌约束（写死，不给调低的口子）——

  · 串行，请求间隔不低于 SLEEP 秒
  · 具名 User-Agent，说明用途与非商业性质
  · 只取元数据，**不下载任何图像**——图像权属另论，且本库范围已限定境内书画
  · 遇 429／403 立即停止并退出，不重试硬闯
  · 页数上限由 --pages 给出，默认保守；缓存落地后不重复请求
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

# —— 藏品号前缀是一条递藏线索，也是一个陷阱 ——
#
# **实测共五系**（36,400 条扫描样本，2026-08-04）:
#
#   故   16,190   1925 年建院时接收的清宫旧藏
#   新   16,799   1949 年以后征集、调拨、捐赠入藏
#   书    2,015   **古籍文献**——样本中该系 100% 为 Historical Documents
#                （如《晚晴簃诗汇》逐页著录）。不属书画，出本库范围。
#   标    1,164   **标本**——样本中该系 100% 为瓷片。研究标本，非完整器。
#   资      232   **含义未经核实。**全为绘画与法书，作者为钱选、戴嵩、文伯仁、
#                文嘉、方从义等。按「书＝书籍、标＝标本」的构词推测可能是「资料」，
#                **但这是推测**: 若确为资料件（复制品／参考件），把它们当原作立目
#                就是把复制品写成真迹。**故本库对「资」字号一律不立目，
#                待向馆方或专业目录核实前缀含义后再定。**
#
# 「故」「新」两系的用处很实在: 新字号大概率**不见于《石渠宝笈》《大观录》
# 《江村销夏录》**一类清代著录（那些著录的是清宫与清代私家所藏），
# 所以查不到递藏不等于漏查，往往就是它本不在那批文献里。
# **据此可以诚实地写「无清宫著录线索」而不必编，也不必为凑 prov 块去攀附。**
# 反之，故字号若查不到清宫著录，那才是真该继续查的信号。
#
# **陷阱在去重上。**本库的藏品号去重一度只认「新」「故」两系
# （正则 `新\d{8}|故\d{8}`），于是「书」「标」「资」三系共 3,411 条
# 一条都匹配不上——**对账会把它们全部报成「本库尚无」，而其中可能已有条目。**
# 下面这条正则覆盖五系，供对账使用。
ACC_RE = r"(?:资(?:绘画|书法|甲骨|新)?|故|新|书|标)\d{5,8}(?:-\d+/\d+)?"
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


def _at(offset, size):
    """按**记录偏移**取一页，而不是按页号。

    页号是个陷阱: `page` 的含义取决于 `pageSize`——第 1900 页在 pageSize=20 下是
    第 38,000 条附近，在 pageSize=200 下是第 380,000 条附近。**本工具第一版就栽在这里**:
    map 用 pageSize=20 探出「热点在第 1900、2000 页」，sweep 却用 pageSize=200 去扫
    同样的页号，扫的其实是完全不同的区域，结果 60,200 条里书画 0 条——
    看着像数据矛盾，实则是单位没对齐。
    还有第二层: map 的循环上界写 9301（那是 pageSize=200 的总页数），
    在 pageSize=20 下总共 93,004 页，**故 map 只看了全库前 10%**。

    所以对外一律用记录偏移说话，页号只在函数内部按 size 换算，单位不可能再混。
    """
    if offset % size:
        raise ValueError(f"偏移 {offset} 不是 {size} 的整数倍，换算会错位")
    return _post({"page": offset // size + 1, "pageSize": size})


def map_space(step=100, probe_size=20):
    """用少量请求把 9,301 页的类目分布探出来，据此只扫书画那几段。

    **为什么值得先做这一步**: 实测返回顺序按类聚簇——前 40 页只得 642 件书画，
    随后 60 页得了 4,926 件。既然聚簇，就不必为取书画而扫完全库。
    每隔 step 页探一次、每次只取 probe_size 条，约 93 次请求即可画出全域分布，
    **比盲扫 9,301 次省两个数量级**。

    这一步也是对站方的礼貌: 少取、只取判断所需，而不是先搬回来再筛。
    """
    marks = []
    TOTAL = 1860076
    stride = step * PAGE_SIZE      # step 以「PAGE_SIZE 页」为单位，换成记录数
    for off in range(0, TOTAL, stride):
        try:
            d = _at(off, probe_size) if off % probe_size == 0 else _post(
                {"page": off // probe_size + 1, "pageSize": probe_size})
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                print(f"偏移 {off} 遇 HTTP {e.code}——立即停止")
                break
            continue
        except Exception:
            continue
        rows = d.get("rows") or []
        if not rows:
            break
        n_sh = sum(1 for r in rows
                   if (r.get("suggestCategoryName") or "") in SHUHUA)
        top = collections.Counter(r.get("suggestCategoryName") or "?"
                                 for r in rows).most_common(1)[0]
        marks.append((off, n_sh, len(rows), top[0], top[1]))
        time.sleep(SLEEP)
    print(f"探了 {len(marks)} 个页点（每 {step} 页一次，各取 {probe_size} 条）")
    print()
    print(f"  {'记录偏移':>10}  {'书画/样':>8}  主导类目")
    for off, n_sh, n, cat, c in marks:
        bar = "█" * int(n_sh / max(1, probe_size) * 20)
        print(f"  {off:>10,}  {n_sh:>3}/{n:<4}  {cat[:30]:<32} {bar}")
    hot = [off for off, n_sh, n, _, _ in marks if n_sh * 2 >= n]
    if hot:
        runs, start = [], hot[0]
        for a, b in zip(hot, hot[1:] + [None]):
            if b is None or b - a > stride:
                runs.append((start, a + stride)); start = b
        print()
        print("书画过半的记录区间（sweep 只扫这些）:")
        tot = 0
        for a, b in runs:
            n_pg = (b - a) // PAGE_SIZE
            print(f"   偏移 {a:>9,}–{b:<9,}  约 {b-a:>7,} 条 = {n_pg} 次请求")
            tot += n_pg
        print()
        print(f"合计约 {tot} 次请求，对比盲扫 {1860076 // PAGE_SIZE:,} 次")
    else:
        print()
        print("未见书画过半的页段——聚簇不明显，抽样策略需重估")
    return marks


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


def sweep(lo, hi):
    """lo／hi 是**记录偏移**，不是页号。"""
    """只扫指定页段。**这是 map 的用处所在。**

    实测总目按类聚簇: 绘画住在约第 1900–2100 与 5400–5800 页，碑帖在 7900–9200，
    其余各段书画为零。所以取绘画只需约 600 次请求，**而非盲扫 9,301 次**——
    少两个数量级，也少给站方两个数量级的负担。

    先 map 后 sweep 是本工具的正当用法; 直接 sweep 全域等于没做 map。
    """
    CACHE_S = CACHE.with_name(".dpm-sweep.json")
    got, seen = [], set()
    if CACHE_S.exists():
        got = json.loads(CACHE_S.read_text(encoding="utf-8")).get("rows") or []
        seen = {r.get("uuid") for r in got}
        print(f"已有扫描缓存 {len(got)} 条，继续")
    for off in range(lo - lo % PAGE_SIZE, hi + 1, PAGE_SIZE):
        p = off // PAGE_SIZE + 1
        try:
            d = _at(off, PAGE_SIZE)
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
            print(f"第 {p} 页无数据，停")
            break
        for r in rows:
            if r.get("uuid") not in seen:
                seen.add(r.get("uuid"))
                got.append(r)
        if (off - lo) % (25 * PAGE_SIZE) == 0:
            n_sh = sum(1 for r in got
                       if (r.get("suggestCategoryName") or "") in SHUHUA)
            print(f"  第 {p} 页 · 累计 {len(got)} 条 · 其中书画 {n_sh}", flush=True)
        time.sleep(SLEEP)
    CACHE_S.write_text(json.dumps({"rows": got}, ensure_ascii=False), encoding="utf-8")
    n_sh = sum(1 for r in got if (r.get("suggestCategoryName") or "") in SHUHUA)
    print(f"扫描缓存 {len(got)} 条 → {CACHE_S.name}；其中书画 {n_sh} 条")


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


def xcheck():
    """拿本机已抓的总目缓存，回核库内声明的故宫（北京）藏品号。**不发一次请求。**

    这一步是「按藏品号对账、不按标题对账」的机械化：总目给的是号与馆方定名，
    库内条目给的是号与本库作者名，两边只能靠号对齐，靠题名对不齐（馆方作
    「锦鸡芙蓉图」而本库作「芙蓉锦鸡图」一类，字形差异一项就够漏检）。

    报三类，且**三类的证据强度截然不同，不可混为一谈**：
      号查不到   缓存只是抽样（约 3%），查不到基本等于「不在样本里」，
                 **不是错号**。故只列出待查，不作断言——这一条最容易被误读成
                 结论，实测 10 条里已知至少一条（新00146088）另有核实来源。
      作者名不合 号在总目、但本库作者名不出现在馆方定名内。**这里才可能藏真错**:
                 髡残《茂林秋树图》曾占用董源《龙宿郊民图》的 故畫000894，
                 就是这一类（见 b91fa60）。但也有大量假阳性——
                 馆方用本名而本库用字号（任颐／任伯年、吴俊卿／吴昌硕），
                 故先用 name_alt 归一再报。
      馆方不具名 馆方定名以「清人画」「宋人」一类起头，即馆方本身未具作者，
                 而本库挂了具体作者。**这不是错，是归属分歧**，须并陈而非改字段。
    """
    import glob
    cat = {}
    for fn in (CACHE, CACHE.with_name(".dpm-sweep.json")):
        if not fn.exists():
            continue
        for r in json.loads(fn.read_text(encoding="utf-8")).get("rows") or []:
            no = (r.get("culturalRelicNo") or "").strip()
            if no:
                cat.setdefault(no, r.get("name") or "")
    if not cat:
        print("总目缓存为空，先跑 map / sweep"); return
    root = Path(__file__).parent / "data"
    names = {}
    for f in glob.glob(str(root / "artists" / "*.json")):
        a = json.loads(Path(f).read_text(encoding="utf-8"))
        # 候选名 = 正名 ＋ name_alt 里「名／初名／又名／改名」后跟的汉字串。
        # 馆方定名多用本名，本库多用行世的字号，不归一就会把同一人报成不合。
        cand = {a.get("name") or ""}
        for s in a.get("name_alt") or []:
            cand |= set(re.findall(r"(?:初名|又名|改名|本名|名)([一-鿿]{1,3})", str(s)))
        names[a["id"]] = {c for c in cand if c}
    ANON = ("清人", "宋人", "元人", "明人", "唐人", "五代人", "佚名", "无款")
    miss, bad, anon, ok = [], [], [], 0
    for f in sorted(glob.glob(str(root / "works" / "*.json"))):
        w = json.loads(Path(f).read_text(encoding="utf-8"))
        hold = str(w.get("holder") or "")
        if "故宫博物院" not in hold or "台北" in hold:
            continue
        for b in (w.get("sections", {}).get("basics") or []):
            if not isinstance(b, dict) or b.get("t") != "kv":
                continue
            for row in b.get("rows") or []:
                if not (isinstance(row, list) and len(row) == 2):
                    continue
                if not re.search(r"藏品号|藏品號", str(row[0])):
                    continue
                m = re.match(r"((?:故|新|书|書|标|资)\d{8}(?:-\d+/\d+)?)", str(row[1]).strip())
                if not m:
                    continue
                no = m.group(1)
                if no not in cat:
                    miss.append((w["id"], no)); continue
                off = cat[no]
                cand = names.get(w.get("artist") or "", set())
                if not cand or any(c in off for c in cand):
                    ok += 1
                elif off.startswith(ANON):
                    anon.append((w["id"], no, sorted(cand)[0], off, "馆方未具名"))
                elif [o for oid, ocs in names.items() if oid != w.get("artist")
                      for o in ocs if len(o) > 1 and o in off]:
                    # 馆方定名里出现的是**另一位本库在册画家**的名字，而非无主之作。
                    # 这不是错号，是两说各指一人的归属分歧——《文苑图》馆方作
                    # 「韩滉文苑图卷」而本库归周文矩，即此形。须并陈，不改字段，
                    # 更不该混进「须查」里当成抄错号来处理。
                    other = sorted({o for oid, ocs in names.items() if oid != w.get("artist")
                                    for o in ocs if len(o) > 1 and o in off})[0]
                    anon.append((w["id"], no, sorted(cand)[0], off, f"馆方归「{other}」"))
                else:
                    bad.append((w["id"], no, sorted(cand)[0], off))
    print(f"总目缓存 {len(cat)} 号 · 库内声明故宫（北京）号 {ok+len(miss)+len(bad)+len(anon)} 条 · 对得上 {ok}\n")
    print(f"[待查] 号不在缓存样本内 {len(miss)} 条——**抽样所限，不等于错号**")
    for i, n in miss:
        print(f"    {i:<40} {n}")
    print(f"\n[归属分歧] 馆方与本库各指其人，或馆方未具名 {len(anon)} 条——并陈，勿改字段")
    for i, n, c, o, why in anon:
        print(f"    {i:<32} 本库「{c}」 {n}  {why}  馆方「{o}」")
    print(f"\n[★须查] 号在总目而作者名与馆方定名不合 {len(bad)} 条——真错藏在这一类")
    for i, n, c, o in bad:
        print(f"    {i:<34} 本库「{c}」 {n}  馆方「{o}」")


def main():
    ap = argparse.ArgumentParser(description="故宫藏品总目抽样索引（不做全量）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    s = sub.add_parser("sample"); s.add_argument("--pages", type=int, default=60)
    m = sub.add_parser("map"); m.add_argument("--step", type=int, default=100)
    w = sub.add_parser("sweep")
    w.add_argument("--from", dest="lo", type=int, required=True, help="起始记录偏移")
    w.add_argument("--to", dest="hi", type=int, required=True, help="结束记录偏移")
    sub.add_parser("stats")
    sub.add_parser("xcheck")
    a = ap.parse_args()
    {"probe": probe, "sample": lambda: sample(a.pages),
     "map": lambda: map_space(a.step),
     "sweep": lambda: sweep(a.lo, a.hi), "stats": stats,
     "xcheck": xcheck}[a.cmd]()


if __name__ == "__main__":
    main()

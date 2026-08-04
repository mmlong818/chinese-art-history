"""本机 Eagle 图库索引：把四万张收藏变成可查的参考层与覆盖缺口清单。

    python eagle.py index              # 扫库建索引（约四万条，只做一次，落缓存）
    python eagle.py find "四羊方尊"     # 查本地有哪些图
    python eagle.py match              # 与 wiki 作品条目对照：哪些条目有本地图可参考
    python eagle.py gaps               # 本地有大量图而 wiki 无条目的 → 覆盖缺口

—— 两种用途，必须分开，分界线与 schema.py 里那三条取图边界是同一条 ——

**一、可发布：二维书画与拓本的忠实翻拍。**
平面公版作品的忠实复制不产生新的著作权，所以「绘画」「书法」「◯◯博物馆藏书画」
一类目录下的扫描件，无论谁扫的，都不构成新的权利。这批可作图版候选。

**二、仅供参考，不可发布：三维器物与遗址的实拍照片。**
本库里绝大多数青铜器、陶瓷、石窟照片出自「倦游人·拿破破的相册」「动脉影的相册」
两位摄影者——**他们是可指名的真实创作者，那些照片是他们的作品。**
器物本身早已进入公有领域，拍它的照片不是。把这些图发到公开站上就是侵权，
而且侵的是个人而非机构。**站点非营利不改变这一点**：中国著作权法的合理使用
是列举式的，公开传播全图不在其中。

所以本工具默认**不导出任何图片**，只产出索引与清单。要用某张三维实拍，
须先取得摄影者授权，那是本工具管不了的事。

—— 分辨率：已查到底，不必再查 ——

**中国书画那批的天花板是 600px，且上游也没有更大的。**实测 2026-08-04：
本地文件即原图（元数据宽高与直读 JPEG 头一致，不是缩略图）；文件名形如
`p418831760` 是豆瓣照片 ID，取豆瓣图床各档比对——
`/l`（大图）600×336、`/raw`（原始）600×336 **且字节数与本地完全相同**
（104,129 B）、`/m` 540×302。即上传者当初上传的就是 600px。

1209 张平面翻拍里 1097 张不足 800px 宽，够作图版（≥2000px）的只有 16 张。
而本库生成器用 800px 缩略、1600px 放大档——**这批素材连缩略图尺寸都不够。**

对照：同库「埃及高清照片」是 7907×5274 的真高清。**所以这不是 Eagle 的限制，
是当初收集来源决定的**：中国书画从豆瓣相册存，埃及那批是成套高清图库。

微博那 9664 张实拍，sinaimg.cn 理论上有 large／original 档可试，但**那批是
两位摄影者的作品，分辨率不是障碍、权属才是**，所以没有去试的意义。

—— 参考用途本身价值极大，不要低估 ——

「青铜器」一个顶层目录下有 274 个子目录，逐件点名：伯格尊、曾侯乙尊盘、何尊、
利簋、虢季子白盘、天亡簋、云纹铜禁、中山王厝壶、作册般鼋形器……
**这是一份「一部完备的中国美术史该收哪些器」的清单**，而本库现有青铜条目不到二十。
写条目时能看到实物，与只凭文献写，是两回事。
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


LIB = Path(r"E:\照片.library")
WIKI = Path(__file__).parent
CACHE = WIKI / ".eagle-index.json"      # 含本机路径，不入库（见 .gitignore）

# 本库范围是**中国**美术史。图库里另有大量无关素材（DND 怪物图、LOTR、AI 作画、
# 游戏地图、桌面壁纸）与虽属艺术但越界的材料，一并排除。
# 尤其注意「东方艺术」顶层**不是中国书画**：其四个子目录是 Datacraft Sozaijiten
# 京都素材集、日本建筑照片、念佛宗无量寿寺——全是日本材料。名字容易误导。
IN_SCOPE_TOPS = {"博物馆", "青铜器", "绘画", "书法"}
OUT_OF_SCOPE = re.compile(r"DND|LOTR|monster|Monster|AI作画|AI绘画|游戏地图|桌面|"
                          r"素材|废图|临时|KLING|luma|vidu|runway|Gen-3|girl|"
                          r"Datacraft|Sozaijiten|Japanese|念佛宗|念仏宗|埃及|"
                          r"技法|蜗牛|花语|节气|哈利波特|蜡像馆|卡地亚|珠宝展")

# 二维（忠实翻拍不生新著作权，可作图版候选）——按顶层目录与目录名关键词判
FLAT_TOPS = {"绘画", "书法"}
FLAT_KEY = re.compile(r"书画|绘画|书法|碑帖|刻本|拓|册页|图卷|图轴|图册|千字文|"
                      r"墨迹|尺牍|题跋|字画|画册")
# 三维实拍（摄影者有著作权，仅供参考）
PHOTO_KEY = re.compile(r"青铜|陶瓷|玉器|金银器|石刻|石窟|造像|漆器|俑|窖藏|墓|"
                       r"遗址|建筑|壁画|丝绸|织|印|砖|瓦")


def _folder_paths(meta):
    """folder id → 从顶层到本层的名称路径。"""
    out = {}

    def walk(fs, path=()):
        for f in fs:
            p = path + (f["name"],)
            out[f["id"]] = p
            walk(f.get("children") or [], p)

    walk(meta.get("folders") or [])
    return out


def classify(paths):
    """判这张图是「可发布的平面翻拍」还是「仅供参考的实拍」。

    判不出来一律归 photo——**宁可把可用的误判为不可用，不可反过来**。
    误判成不可用只是少一张图；误判成可用就是拿别人的作品去发布。

    **必须先过范围闸再判平面**：`FLAT_KEY` 里的「绘画」会把顶层目录「AI绘画」
    一并匹配上，「世界绘画大师动物画技法」也一样——第一版就是这么把三千多张
    AI 生成图与动物画教程误判成「可发布的中国书画翻拍」的。
    宽正则撞上相似目录名，这个坑本项目踩过不止一次。
    """
    top = {p[0] for p in paths if p}
    if not (top & IN_SCOPE_TOPS):
        return "photo"                    # 范围外一律不作图版候选
    joined = " / ".join(" ".join(p) for p in paths)
    if OUT_OF_SCOPE.search(joined) or PHOTO_KEY.search(joined):
        return "photo"
    # 图库里**有两个都叫「绘画」的顶层目录**：一个装欧美当代画家（Iris Scott、
    # sparth 等），一个装中国书画（陈洪绶、钱选、黄公望）。靠目录名分不开，
    # 故要求末级目录名含汉字——中国书画的目录名不会是纯拉丁。
    leaf_has_cjk = any(re.search(r"[一-鿿]", p[-1]) for p in paths if p)
    if not leaf_has_cjk:
        return "photo"
    if (top & FLAT_TOPS) or FLAT_KEY.search(joined):
        return "flat"
    return "photo"


def build():
    meta = json.loads((LIB / "metadata.json").read_text(encoding="utf-8"))
    fp = _folder_paths(meta)
    items, n, skipped = [], 0, 0
    for d in (LIB / "images").iterdir():
        p = d / "metadata.json"
        if not p.is_file():
            continue
        try:
            x = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        if x.get("isDeleted"):
            continue
        n += 1
        paths = [fp[f] for f in (x.get("folders") or []) if f in fp]
        items.append({
            "id": x.get("id"), "name": x.get("name") or "",
            "ext": x.get("ext"), "w": x.get("width") or 0, "h": x.get("height") or 0,
            "tags": x.get("tags") or [],
            "paths": ["/".join(t) for t in paths],
            "url": x.get("url") or "",
            "use": classify(paths),
            "file": str(d / f'{x.get("name")}.{x.get("ext")}'),
        })
        if n % 5000 == 0:
            print(f"  …{n}", flush=True)
    CACHE.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    c = collections.Counter(i["use"] for i in items)
    print(f"\n索引 {len(items)} 条（跳过 {skipped} 条读取失败）→ {CACHE.name}")
    print(f"  可发布候选（平面翻拍）flat：{c['flat']}")
    print(f"  仅供参考（实拍）    photo：{c['photo']}")
    return items


def load():
    if not CACHE.exists():
        sys.exit("尚无索引，先跑 python eagle.py index")
    return json.loads(CACHE.read_text(encoding="utf-8"))["items"]


def find(q, limit=30):
    items = load()
    hit = [i for i in items
           if q in i["name"] or q in " ".join(i["tags"]) or q in " ".join(i["paths"])]
    flat = [i for i in hit if i["use"] == "flat"]
    print(f"「{q}」命中 {len(hit)} 张（其中平面翻拍 {len(flat)} 张可作图版候选）\n")
    for i in hit[:limit]:
        mark = "可发布" if i["use"] == "flat" else "仅参考"
        print(f"  [{mark}] {i['w']}×{i['h']:<6} {i['name'][:44]}")
        print(f"           {i['paths'][0] if i['paths'] else '(无目录)'}")
    if len(hit) > limit:
        print(f"  …另 {len(hit)-limit} 张")


def _wiki_works():
    out = []
    for f in (WIKI / "data" / "works").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append((d["id"], d.get("title") or "", bool(d.get("image")),
                    d.get("image_status")))
    return out


def match():
    """本库作品条目 × 本机图库。重点看**现在没图的条目里，哪些本地有料**。"""
    items = load()
    idx = collections.defaultdict(list)
    for i in items:
        key = i["name"] + " " + " ".join(i["tags"]) + " " + " ".join(i["paths"])
        idx[i["use"]].append((key, i))
    rows = []
    for wid, title, has_img, st in sorted(_wiki_works()):
        t = re.sub(r"[《》〈〉·、（）()]", "", title)
        core = t[:4] if len(t) >= 4 else t
        if not core:
            continue
        flat = sum(1 for k, _ in idx["flat"] if core in k)
        photo = sum(1 for k, _ in idx["photo"] if core in k)
        if flat or photo:
            rows.append((has_img, st or "", wid, title, flat, photo))
    rows.sort(key=lambda r: (r[0], -(r[4] * 10 + r[5])))
    print(f"{len(rows)} 个作品条目在本机图库有同名素材：\n")
    print(f"  {'现状':<14} {'条目':<34} {'平面':>4} {'实拍':>4}  标题")
    for has, st, wid, title, flat, photo in rows:
        cur = "已有图" if has else (st or "无图")
        print(f"  {cur:<14} {wid:<34} {flat:>4} {photo:>4}  {title[:26]}")


def gaps(least=12):
    """本机图库里成规模而 wiki 无对应条目的题目 —— 覆盖缺口清单。

    这是本工具最有价值的输出：「青铜器」下 274 个子目录逐件点名，
    而本库现有青铜条目不到二十。缺口不是靠想出来的，是靠比出来的。
    """
    items = load()
    have = " ".join(t for _, t, _, _ in _wiki_works())
    cnt, top_of = collections.Counter(), {}
    for i in items:
        for p in i["paths"]:
            parts = p.split("/")
            if parts[0] not in IN_SCOPE_TOPS:
                continue
            leaf = re.sub(r"^(倦游人·拿破破的相册-|动脉影的相册-|[A-Za-z_]+的相册-)",
                          "", parts[-1])
            if OUT_OF_SCOPE.search(leaf) or len(leaf) < 2:
                continue
            cnt[leaf] += 1
            top_of[leaf] = parts[0]
    miss = [(n, c) for n, c in cnt.most_common()
            if c >= least and not (len(n) >= 3 and n[:3] in have)]
    print(f"本机成规模（≥{least} 张）而本库尚无对应条目的题目 {len(miss)} 项")
    print("（已限定在博物馆／青铜器／绘画／书法四个顶层，"
          "已剔 DND、AI 作画、日本素材、埃及等越界材料）\n")
    for n, c in miss[:90]:
        print(f"  {c:>5}  [{top_of[n]}] {n}")
    if len(miss) > 90:
        print(f"  …另 {len(miss)-90} 项")


def main():
    ap = argparse.ArgumentParser(description="本机 Eagle 图库索引（只读，不导出图片）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    f = sub.add_parser("find"); f.add_argument("q"); f.add_argument("--limit", type=int, default=30)
    sub.add_parser("match")
    g = sub.add_parser("gaps"); g.add_argument("--least", type=int, default=12)
    a = ap.parse_args()
    {"index": lambda: build(), "find": lambda: find(a.q, a.limit),
     "match": match, "gaps": lambda: gaps(a.least)}[a.cmd]()


if __name__ == "__main__":
    main()

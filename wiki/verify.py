"""事实核查通道。查生卒年、器物年代、现藏、尺寸、判权——不占工具配额。

    python verify.py person "八大山人"        # 人：生卒、别名、身份
    python verify.py object "后母戊鼎"        # 物：年代、现藏、材质、尺寸
    python verify.py holdings "ding tripod"   # 在西方馆的中国藏品里检索（含判权与图）

与 fetchimg.py 一样直接走 urllib，不经过任何受配额的工具。子代理的检索配额
一耗尽就会退回凭记忆写作，而记忆产出的年代与尺寸在 JSON 里跟核实过的一模一样——
对一个以可追溯为前提的库，这是最危险的失效方式：不报错，只是悄悄变得不可信。
所以撰写阶段必须永远有一条核查通道可用。

—— 一个必须正视的不对称（2026-08-04 逐一实测）——

可编程核查的：
    克利夫兰美术馆   完整 API，中国部藏品，share_license_status 直接给 CC0，图像带宽高
    大都会博物馆     检索 + 单件 API，亚洲部 departmentId=6，isPublicDomain 判权
    哈佛艺术博物馆   IIIF manifest 公开可读
    Wikidata        中文标签检索可用，作 tier 4 交叉参考

**无法编程核查的**：
    故宫博物院、中国国家博物馆、数字敦煌  —— 仅有 HTML 页面，无公开 API
    台北故宫、上海博物馆                  —— 实测 SSL 证书链失败／握手超时

后果是明确的：**最重要的中国文物恰恰在北京与台北**。后母戊鼎、《清明上河图》、
汝窑天青釉洗，既无 API 可核，也无自由授权图像；而流散在西方馆的中国文物反倒数据齐备。
所以本库对这两类采取不同待遇——西方馆藏可标 verification=museum 并取图；
中国馆藏一律记下官方藏品页 URL 供人工核对，verification 最高只能标 wikidata，
图像按 image_status 说明为何缺失。**这个不对称是史实与现实，不是编纂偷懒。**
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

# Windows 终端默认走本地码页，中文输出会变乱码——一个看不懂的报错等于没报错。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


UA = "china-art-history-archive/0.1 (build-time fact check; contact via repo)"
WD_API = "https://www.wikidata.org/w/api.php"
CMA_API = "https://openaccess-api.clevelandart.org/api/artworks/"
MET_API = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_ASIAN_DEPT = 6

# Wikidata 属性。P 号稳定，可硬编码。
P = {
    "P569": "生年", "P570": "卒年", "P19": "出生地", "P106": "身份",
    "P1559": "母语名", "P742": "笔名/别号", "P27": "国籍",
    "P571": "年代", "P276": "所在地", "P195": "收藏", "P186": "材质",
    "P2048": "高(cm)", "P2049": "宽(cm)", "P217": "藏品号", "P31": "类型",
}

PERSON_KEYS = ["P569", "P570", "P19", "P27", "P106", "P742"]
OBJECT_KEYS = ["P31", "P571", "P195", "P276", "P186", "P2048", "P2049", "P217"]

# 中国馆藏机构：命中这些说明无 API 可核，需人工查官方页
CN_HOLDERS = ("故宫", "国家博物馆", "上海博物馆", "南京博物院", "陕西历史",
              "河南博物院", "湖北省博物馆", "浙江省博物馆", "辽宁省博物馆",
              "台北", "敦煌", "云冈", "龙门", "麦积山", "大足")


def _get(url, tries=4, raw=False):
    """带退避重试。一次 report 会连打十几个请求，对方限流就整条查询失败，比慢几秒糟得多。"""
    delay = 1.5
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                b = r.read()
                return b if raw else json.loads(b.decode("utf-8"))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2


# ── Wikidata ──────────────────────────────────────────────────────────────

_LABELS = {}


def _label(qid):
    """标签逐个查很费请求，同一个 QID（材质「青铜」）会反复出现，故进程内缓存。"""
    if qid in _LABELS:
        return _LABELS[qid]
    try:
        e = _wd_entity(qid)
        lab = e.get("labels", {})
        out = (lab.get("zh") or lab.get("zh-hans") or lab.get("en") or {}).get("value", qid)
    except Exception:
        out = qid                      # 查不到就把 QID 原样交出，别假装知道
    _LABELS[qid] = out
    return out


def _wd_search(term, limit=8):
    for lang in ("zh", "en"):
        q = urllib.parse.urlencode({
            "action": "wbsearchentities", "search": term, "language": lang,
            "uselang": lang, "format": "json", "limit": str(limit), "type": "item"})
        hits = _get(f"{WD_API}?{q}").get("search", [])
        if hits:
            return hits
    return []


def _wd_entity(qid):
    q = urllib.parse.urlencode({
        "action": "wbgetentities", "ids": qid, "format": "json",
        "props": "claims|labels|descriptions", "languages": "zh|zh-hans|en"})
    return _get(f"{WD_API}?{q}")["entities"][qid]


def _wd_val(claim):
    dv = claim.get("mainsnak", {}).get("datavalue")
    if not dv:
        return None
    v, t = dv.get("value"), dv.get("type")
    if t == "time":
        s = str(v.get("time", ""))
        neg = s.startswith("-")
        y = s.lstrip("+-").split("-")[0].lstrip("0") or "0"
        y = f"前 {y}" if neg else y
        if v.get("precision", 11) <= 9:
            return y
        parts = s.lstrip("+-").split("T")[0].split("-")
        return f"{y}-{parts[1]}-{parts[2]}" if len(parts) > 2 else y
    if t == "wikibase-entityid":
        return _label(v.get("id"))
    if t == "quantity":
        return str(v.get("amount", "")).lstrip("+")
    if t == "monolingualtext":
        return v.get("text")
    return str(v)


def wikidata(term, keys, require=()):
    hits = _wd_search(term)
    if not hits:
        print(f"  Wikidata 无匹配：{term!r}")
        return None
    pick, claims = None, {}
    for h in hits:
        cl = _wd_entity(h["id"]).get("claims", {})
        if not require or any(p in cl for p in require):
            pick, claims = h, cl
            break
    if pick is None:
        print(f"  Wikidata 检索到 {len(hits)} 条同名，但无一带 {list(require)} 属性——"
              f"可能都不是要找的那类。候选："
              + "  ".join(f'{x["id"]}:{x.get("description","")[:22]}' for x in hits[:4]))
        return None
    print(f'  {pick["id"]}  {pick.get("label","")} — {pick.get("description","")}')
    print(f'  https://www.wikidata.org/wiki/{pick["id"]}')
    for pid in keys:
        if pid in claims:
            vals = [v for v in (_wd_val(c) for c in claims[pid][:4]) if v]
            if vals:
                print(f'    {P[pid]:<8} {" / ".join(vals)}')
    missing = [P[k] for k in keys if k not in claims]
    if missing:
        print(f'    （无此项：{"、".join(missing)}）')
    return claims


# ── 西方馆藏：可编程核查且多为 CC0 ────────────────────────────────────────


def cleveland(q, limit=5):
    """克利夫兰美术馆中国部。实测 share_license_status 直接给 CC0，图像带宽高。"""
    url = CMA_API + "?" + urllib.parse.urlencode({
        "q": q, "department": "Chinese Art", "limit": str(limit),
        "fields": "id,title,creation_date,culture,technique,measurements,"
                  "share_license_status,creditline,images,accession_number"})
    data = _get(url).get("data", [])
    if not data:
        print("  克利夫兰：无匹配")
        return
    for a in data:
        web = (a.get("images") or {}).get("web") or {}
        cul = (a.get("culture") or [""])[0]
        print(f'  CMA {a.get("accession_number","?"):>12}  {str(a.get("title"))[:34]:36} '
              f'{str(a.get("creation_date"))[:16]:18} {a.get("share_license_status","?"):4} '
              f'{"图 " + str(web.get("width")) + "×" + str(web.get("height")) if web else "无图"}')
        if cul:
            print(f'                 文化断代：{cul[:66]}')


def met(q, limit=5):
    """大都会亚洲部（departmentId=6）。isPublicDomain 是判权字段。"""
    url = f"{MET_API}/search?" + urllib.parse.urlencode(
        {"departmentId": MET_ASIAN_DEPT, "q": q})
    ids = (_get(url).get("objectIDs") or [])[:limit]
    if not ids:
        print("  大都会：无匹配")
        return
    for oid in ids:
        try:
            o = _get(f"{MET_API}/objects/{oid}")
        except Exception:
            continue
        pd = "PD" if o.get("isPublicDomain") else "受限"
        print(f'  MET {o.get("accessionNumber","?"):>12}  {str(o.get("title"))[:34]:36} '
              f'{str(o.get("objectDate"))[:16]:18} {pd:4} '
              f'{"有图" if o.get("primaryImage") else "无图"}')
        if o.get("culture") or o.get("dynasty"):
            print(f'                 {o.get("culture","")} {o.get("dynasty","")}'.rstrip()[:76])


def holdings(q):
    print("西方馆藏中国文物检索（可编程核查、多为 CC0／PD）：")
    for fn in (cleveland, met):
        try:
            fn(q)
        except Exception as ex:
            print(f"  {fn.__name__} 失败：{type(ex).__name__}")


# ── 主入口 ────────────────────────────────────────────────────────────────

TIER_NOTE = ("\n  注：Wikidata 属 tier 4（同百科），仅用于抓错与提供线索，不可单独作为事实依据。"
             "\n  西方馆（克利夫兰／大都会／哈佛）属 tier 1，可标 verification=museum。"
             "\n  故宫、国博、台北故宫、上博、敦煌研究院无公开 API，只能人工查官方页，"
             "\n  这类条目 verification 最高标 wikidata，并须在 image_status 说明缺图原因。")


def main():
    ap = argparse.ArgumentParser(description="中国美术史事实核查通道")
    ap.add_argument("kind", choices=["person", "object", "holdings"])
    ap.add_argument("term")
    a = ap.parse_args()

    if a.kind == "holdings":
        holdings(a.term)
        print(TIER_NOTE)
        return 0

    print(f"Wikidata（tier 4，交叉参考）：")
    if a.kind == "person":
        wikidata(a.term, PERSON_KEYS, require=("P569", "P570"))
    else:
        wikidata(a.term, OBJECT_KEYS, require=("P571", "P276", "P195"))
        print(f"\n同名物在西方馆的藏品（tier 1）：")
        holdings(a.term)
    print(TIER_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

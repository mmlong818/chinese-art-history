"""构建期取图与判权。只用标准库。

    python fetchimg.py find "ding tripod"                 # 三家馆 + Commons 一起搜
    python fetchimg.py cma 162576   --id shang-ding-cma    # 克利夫兰（CC0，首选）
    python fetchimg.py met 44022    --id yuan-blue-white   # 大都会亚洲部
    python fetchimg.py wikimedia "Houmuwu_ding.jpg" --id houmuwu-ding
    python fetchimg.py auto <作品id> [--write]              # 按条目自动取
    python fetchimg.py auto-pending --write                # 扫全部待补图

—— 两条路径，信任级别不同 ——

**馆藏 API 路径（克利夫兰／大都会／哈佛）可直接信任。**它们返回的是馆方对自己藏品的
记录：说这是商代鼎，就是它库里那件商代鼎，不存在同名张冠李戴。所以这条路不需要
作者核验，判权也直接取馆方字段（克利夫兰 share_license_status、大都会 isPublicDomain）。

**Commons 路径必须核验。**谁都能往 Commons 上传同名文件，文件名分不出原件与摹本、
分不出器物照片与论该器物的旧书扫描页。所以这条路要看 Artist 字段，并排除
`(IA `／`memorie` 一类书扫标记——这两个坑西洋库都实地踩过。

—— 一个必须正视的不对称 ——

故宫、国博、上博、台北故宫、敦煌研究院**均无公开 API**（2026-08-04 实测：前三者只有
HTML，后两者 SSL 握手失败）。而最重要的中国文物恰在这几家。所以：

    在西方馆的中国文物  → 本工具可取图，多为 CC0／PD，verification 可标 museum
    在中国馆的中国文物  → 本工具取不到，须记官方藏品页 URL 供人工核对，
                          image_status 标 no-free-image 或 in-situ

**缺图在这里往往不是编纂偷懒，是那件东西的图像权属现实。**
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Windows 终端默认走本地码页，中文输出会变乱码——一个看不懂的报错等于没报错。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


IMGDIR = Path(__file__).parent / "static" / "img"
DATA = Path(__file__).parent / "data"
UA = "china-art-history-archive/0.1 (build-time image fetch; contact via repo)"

CMA_API = "https://openaccess-api.clevelandart.org/api/artworks"
MET_API = "https://collectionapi.metmuseum.org/public/collection/v1"
MET_ASIAN_DEPT = 6
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/File:"
COMMONS_REDIR = "https://commons.wikimedia.org/w/index.php?title=Special:Redirect/file/"

CC_RE = re.compile(r"cc[\s-]*by(?:[\s-]*(sa))?[\s-]*(\d\.\d)", re.I)
# 书扫与非器物图的标记。Commons 上「Donatello - memorie, opere (IA …)」那类坑。
JUNK_RE = re.compile(r"\(IA |bub_gb|memorie|opere|guida|libro|\bplate\b|\bpage\b", re.I)


def _get(url, raw=False, tries=5):
    """带退避重试。Commons 与 Wikidata 对突发请求会回 429，也会偶发 SSL EOF；
    不重试就会大面积假失败，看着像「无自由图」，其实只是被限流。"""
    delay = 2.0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return b if raw else json.loads(b.decode("utf-8"))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _save(url, stem):
    IMGDIR.mkdir(parents=True, exist_ok=True)
    ext = ".png" if ".png" in url.lower() else ".jpg"
    path = IMGDIR / f"{stem}-800{ext}"
    path.write_bytes(_get(url, raw=True))
    return f"img/{path.name}", path.stat().st_size


def _jpeg_size(path):
    """从落盘的 JPEG／PNG 里读真实宽高，不引第三方库。

    大都会的 API **不返回图像尺寸**，若留 w=0/h=0，页面的长宽比预留就失效，
    图一到位版面就跳——而防跳动正是本库要求 w/h 必填的全部理由。
    与其填一个假比例，不如从文件本身读。
    """
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
        m = b[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return (int.from_bytes(b[i + 7:i + 9], "big"),
                    int.from_bytes(b[i + 5:i + 7], "big"))
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
        else:
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    return 0, 0


def _lic(short, terms=""):
    """归一许可标记，保留版本号。

    三维物件尤其要紧：器物本身早已进入公版，但拍它的那张照片是新的受版权作品，
    所以 Commons 上常见 CC BY-SA 而非 PD。不要把它压成 PD——那等于替摄影者
    放弃他的权利。判不出来返回 rights-review，生成器拒绝渲染。
    """
    s = f"{short or ''} {terms or ''}".strip()
    if re.search(r"public\s*domain|\bpd[\s-]", s, re.I) or s.strip().lower() == "pd":
        return "PD"
    if re.search(r"\bcc0\b|zero", s, re.I):
        return "CC0"
    m = CC_RE.search(s)
    if m:
        return f"CC BY{'-SA' if m.group(1) else ''} {m.group(2)}"
    return "rights-review"


# ── 馆藏 API：可直接信任，无摹本问题 ─────────────────────────────────────


def from_cma(oid, eid):
    """克利夫兰美术馆。实测中国部藏品多为 CC0，图像带宽高，元数据含文化断代。"""
    d = _get(f"{CMA_API}/{oid}")["data"]
    web = (d.get("images") or {}).get("web") or {}
    if not web.get("url"):
        sys.exit(f"克利夫兰 {oid} 无可用图像")
    lic = "CC0" if d.get("share_license_status") == "CC0" else "rights-review"
    thumb, size = _save(web["url"], eid)
    img = {
        "source": "cleveland",
        "source_url": f'https://www.clevelandart.org/art/{d.get("accession_number", oid)}',
        "credit": (d.get("creditline") or "Cleveland Museum of Art")[:110],
        "license": lic,
        "thumb": thumb,
        "full": ((d.get("images") or {}).get("full") or {}).get("url") or web["url"],
        "w": int(web.get("width") or 0), "h": int(web.get("height") or 0),
    }
    meta = {"title": d.get("title"), "date": d.get("creation_date"),
            "culture": (d.get("culture") or [""])[0], "technique": d.get("technique"),
            "measurements": d.get("measurements"), "accession": d.get("accession_number")}
    return img, size, meta


CN_CULTURE = re.compile(r"china|chinese|中国|tang|song|ming|qing|shang|zhou|han\b|yuan",
                        re.I)


def _is_chinese(d):
    """大都会的 departmentId=6 是「亚洲部」，涵盖全亚洲，没有「中国部」可筛。

    实测踩过：检索 Buddha 取回的 38165「Standing Buddha」是**泰国**造像。
    本库范围是中国美术史，混进日、韩、泰、印材料就是 L2 那条「地域越界」。
    所以取图前必须用 culture／dynasty／objectName 判一次产地，判不出就不取。
    克利夫兰有 department=Chinese Art 可筛，不需要这一步。
    """
    hay = " ".join(str(d.get(k) or "") for k in
                   ("culture", "dynasty", "period", "country", "region", "artistNationality"))
    return bool(CN_CULTURE.search(hay))


def from_met(oid, eid):
    """大都会博物馆。isPublicDomain 是判权字段；非公版一律不取。"""
    d = _get(f"{MET_API}/objects/{oid}")
    url = d.get("primaryImage") or d.get("primaryImageSmall")
    if not url:
        sys.exit(f"大都会 {oid} 无图像")
    if not d.get("isPublicDomain"):
        sys.exit(f"大都会 {oid} 非公版（isPublicDomain=false），本库不取")
    if not _is_chinese(d):
        sys.exit(f"大都会 {oid} 产地非中国"
                 f"（culture={d.get('culture')!r} dynasty={d.get('dynasty')!r}）——"
                 f"亚洲部含日韩泰印，本库范围是中国美术史，不取")
    thumb, size = _save(d.get("primaryImageSmall") or url, eid)
    w, h = _jpeg_size(IMGDIR / Path(thumb).name)
    img = {
        "source": "met",
        "source_url": d.get("objectURL") or f"https://www.metmuseum.org/art/collection/search/{oid}",
        "credit": (d.get("creditLine") or "The Metropolitan Museum of Art")[:110],
        "license": "PD",
        "thumb": thumb,
        "full": url,
        "w": w, "h": h,
    }
    meta = {"title": d.get("title"), "date": d.get("objectDate"),
            "culture": f'{d.get("culture","")} {d.get("dynasty","")}'.strip(),
            "technique": d.get("medium"), "measurements": d.get("dimensions"),
            "accession": d.get("accessionNumber")}
    return img, size, meta


# ── Commons：必须核验 ─────────────────────────────────────────────────────


def from_wikimedia(fname, eid):
    fname = fname.replace("File:", "").replace(" ", "_")
    q = urllib.parse.urlencode({
        "action": "query", "titles": f"File:{fname}", "prop": "imageinfo",
        "iiprop": "extmetadata|url|size", "format": "json"})
    page = next(iter(_get(f"{COMMONS_API}?{q}")["query"]["pages"].values()))
    if "imageinfo" not in page:
        sys.exit(f"Commons 无此文件：{fname}")
    ii = page["imageinfo"][0]
    md = ii.get("extmetadata", {})

    def m(k):
        return _strip((md.get(k) or {}).get("value", ""))

    thumb, size = _save(f"{COMMONS_REDIR}{urllib.parse.quote(fname)}&width=800", eid)
    tw, th = _jpeg_size(IMGDIR / Path(thumb).name)
    credit = m("Artist") or m("Credit") or "Wikimedia Commons"
    img = {
        "source": "wikimedia",
        "source_url": COMMONS_FILE + urllib.parse.quote(fname),
        "credit": f"{credit[:88]} / Wikimedia Commons",
        "license": _lic(m("LicenseShortName"), m("UsageTerms")),
        "attribution_required": (m("AttributionRequired") or "").lower() == "true",
        "thumb": thumb,
        "full": ii.get("url"),
        # w/h 描述的是 thumb 那个文件，不是 Commons 原图。
        # 原图尺寸与缩略图长宽比相同，故不影响 aspect-ratio 预留；但 from_met／from_cma
        # 记的都是落盘文件的实测尺寸，此处若填原图尺寸就成了三条路径各说一套。
        # 另：Commons 的 width= 会吸附到固定档位（要 800 实得 960），所以只能实测。
        "w": tw or ii.get("width"), "h": th or ii.get("height"),
    }
    return img, size, {"title": fname, "date": m("DateTimeOriginal")}


# ── 检索：三家馆 + Commons 一起看 ─────────────────────────────────────────


def find(q):
    print(f"检索「{q}」\n")
    print("── 克利夫兰美术馆 · 中国部（CC0 首选，可直接信任）")
    try:
        url = f"{CMA_API}?" + urllib.parse.urlencode({
            "q": q, "department": "Chinese Art", "limit": "6",
            "fields": "id,title,creation_date,culture,share_license_status,images,accession_number"})
        data = _get(url).get("data", [])
        for a in data or []:
            web = (a.get("images") or {}).get("web") or {}
            print(f'  cma {a["id"]:<8} {str(a.get("title"))[:30]:32} '
                  f'{str(a.get("creation_date"))[:14]:16} {a.get("share_license_status","?"):4} '
                  f'{str(web.get("width","?"))}×{str(web.get("height","?"))}')
            cul = (a.get("culture") or [""])[0]
            if cul:
                print(f'                 {cul[:70]}')
        if not data:
            print("  无匹配")
    except Exception as ex:
        print(f"  失败 {type(ex).__name__}")

    print("\n── 大都会博物馆 · 亚洲部")
    try:
        ids = (_get(f"{MET_API}/search?" + urllib.parse.urlencode(
            {"departmentId": MET_ASIAN_DEPT, "q": q})).get("objectIDs") or [])[:6]
        for oid in ids:
            try:
                o = _get(f"{MET_API}/objects/{oid}")
            except Exception:
                continue
            cn = _is_chinese(o)
            print(f'  met {oid:<8} {str(o.get("title"))[:30]:32} '
                  f'{str(o.get("objectDate"))[:14]:16} '
                  f'{"PD" if o.get("isPublicDomain") else "受限":4} '
                  f'{"有图" if o.get("primaryImage") else "无图":4} '
                  f'{(str(o.get("culture") or o.get("dynasty") or "?"))[:16] if cn else "← 非中国，不取"}')
        if not ids:
            print("  无匹配")
    except Exception as ex:
        print(f"  失败 {type(ex).__name__}")

    print("\n── Wikimedia Commons（须核作者，可能是摹本或书扫页）")
    try:
        p = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": q, "srnamespace": "6",
            "srlimit": "6", "format": "json"})
        hits = _get(f"{COMMONS_API}?{p}")["query"]["search"]
        if hits:
            titles = "|".join(h["title"] for h in hits)
            p2 = urllib.parse.urlencode({
                "action": "query", "titles": titles, "prop": "imageinfo",
                "iiprop": "extmetadata|size", "format": "json"})
            pages = _get(f"{COMMONS_API}?{p2}")["query"]["pages"]
            meta = {}
            for pg in pages.values():
                ii = (pg.get("imageinfo") or [{}])[0]
                md = ii.get("extmetadata", {})
                meta[pg["title"]] = (
                    _strip((md.get("Artist") or {}).get("value", ""))[:24],
                    _lic(_strip((md.get("LicenseShortName") or {}).get("value", ""))),
                    f'{ii.get("width","?")}×{ii.get("height","?")}')
            for h in hits:
                nm = h["title"].replace("File:", "")
                a, lic, wh = meta.get(h["title"], ("?", "?", "?"))
                flag = " ← 疑书扫页" if JUNK_RE.search(nm) else ""
                print(f'  {nm[:46]:48} {a:26} {lic:14} {wh}{flag}')
        else:
            print("  无匹配")
    except Exception as ex:
        print(f"  失败 {type(ex).__name__}")

    print("\n注：故宫、国博、上博、台北故宫、敦煌研究院均无公开 API，本工具取不到。"
          "\n    那几家的藏品须记官方页 URL 供人工核对，并按 image_status 说明缺图原因。")


# ── 按条目自动取图 ────────────────────────────────────────────────────────


def auto(work_id, write=False):
    """按作品条目自动取图。

    优先级：条目已记 cma_id / met_id 就直接走馆藏 API（可信任）；
    否则拿标题去 Commons 搜，并核作者与书扫标记。
    """
    wp = DATA / "works" / f"{work_id}.json"
    if not wp.exists():
        print(f"  {work_id}: 无此作品条目")
        return False
    w = json.loads(wp.read_text(encoding="utf-8"))

    got = None
    try:
        if w.get("cma_id"):
            got = from_cma(w["cma_id"], work_id)
        elif w.get("met_id"):
            got = from_met(w["met_id"], work_id)
    except SystemExit as ex:
        print(f"  {work_id}: {ex}")
        return False
    except Exception as ex:
        print(f"  {work_id}: 馆藏 API 失败 {type(ex).__name__}")
        return False

    if got is None:
        # 无馆藏 id，只能试 Commons——须核作者与书扫标记
        term = w.get("title_orig") or w.get("title")
        try:
            p = urllib.parse.urlencode({
                "action": "query", "list": "search", "srsearch": term,
                "srnamespace": "6", "srlimit": "8", "format": "json"})
            hits = _get(f"{COMMONS_API}?{p}")["query"]["search"]
        except Exception as ex:
            print(f"  {work_id}: Commons 检索失败 {type(ex).__name__}")
            return False
        for h in hits:
            nm = h["title"].replace("File:", "")
            if JUNK_RE.search(nm):
                continue
            try:
                got = from_wikimedia(nm, work_id)
            except SystemExit:
                continue
            except Exception:
                continue
            if got[0]["license"] == "rights-review":
                got = None
                continue
            break
        if got is None:
            print(f"  {work_id}: Commons 无可用候选（检索词：{str(term)[:30]}），"
                  f"若原物在国内馆藏属正常，请标 image_status")
            return False

    img, size, meta = got
    img["alt"] = w.get("title", "")
    print(f'  {work_id}: {img["source"]} · {img["license"]} · {size // 1024}KB'
          f'{" · " + str(meta.get("culture"))[:34] if meta.get("culture") else ""}')
    if write:
        w["image"] = img
        w.pop("image_status", None)
        wp.write_text(json.dumps(w, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def auto_pending(write=False):
    d = DATA / "works"
    if not d.exists():
        print("尚无 data/works/")
        return
    todo = []
    for f in sorted(d.glob("*.json")):
        obj = json.loads(f.read_text(encoding="utf-8"))
        if not obj.get("image") and obj.get("image_status") in (None, "pending"):
            todo.append(f.stem)
    print(f"待补图 {len(todo)} 件{'' if write else '（试运行，未回写）'}\n")
    ok = 0
    for i, t in enumerate(todo):
        if i:
            time.sleep(1.5)          # 主动限速，别把对方打到 429 再靠重试救
        try:
            ok += bool(auto(t, write))
        except Exception as ex:
            print(f"  {t}: 放弃（{type(ex).__name__}）")
    print(f"\n成功 {ok} / {len(todo)}")


ARTIC_API = "https://api.artic.edu/api/v1/artworks"
ARTIC_IIIF = "https://www.artic.edu/iiif/2"


def from_artic(oid, eid):
    """芝加哥艺术博物馆。走 IIIF，判权看 is_public_domain。

    与克利夫兰同属「馆藏 API 可直接信任」那一类：它说这是中国商代鼎，
    就是它库里那件，不存在同名张冠李戴。但**产地仍要过一道**——
    该馆全文检索「China」会把法国、英国 Burslem（陶瓷产地，「china」即瓷器）
    与美国的东西一并带回来，实测 12 件里只有 1 件是中国的。
    这与大都会亚洲部混进泰国造像是同一类越界，只是成因不同。
    """
    d = _get(f"{ARTIC_API}/{oid}")["data"]
    if not d.get("is_public_domain"):
        sys.exit(f"芝加哥 {oid} 非公版（is_public_domain=false），本库不取")
    place = str(d.get("place_of_origin") or "")
    if not CN_CULTURE.search(place):
        sys.exit(f"芝加哥 {oid} 产地 {place!r} 非中国——"
                 f"该馆检索 China 会带回法/英/美的瓷器，本库范围是中国美术史，不取")
    iid = d.get("image_id")
    if not iid:
        sys.exit(f"芝加哥 {oid} 无 IIIF 图像")
    thumb, size = _save(f"{ARTIC_IIIF}/{iid}/full/843,/0/default.jpg", eid)
    w, h = _jpeg_size(IMGDIR / Path(thumb).name)
    img = {
        "source": "artic",
        "source_url": f"https://www.artic.edu/artworks/{oid}",
        "credit": (d.get("credit_line") or "The Art Institute of Chicago")[:110],
        # **写 PD 而不是 CC0，因为只核到了这一步。**
        # 该馆 API 的 license_text 实测写的是「description 字段 CC BY 4.0、
        # 其余数据 CC0」——那是对**元数据**的声明，没有直接说图像。
        # 而 `is_public_domain: True` 直接支持的判断是「这件作品已进入公有领域」，
        # 即 PD。图像本身是否另有 CC0 专项奉献，未经核实，故不写。
        # 核到哪一步写哪一步，这是本库全部认知等级的同一条规矩。
        "license": "PD",
        "thumb": thumb,
        # IIIF 全尺寸留给读者深看，本库不复制那份带宽
        "iiif": f"{ARTIC_IIIF}/{iid}/full/full/0/default.jpg",
        "full": f"{ARTIC_IIIF}/{iid}/full/1686,/0/default.jpg",
        "w": w, "h": h,
    }
    meta = {"title": d.get("title"), "date": d.get("date_display"),
            "culture": place, "technique": d.get("medium_display"),
            "measurements": d.get("dimensions"),
            "accession": d.get("main_reference_number")}
    return img, size, meta


def main():
    ap = argparse.ArgumentParser(description="构建期取图与判权")
    ap.add_argument("source", choices=["find", "cma", "met", "artic", "wikimedia",
                                       "auto", "auto-pending"])
    ap.add_argument("ref", nargs="?", default="", help="检索词 / 馆方 id / Commons 文件名 / 条目 id")
    ap.add_argument("--id", help="条目 id，用作缩略图文件名")
    ap.add_argument("--write", action="store_true", help="回写 image 块（默认只试运行）")
    a = ap.parse_args()

    if a.source == "find":
        return find(a.ref)
    if a.source == "auto-pending":
        return auto_pending(a.write)
    if a.source == "auto":
        return auto(a.ref, a.write)
    if not a.id:
        sys.exit("取图须给 --id")
    fn = {"cma": from_cma, "met": from_met, "artic": from_artic,
          "wikimedia": from_wikimedia}[a.source]
    img, size, meta = fn(a.ref, a.id)
    print(json.dumps(img, ensure_ascii=False, indent=2))
    print(f'\n落盘 static/{img["thumb"]} · {size // 1024} KB · 许可 {img["license"]}',
          file=sys.stderr)
    if meta:
        print("馆方元数据（用于填条目，勿凭记忆改写）：", file=sys.stderr)
        for k, v in meta.items():
            if v:
                print(f"  {k:14} {v}", file=sys.stderr)


if __name__ == "__main__":
    main()

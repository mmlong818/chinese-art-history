"""中国美术史 · 数据契约与校验器。

六类实体，data/ 下的 JSON 是唯一真相源：

    artist   人    画家书家篆刻家雕塑家
    work     物    一切单件——书画、青铜、陶瓷、玉器、石刻、壁画、塑像…
    site     地    遗址与窟龛，用 parent 自嵌套（莫高窟 → 第 45 窟）
    class    类    器类、窑口、书体、画科、流派、技法、纹样
    treatise 文    画论书论，既是信源也是研究对象
    event    事    事件

为什么只有六类而不是十几类：青铜鼎与宋人山水都是「一件物」，区别在
`work.kind` 决定必备哪套维度，不是造两种实体。石窟的三级层次靠
`site.parent` 表达，加大足、加克孜尔不必改契约。

—— 三条中国材料特有的硬约束 ——

一、**断代依据独立成块**（dating）。一件青铜器靠铭文断代、靠器形序列断代、
    还是靠共出器物断代，可靠性差三个等级；龙门有确切造像题记的窟龛只占少数。
    只填一个年份等于把三种证据抹平，所以年代必须连同依据与可靠度一起记。

二、**重修层必备**（relayer）。敦煌大量洞窟经西夏、元、清重绘，今天看到的
    画面是叠压层。不写清重修，就是把后代的画当原作讲。

三、**拓本谱系必备**（rubbing）。碑刻的研究对象常是拓本而非原石，
    宋拓、明拓、翻刻是不同证据等级。不注明用的哪种拓本，字口存损无从判断。

另有 **题跋与鉴藏结构化**（colophon）：一卷宋画叠着元明清题跋与鉴藏印，
递藏史刻在物上，是中国书画独有的可见证据链。写成散文就查不了、统计不了。

用法：
    python schema.py            # 校验 data/，ERROR>0 则退出码 1
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

DATA = Path(__file__).parent / "data"
STATIC_IMG = Path(__file__).parent / "static" / "img"

# ── 陈述四态 ──────────────────────────────────────────────────────────────
# 沿用中国库既有语义（它是对的），但从 HTML class 提为数据字段。

STMT_STATES = {
    "adopt": "本库采纳",
    "pend": "待校验 / 材料不足",
    "disp": "有争议 / 并存异说",
    "dep": "旧说，已被质疑或推翻",
}

BADGES = {"ok": "已核 / 有款可考", "neu": "待校 / 无资料",
          "warn": "有争议 / 需审慎", "dep": "已佚 / 旧说已废"}

# ── 断代依据：中国材料的核心机制 ───────────────────────────────────────────
# basis 说明「凭什么定这个年代」，conf 说明「这个依据有多硬」。
# 二者分开，因为同一种依据也可能不硬：有铭文但铭文本身是后刻的。

DATING_BASIS = {
    "铭文": "器物自带铸铭或刻铭",
    "碑记纪年": "碑刻本身有立石年月",
    "造像题记": "窟龛或造像有供养题记",
    "款识": "书画有作者款印",
    "文献著录": "见于同代或后世著录，物本身无纪年",
    "器形序列": "按已建立的形制演变序列比对",
    "共出器物": "由同墓同坑共出物推定",
    "地层": "考古地层学",
    "科技测年": "碳十四、热释光等",
    "风格比对": "与已断代作品的风格比较——本库视为最弱的一档",
}

DATING_CONF = {
    "确证": "依据直接且未见异议",
    "较可靠": "依据成立但存在可讨论处",
    "存疑": "依据薄弱或异说并存",
    "不可考": "无可用依据，年代仅为习称",
}

# 拓本等级。碑刻研究常以拓本为对象，等级差异直接决定证据力。
RUBBING_EDITIONS = {
    "宋拓": "字口最全，存世极罕",
    "明拓": "多数名碑的可用上限",
    "清拓": "常见，字口已有损",
    "近拓": "损字最多",
    "翻刻": "据拓本重刻，非原石——性质与摹本同",
    "原石现状": "非拓本，直接记录今日原石",
}

# ── 块词汇表 ──────────────────────────────────────────────────────────────

BLOCKS = {
    "p": {"text"},
    "ul": {"items"},
    "kv": {"rows"},
    "stmt": {"state", "text"},
    "gap": set(),
    "quote": {"text"},
    "fig": {"image"},
    "tl": {"rows"},
    # 以下四类为中国材料专设
    "dating": {"basis", "conf", "text"},      # 断代依据
    "relayer": {"when", "what"},              # 重修层
    "rubbing": {"edition", "text"},           # 拓本
    "colophon": {"by", "text"},               # 题跋与鉴藏印
}

# ── 各实体的必备维度 ──────────────────────────────────────────────────────

# 艺术家十二维：沿用既有单文件库的划分（已在 121 家上验证过），
# 其中「代表作索引」「代表作总表」由生成器推导，不手写。
ARTIST_SECTIONS = [
    ("overview", "概览"),
    ("dossier", "基础档案"),
    ("life", "人生轨迹"),
    ("network", "人物关系"),
    ("system", "创作体系"),
    ("coordinates", "历史坐标"),
    ("secular", "世俗生活"),
    ("legend", "八卦与传奇"),
    ("quotes", "语录库"),
]
ARTIST_GENERATED = [("works-index", "★ 代表作·独立条目"), ("works-table", "代表作总表")]

# 艺术家门类。与 work.kind 分属两个词表——「画作」是物的类别，「绘画」是人的门类，
# 二者不可混用。兼擅多门者以「、」并列，主业在前（米芾作「书法、绘画」）。
ARTIST_CAT = {"绘画", "书法", "篆刻", "雕塑"}

# work 的维度按 kind 分派。不给每种材质造一套，归成五组。
WORK_KIND_GROUP = {
    "画作": "书画", "书法": "书画", "篆刻": "书画",
    "青铜": "器物", "陶瓷": "器物", "玉器": "器物", "漆器": "器物",
    "织绣": "器物", "金银器": "器物", "家具": "器物", "竹木牙角": "器物",
    "碑刻": "石刻", "摩崖": "石刻", "画像石": "石刻", "经幢": "石刻",
    "壁画": "窟寺", "塑像": "窟寺",
    "石雕": "雕塑", "木雕": "雕塑", "铜佛": "雕塑",
}

WORK_SECTIONS = {
    "书画": [
        ("basics", "基础信息"), ("commission", "创作背景"), ("reading", "画面分析"),
        ("technique", "技法与材料"), ("colophons", "题跋与鉴藏"),
        ("provenance", "来源与流转"), ("reception", "评价与影响"), ("viewing", "观看指南"),
    ],
    "器物": [
        ("basics", "基础信息"), ("form", "器形与纹饰"), ("inscription", "铭文释读"),
        ("technique", "工艺"), ("excavation", "出土与著录"), ("dating", "断代依据"),
        ("provenance", "来源与流转"), ("viewing", "观看指南"),
    ],
    "石刻": [
        ("basics", "基础信息"), ("occasion", "碑主与立碑缘由"), ("script", "书体与刻工"),
        ("rubbings", "拓本谱系"), ("condition", "字口存损"),
        ("record", "著录与考订"), ("dating", "断代依据"), ("viewing", "观看指南"),
    ],
    "窟寺": [
        ("basics", "基础信息"), ("placement", "位置与配置"), ("iconography", "图像程式"),
        ("technique", "材料与技法"), ("relayers", "重修层"),
        ("condition", "保存状况"), ("dating", "断代依据"), ("viewing", "观看指南"),
    ],
    "雕塑": [
        ("basics", "基础信息"), ("form", "形制与题材"), ("technique", "材料与技法"),
        ("excavation", "出土与著录"), ("dating", "断代依据"), ("viewing", "观看指南"),
    ],
}

SITE_SECTIONS = [
    ("overview", "概览"), ("history", "沿革与营建"), ("layout", "形制与布局"),
    ("donors", "供养与出资"), ("iconography", "图像程式"),
    ("conservation", "保存与修复史"), ("archaeology", "考古与著录史"),
]

CLASS_SECTIONS = [
    ("overview", "概览"), ("definition", "定义与范围"), ("features", "形制与特征"),
    ("evolution", "分期演变"), ("examples", "代表实例"), ("scholarship", "研究史"),
]

TREATISE_SECTIONS = [
    ("overview", "概览"), ("authorship", "成书与作者"), ("editions", "版本谱系"),
    ("concepts", "核心概念"), ("influence", "引用与传播"), ("misreading", "后世误读"),
]

EVENT_SECTIONS = [("course", "事件经过"), ("fields", "字段")]

# 争议字段：六类实体一律必备。史料稀疏不是省略它的理由。
DISPUTES = ("disputes", "争议字段")

# 需要断代依据的 kind 组——这几组的 dating 一节里必须有 dating 块或 gap
DATING_REQUIRED = {"器物", "石刻", "窟寺", "雕塑"}

# ── 图像 ──────────────────────────────────────────────────────────────────

IMAGE_REQUIRED = {"source", "source_url", "credit", "license", "thumb", "w", "h"}
IMAGE_OPTIONAL = {
    "full", "iiif", "focus", "alt", "detail_of", "note",
    "attribution_required", "fetched", "thumb_w", "thumb_h",
}

FREE_LICENSE = re.compile(r"^(PD|PD-US|CC0|OASC|CC BY(-SA)? \d\.\d)$")
LICENSE_BLOCKED = {"rights-review", "unknown"}


def license_ok(s):
    return bool(FREE_LICENSE.match(str(s or ""))) or s in LICENSE_BLOCKED


IMAGE_SOURCES = {
    "wikimedia", "npm-taipei", "dpm", "shanghai", "nmc", "dunhuang",
    "smithsonian", "met", "cleveland", "harvard", "princeton", "other",
}

IMAGE_STATUS = {
    "copyright": "仍在版权期或馆方未开放授权",
    "no-free-image": "已属公版，但未找到可用的自由授权复制品",
    "lost": "原物已佚或已毁",
    "in-situ": "原址不可移动且馆方未开放影像（石窟、摩崖多属此类）",
    "pending": "尚未补图",
}

# ── 核查等级 ──────────────────────────────────────────────────────────────
# 与西洋库不同：这里**不设「未标」逃生口**，verification 为必填。
# 西洋库留下 230 条未标是历史遗留，中国库从第一条起就不允许。
VERIFICATION = {
    "museum": "馆藏官方页或考古报告逐条核实",
    "wikidata": "经 verify.py 对结构化数据核过关键字段，未及馆藏页",
    "memory": "未经外部核对——所有具体数字、年代、尺寸应视为待校验",
}

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(
    r"\[\[(artist|work|site|class|treatise|event|src):([^\]|]+)(?:\|([^\]]+))?\]\]")
ENTITY_DIRS = {"artist": "artists", "work": "works", "site": "sites",
               "class": "classes", "treatise": "treatises", "event": "events"}


class Problem:
    def __init__(self, level, where, msg):
        self.level, self.where, self.msg = level, where, msg

    def __str__(self):
        return f"{self.level:5} {self.where:40} {self.msg}"


# ── 载入 ──────────────────────────────────────────────────────────────────


class Corpus:
    def __init__(self, data=DATA):
        self.root = Path(data)
        self.broken = []
        self.canon = self._json("canon.json")
        self.sources = self._json("sources.json", default={"schema": "sources/1", "items": []})
        self.periods = {p["id"]: p for p in self.canon.get("periods", [])}
        self.eras = {e["id"]: e for e in self.canon.get("eras", [])}
        self.artists = self._dir("artists")
        self.works = self._dir("works")
        self.sites = self._dir("sites")
        self.classes = self._dir("classes")
        self.treatises = self._dir("treatises")
        self.events = self._dir("events")
        self.src_ids = {s["id"] for s in self.sources.get("items", [])}

    def _json(self, name, default=None):
        p = self.root / name
        if not p.exists():
            if default is not None:
                return default
            raise FileNotFoundError(p)
        return json.loads(p.read_text(encoding="utf-8"))

    def _dir(self, name):
        d = self.root / name
        if not d.exists():
            return {}
        out = {}
        for f in sorted(d.glob("*.json")):
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as ex:
                self.broken.append((f.relative_to(self.root),
                                    f"第 {ex.lineno} 行第 {ex.colno} 列：{ex.msg}"))
                continue
            obj["_file"] = str(f.relative_to(self.root))
            out[obj.get("id", f.stem)] = obj
        return out

    def table(self, kind):
        return {"artist": self.artists, "work": self.works, "site": self.sites,
                "class": self.classes, "treatise": self.treatises,
                "event": self.events}[kind]

    def entity(self, kind, eid):
        return self.table(kind).get(eid)

    def all_entities(self):
        for kind in ENTITY_DIRS:
            for eid, obj in self.table(kind).items():
                yield kind, eid, obj

    def works_of(self, aid):
        ws = [w for w in self.works.values()
              if w.get("artist") == aid or aid in (w.get("artists") or [])]
        return sorted(ws, key=lambda w: (w.get("year_sort") or 0, w["id"]))

    def children_of(self, sid):
        return sorted((s for s in self.sites.values() if s.get("parent") == sid),
                      key=lambda s: s.get("sort", 0))

    def works_at(self, sid):
        return sorted((w for w in self.works.values() if w.get("site") == sid),
                      key=lambda w: (w.get("year_sort") or 0, w["id"]))

    def periods_sorted(self):
        return sorted(self.periods.values(), key=lambda p: p.get("sort", 0))

    def stats(self):
        return {"分期": len(self.periods), "艺术家": len(self.artists),
                "作品": len(self.works), "遗址": len(self.sites),
                "类目": len(self.classes), "画论": len(self.treatises),
                "事件": len(self.events),
                "图版": sum(1 for _ in self.all_images()), "信源": len(self.src_ids)}

    def all_images(self):
        for kind, eid, obj in self.all_entities():
            for img in _images_in(obj):
                yield kind, eid, img


def _images_in(obj):
    if isinstance(obj, dict):
        if "thumb" in obj and "source" in obj:
            yield obj
        for v in obj.values():
            yield from _images_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _images_in(v)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


# ── 校验 ──────────────────────────────────────────────────────────────────


def _check_id(eid, where, out):
    if not ID_RE.match(eid or ""):
        out.append(Problem("ERROR", where, f"id 不合法（须 ^[a-z0-9-]+$）：{eid!r}"))


CYR_GREEK = re.compile(r"[Ͱ-ϿЀ-ӿ]+")


def _check_homoglyph(obj, where, out):
    """抓西里尔与希腊字母。

    本库正文只该出现汉字、拉丁专名（馆名、藏品号、外国学者名）与数字，
    西里尔和希腊字母没有正当用途，故一律报错——**不论是否与拉丁字母混用**。

    两种都真的发生过：
      · 旧单文件库曾在实体 ID 里查出西里尔 а/н/о 冒充拉丁 a/n/o，
        肉眼不可辨却使该 ID 无法检索（混用型，ID 正则已从源头排除）；
      · 本库 canon.json 初稿在魏晋一期的 one_line 里混进了整个西里尔词
        「начало」（纯西里尔型）。原先只查「拉丁词中混入」的写法漏掉了后者——
        它不与拉丁字母相邻，检查便视而不见。
    """
    for text in _strings(obj):
        for run in CYR_GREEK.findall(text):
            out.append(Problem("ERROR", where,
                               f"混入西里尔/希腊字母：{run!r}——本库正文只应有汉字、"
                               f"拉丁专名与数字"))


def _check_blocks(blocks, where, c, out):
    if not isinstance(blocks, list) or not blocks:
        out.append(Problem("ERROR", where, "区块为空——史料不足请显式写 gap 块，不要留空"))
        return
    for i, b in enumerate(blocks):
        at = f"{where}[{i}]"
        t = b.get("t")
        if t not in BLOCKS:
            out.append(Problem("ERROR", at, f"未知块类型 {t!r}，可用：{sorted(BLOCKS)}"))
            continue
        missing = BLOCKS[t] - set(b)
        if missing:
            out.append(Problem("ERROR", at, f"{t} 块缺键 {sorted(missing)}"))
        if t == "stmt":
            if b.get("state") not in STMT_STATES:
                hint = ("；若意在标注空缺，用 {\"t\":\"gap\"} 块，gap 不是 stmt 的状态"
                        if b.get("state") == "gap" else "")
                out.append(Problem("ERROR", at,
                                   f"stmt.state 须为 {sorted(STMT_STATES)}{hint}"))
            elif b["state"] == "dep" and not b.get("src"):
                out.append(Problem("WARN", at, "判定旧说已废须标 src——推翻前说是强主张"))
        if t == "dating":
            if b.get("basis") not in DATING_BASIS:
                out.append(Problem("ERROR", at, f"dating.basis 须为 {sorted(DATING_BASIS)}"))
            if b.get("conf") not in DATING_CONF:
                out.append(Problem("ERROR", at, f"dating.conf 须为 {sorted(DATING_CONF)}"))
            if b.get("basis") == "风格比对" and b.get("conf") == "确证":
                out.append(Problem("ERROR", at,
                                   "风格比对不能判为『确证』——它是本库承认的最弱一档依据"))
        if t == "rubbing" and b.get("edition") not in RUBBING_EDITIONS:
            out.append(Problem("ERROR", at, f"rubbing.edition 须为 {sorted(RUBBING_EDITIONS)}"))
        if t == "fig":
            _check_image(b.get("image"), at, out)


def _check_image(img, where, out):
    if not isinstance(img, dict):
        out.append(Problem("ERROR", where, "fig.image 不是对象"))
        return
    missing = IMAGE_REQUIRED - set(img)
    if missing:
        out.append(Problem("ERROR", where, f"图像缺必填字段 {sorted(missing)}"))
    unknown = set(img) - IMAGE_REQUIRED - IMAGE_OPTIONAL
    if unknown:
        out.append(Problem("WARN", where, f"图像有未定义字段 {sorted(unknown)}"))
    if not license_ok(img.get("license")):
        out.append(Problem("ERROR", where, f"许可 {img.get('license')!r} 不是可识别的自由许可；"
                                          f"无法判定请写 rights-review"))
    if img.get("source") not in IMAGE_SOURCES:
        out.append(Problem("ERROR", where, f"source {img.get('source')!r} 不在白名单"))
    for k in ("w", "h"):
        if k in img and not (isinstance(img[k], int) and img[k] > 0):
            out.append(Problem("ERROR", where, f"图像 {k} 须为正整数"))
    if "thumb" in img:
        t = str(img["thumb"])
        if not (t.startswith("img/") or t.startswith("https://")):
            out.append(Problem("ERROR", where, f"thumb 须为本地 img/… 或 https URL"))
        # 只有真取过图，盘上才有文件。这条防的是「编出来的出处」——
        # 西洋库出过一次：字段齐全、格式全对、许可写 PD，但那个文件根本不存在。
        elif t.startswith("img/") and not (STATIC_IMG / t[4:]).exists():
            out.append(Problem("ERROR", where, f"缩略图文件不存在：static/{t}"
                                              f"——若 image 块系凭记忆填写，请整块删除"))
    for k in ("full", "iiif", "source_url"):
        if k in img and not str(img[k]).startswith("https://"):
            out.append(Problem("ERROR", where, f"图像 {k} 须为 https URL"))


def _check_links(obj, where, c, out):
    for text in _strings(obj):
        for kind, eid, _label in LINK_RE.findall(text):
            if kind == "src":
                if eid not in c.src_ids:
                    out.append(Problem("ERROR", where, f"死链 src:{eid}（未在 sources.json 登记）"))
            elif not c.entity(kind, eid):
                out.append(Problem("ERROR", where, f"死链 {kind}:{eid}"))


def _check_sections(obj, spec, where, c, out):
    secs = obj.get("sections")
    if not isinstance(secs, dict):
        out.append(Problem("ERROR", where, "缺 sections 对象"))
        return
    known = {s for s, _ in spec} | {DISPUTES[0]}
    for sid, label in list(spec) + [DISPUTES]:
        if sid not in secs:
            out.append(Problem("ERROR", where, f"缺必备维度 {sid}（{label}）"))
        else:
            _check_blocks(secs[sid], f"{where}.{sid}", c, out)
    for sid in secs:
        if sid not in known:
            _check_blocks(secs[sid], f"{where}.{sid}", c, out)


def _check_common(obj, where, c, out, need):
    _check_id(obj.get("id"), where, out)
    _check_homoglyph(obj, where, out)
    _check_links(obj, where, c, out)
    for k in need:
        if not obj.get(k):
            out.append(Problem("ERROR", where, f"缺字段 {k}"))
    if not obj.get("sources"):
        out.append(Problem("ERROR", where, "缺 sources——每条必须能追到出处，这是本库的底线"))
    for s in obj.get("sources", []):
        if isinstance(s, dict) and s.get("ref") and s["ref"] not in c.src_ids:
            out.append(Problem("ERROR", where, f"sources 引用未登记的 src:{s['ref']}"))
    # 与西洋库不同：verification 为必填，不留「未标」逃生口。
    v = obj.get("verification")
    if not v:
        out.append(Problem("ERROR", where,
                           f"缺 verification——须为 {sorted(VERIFICATION)} 之一。"
                           f"本库不允许留空：凭记忆写的与核实过的在文件里长得一样，"
                           f"不标就分不出来"))
    elif v not in VERIFICATION:
        out.append(Problem("ERROR", where, f"verification 须为 {sorted(VERIFICATION)} 之一，得到 {v!r}"))
    if obj.get("portrait"):
        _check_image(obj["portrait"], f"{where}.portrait", out)
    if obj.get("image"):
        _check_image(obj["image"], f"{where}.image", out)


def _has(secs, sid, t):
    return any(b.get("t") == t for b in secs.get(sid, []))


def validate(c):
    out = [Problem("ERROR", str(f), f"JSON 语法错误——{msg}") for f, msg in c.broken]

    for pid, p in c.periods.items():
        _check_id(pid, f"period/{pid}", out)
        if p.get("era") and p["era"] not in c.eras:
            out.append(Problem("ERROR", f"period/{pid}", f"era {p['era']!r} 不存在"))

    for aid, a in c.artists.items():
        w = f"artist/{aid}"
        _check_common(a, w, c, out, ["name", "cat", "period", "one_line"])
        if a.get("period") and a["period"] not in c.periods:
            out.append(Problem("ERROR", w, f"period {a['period']!r} 不在 canon 中"))
        # cat 原先只查存在不查取值，于是「画作」（那是 work.kind 的词）能混进来。
        # 兼职多门者用「、」并列，顺序有意义（主业在前，米芾是书法在前），故只查成员不查序。
        for part in str(a.get("cat") or "").split("、"):
            if part and part not in ARTIST_CAT:
                out.append(Problem("ERROR", w, f"cat {part!r} 不在词表中，可用：{sorted(ARTIST_CAT)}"
                                               f"（多门以「、」并列，主业在前）"))
        _check_sections(a, ARTIST_SECTIONS, w, c, out)

    for wid, wk in c.works.items():
        w = f"work/{wid}"
        _check_common(wk, w, c, out, ["title", "kind", "period", "holder"])
        kind = wk.get("kind")
        group = WORK_KIND_GROUP.get(kind)
        if not group:
            out.append(Problem("ERROR", w, f"kind {kind!r} 未定义，可用：{sorted(WORK_KIND_GROUP)}"))
            continue
        _check_sections(wk, WORK_SECTIONS[group], w, c, out)
        secs = wk.get("sections", {})
        # 三条硬约束
        if group in DATING_REQUIRED and not (_has(secs, "dating", "dating")
                                            or _has(secs, "dating", "gap")):
            out.append(Problem("ERROR", w, f"{kind} 的『断代依据』须含 dating 块或 gap——"
                                          f"只写年份等于把不同等级的证据抹平"))
        if group == "窟寺" and not (_has(secs, "relayers", "relayer")
                                   or _has(secs, "relayers", "gap")):
            out.append(Problem("ERROR", w, "壁画／塑像的『重修层』须含 relayer 块或 gap——"
                                          "不写重修就是把后代补绘当原作讲"))
        if group == "石刻" and not (_has(secs, "rubbings", "rubbing")
                                    or _has(secs, "rubbings", "gap")):
            out.append(Problem("ERROR", w, "石刻的『拓本谱系』须含 rubbing 块或 gap——"
                                          "不注明所据拓本，字口存损无从判断"))
        if wk.get("artist") and wk["artist"] not in c.artists:
            out.append(Problem("ERROR", w, f"作者 {wk['artist']!r} 无对应艺术家条目"))
        if wk.get("site") and wk["site"] not in c.sites:
            out.append(Problem("ERROR", w, f"所属遗址 {wk['site']!r} 无对应条目"))
        if not wk.get("image") and wk.get("image_status") not in IMAGE_STATUS:
            out.append(Problem("WARN", w, f"无 image 且未说明原因——请标 image_status"))

    for sid, s in c.sites.items():
        w = f"site/{sid}"
        _check_common(s, w, c, out, ["name", "one_line"])
        _check_sections(s, SITE_SECTIONS, w, c, out)
        if s.get("parent"):
            if s["parent"] not in c.sites:
                out.append(Problem("ERROR", w, f"parent {s['parent']!r} 无对应条目"))
            elif s["parent"] == sid:
                out.append(Problem("ERROR", w, "parent 指向自身"))

    for cid, cl in c.classes.items():
        w = f"class/{cid}"
        _check_common(cl, w, c, out, ["name", "facet", "one_line"])
        _check_sections(cl, CLASS_SECTIONS, w, c, out)

    for tid, t in c.treatises.items():
        w = f"treatise/{tid}"
        _check_common(t, w, c, out, ["title", "author", "one_line"])
        _check_sections(t, TREATISE_SECTIONS, w, c, out)

    for eid, e in c.events.items():
        w = f"event/{eid}"
        _check_common(e, w, c, out, ["title", "when"])
        _check_sections(e, EVENT_SECTIONS, w, c, out)

    seen = set()
    for s in c.sources.get("items", []):
        sid = s.get("id")
        _check_id(sid, f"src/{sid}", out)
        if sid in seen:
            out.append(Problem("ERROR", f"src/{sid}", "信源 id 重复登记——请合并为一条"))
        seen.add(sid)
        for k in ("title", "kind", "tier"):
            if not s.get(k):
                out.append(Problem("ERROR", f"src/{sid}", f"缺字段 {k}"))
    return out


def main():
    try:
        c = Corpus()
    except FileNotFoundError as e:
        print(f"ERROR 找不到 {e}")
        return 1
    problems = validate(c)
    for p in problems:
        print(p)
    errors = sum(1 for p in problems if p.level == "ERROR")
    print("\n" + " · ".join(f"{k} {v}" for k, v in c.stats().items()))
    print(f"ERROR {errors} · WARN {len(problems) - errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

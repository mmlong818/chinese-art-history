"""台北故宫开放资料专路。

    python ingest_npm.py probe                  # 校准：只读不写，看字段与对照
    python ingest_npm.py survey                 # 各分類的件数，与馆方自述对账
    python ingest_npm.py run --type 拓片 --n 40 [--apply]

—— 为什么单开这一路 ——

本库最大的结构问题是来源偏斜：2,482 件作品里 83.4% 出自克利夫兰、芝加哥、大都会。
而 **`npm-taipei` 早已是本库 tier 1 信源，此前却是经 Wikidata 摄入的**
——馆方自己的开放资料一直没走过。这不是新增渠道，是改一个现成的错。

`digitalarchive.npm.gov.tw/opendata`：**108,653 件**（18 个分類的件数加总正好吻合），
41 万张 100 萬像素 CC0 + 41 万张 600 萬像素 CC BY 4.0，取图不需注册。

—— 与北京故宫的分别，必须写在这里 ——

北京故宫数字文物库无 JSON 接口，且查询逻辑用 jsjiami.com.v7 **商业混淆器**加密。
**混淆不是技术难度，是运营方花钱装上的访问控制**——本库不逆向它。
台北故宫恰相反：它自己声明 CC0／CC BY 4.0、不需注册、把资料页公开发布，
读它自己的列表接口是它邀请的用法。**分别在于来源方的意思表示，不在于难度。**

—— 校准阶段撞到的四个坑，写在这里省下一个人的时间 ——

一、**`dep` 与 `id` 合起来才是键，而我先后判错两次。**
   第一次：看见落地页有 `#B` `#C` `#U` 三个锚点，又看见 `dep=U`，
   便以为 B／C／U 是书画／图书文献／器物三处。
   **那三个锚点是台湾政府网站的无障碍 accesskey**（B=頁尾、C=中央內容、U=上方選單），
   与部门毫无关系——**宽模式认结构，见 tasks/lessons.md L11。**
   第二次（更险）：改判为「dep 恒为 U」并写死进常量。
   实测未筛选时确实页页都是 U，但**筛 `RegisterType=拓片` 后返回的是 `dep=P`**。
   而 `id` 只在同一个 dep 内唯一，于是拿 `dep=U` 去请求拓片的 id 25139，
   **馆方返回了另一件真实存在的物**——一枚玉印，分類「玉器」。
   **它不报错，它安静地返回看起来完全合理的错数据。**
   若当时直接跑摄入，会写出一批标着「拓片」而内容是玉印与瓷盘的条目。

   **这一处还留下一条更一般的教训：对 A 步的对照，不构成对 B 步的验证。**
   取列表那一步我做足了对照（納尼亞 返回 0、页数与馆方自述精确吻合），
   两道检查都通过了——**它们验的是筛选，验不到我随后用错键去取详情。**

二、**详情页有四节，形状各不相同**，不能用一套「首列=字段名」的两列抽法通吃：
   基本資料（2 列键值）· 參考資料（書名／作者編者／出版者／出版日期）·
   款識（識文位置／識文種類／全文）· 保存維護。
   第一版把三张表压成一个 dict，于是《國立故宮博物院－文房聚英》被当成字段名。

三、**分類筛选用 shell 直接传中文会被编码弄坏**，而坏掉的样子是「返回 0 条」
   ——与「这一类真的没有」无从分辨。**靠对照查验才认出来**：
   `拓片` 与不存在的 `納尼亞` 都返回 0，说明不是筛选生效而是请求坏了。
   改用 UTF-8 文件 body（`--data-binary`）后：拓片 81 页、繪畫 1,113 页、納尼亞 0 条。

四、**证书缺 Subject Key Identifier**，Python 的 OpenSSL 拒绝握手而 curl 走 schannel 可以。
   **没有关掉证书校验**——那才是真的削弱；换一个接受该证书的 TLS 栈是另一件事。

—— 三条本路特有的处理 ——

一、**verification 标 museum。**台北故宫是藏家本身。这与 CAOD（聚合方，
   一律不得标 museum）的区别必须守住——读了聚合库就标 museum，等于把二手冒充一手。

二、**款識 四种（題銘／人名款／年號款／收藏款）落成 colophon 块，不许摊平进散文。**
   这是要这个渠道的主要理由：克利夫兰 61 个字段里只有一个笼统的 inscription。
   **人名款与年號款另落一条 dating 块，basis 记「款识」**——它们正是本库那一档依据。

三、**時代字段带年號**（「清 康熙五十五年」），馆方的朝代归属是它自己的断代声明，
   分期一律以此为主来源。**复合朝代（「明至清」）判不出就不摄入。**
"""

import argparse
import collections
import html
import json
import pathlib
import re
import subprocess
import sys
import time
from html.parser import HTMLParser

WIKI = pathlib.Path(__file__).parent
sys.path.insert(0, str(WIKI))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = "https://digitalarchive.npm.gov.tw"
SEARCH = BASE + "/opendata/Pub/Search"
# **(dep, id) 才是键，id 单独不唯一。**dep 必须取自列表结果里的链接，不许写死：
# 实测拓片类用 dep=P、器物类用 dep=U，而拿 dep=U 去请求一个 P 类的 id，
# 馆方会返回**另一件真实存在的物**（拓片 id 25139 在 dep=U 下是一枚玉印）。
# **它不报错，它安静地返回看起来完全合理的错数据**——最坏的一类错。
DETAIL = BASE + "/opendata/Pub/Detail/{id}?dep={dep}&mode=full"
IMG = BASE + "/opendata/Image/GetImage?imageId={i}&randomCode={r}"
UA = ("china-art-history-archive/0.1 (non-profit Chinese art history wiki; "
      "reads NPM open data under its declared CC0/CC BY 4.0; contact via repo)")
GAP = 1.6          # 礼貌限速。馆方未声明限流，宁可慢。
HOLDER = "国立故宫博物院（台北）"

# 18 个分類与馆方自述件数。**留着件数是为了对账**——
# survey 若跑出来与这些数字差得远，说明请求或解析出了问题，而不是馆藏变了。
TYPES = {
    "陶瓷器": 24201, "繪畫": 16690, "玉器": 12327, "雜項": 11974, "法帖": 7147,
    "錢幣": 6953, "銅器": 6236, "法書": 5754, "其他": 5227, "成扇": 2545,
    "琺瑯器": 2521, "文具": 2385, "織品": 1612, "拓片": 1214, "漆器": 762,
    "雕刻": 667, "絲繡": 438,
}

# 分類 → 本库 work.kind。**判不出的一律不摄入，不拿最近的凑。**
KIND = {
    "繪畫": "画作", "法書": "书法",
    # **法帖与拓片都归「碑刻」。**拓本并入碑，作为碑的补充——
    # 年代归碑（题名里记着「漢武都太守李翕碑」的漢），拓本落成 `rubbing` 块，
    # 「早拓与近拓字口不同」这一层由那个块的六档（宋拓／明拓／清拓／近拓／翻刻／
    # 原石现状）承担。此前把拓片立为独立 kind，反而制造了「拓本自身无年代」的死结：
    # 实测 20 件试摄有 19 件因「無時代」落不了地。
    "法帖": "碑刻", "拓片": "碑刻",
    "陶瓷器": "陶瓷", "玉器": "玉器", "銅器": "青铜", "漆器": "漆器",
    "琺瑯器": "珐琅", "織品": "织绣", "絲繡": "织绣",
}
SKIP_TYPE = {
    "錢幣": "本库无「钱币」kind，加一个是范围决定，不由摄入脚本顺手定",
    "雕刻": "雕塑组分石雕／木雕／铜造像三档，馆方分類不给材质，判不出",
    "文具": "砚、墨、笔、纸分属不同材质组，一律归竹木牙角就是抹平",
    "成扇": "扇骨与扇面是两种物，馆方一条记录含两者，拆不开",
    "雜項": "分類本身即「判不出」",
    "其他": "同上",
}

# 時代前缀 → canon 分期。繁体优先，长的在前。
DYN = [
    ("新石器", "neolithic"), ("商", "erlitou-shang"), ("西周", "western-zhou"),
    ("春秋", "spring-autumn-warring"), ("戰國", "spring-autumn-warring"),
    ("戰国", "spring-autumn-warring"),
    ("秦", "qin-han"), ("漢", "qin-han"), ("汉", "qin-han"),
    ("三國", "wei-jin-nanbei"), ("晉", "wei-jin-nanbei"), ("晋", "wei-jin-nanbei"),
    ("南北朝", "wei-jin-nanbei"), ("北魏", "wei-jin-nanbei"),
    ("隋", "sui-tang"), ("唐", "sui-tang"),
    ("五代", "five-dynasties"),
    ("北宋", "northern-song"), ("南宋", "southern-song"),
    ("遼", "liao-jin"), ("辽", "liao-jin"), ("金", "liao-jin"),
    ("元", "yuan"), ("明", "ming"), ("清", "qing"),
    ("民國", "late-qing-republic"),
]
# 判不出的时代写法。**这里的每一条都必须精确，第一版就栽了。**
#
# 第一版把「跨代」写成裸的 `至`，于是「元惠宗**至正**八年（1348）」被判成跨代而跳过
# ——`至` 既是范围符（「明至清」），也是至正、至元、至順、至大、至治、至德的年号字。
# **又一次宽模式认结构，而且是在我自己写着这条警告的文件里犯的。**
# 改为只在「朝代 + 至 + 朝代」这个形状上认跨代。
_DYNCH = "新石器商周春秋戰戰國秦漢汉三國晉晋南北朝魏隋唐五代宋遼辽金元明清民國"
AMBIG = [
    # 跨代：两个朝代名之间夹范围符，**且第二个朝代名之后就结束**。
    # 末尾的 `$` 是必需的：没有它，「元 至元三年」也会被判成跨代
    # ——「元」+「至」+「元」正好长得像朝代-至-朝代，而至元是元代年号
    # （至元、至正、至順、至大、至治、至德 都是）。
    # 分辨点在于**年号后面跟着「三年」，而跨代表述到第二个朝代就完了**。
    re.compile(rf"^[{_DYNCH}]{{1,3}}(代)?\s*[至～~－—-]\s*"
               rf"[{_DYNCH}]{{1,3}}(代|初|中|末|期)?$"),
    re.compile(r"^(宋|周)(代)?$"),        # 单「宋」不分南北，单「周」不分西东
    re.compile(r"不詳|未詳|不明"),          # 馆方自己说不知道
    re.compile(r"^(傳|传|仿|舊傳)"),        # 传为、仿作——归属本身待考
]


def fetch(url, body=None, tries=5):
    """经 curl，不用 urllib。原因见文件头第四条（证书缺 SKI）。

    body 走临时文件 + --data-binary，**不走命令行参数**：
    中文经 shell 传递会被编码弄坏，而坏掉的样子是「返回 0 条」，
    与「这一类真的没有」无从分辨。
    """
    args = ["curl", "-sS", "-m", "90", "-w", "\n%{http_code}", "-A", UA,
            "-H", "Referer: " + BASE + "/opendata"]
    tmp = None
    if body is not None:
        tmp = WIKI / ".npm_body.json"
        tmp.write_bytes(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        args += ["-X", "POST", "-H", "Content-Type: application/json; charset=utf-8",
                 "--data-binary", "@" + str(tmp)]
    args.append(url)
    delay = 2.0
    for i in range(tries):
        p = subprocess.run(args, capture_output=True)
        if p.returncode == 0:
            s = p.stdout.decode("utf-8", "replace")
            nl = s.rfind("\n")
            code = s[nl + 1:].strip()
            return (int(code) if code.isdigit() else 0), s[:nl]
        if i == tries - 1:
            raise RuntimeError(f"curl rc={p.returncode}: "
                               f"{p.stderr.decode('utf-8', 'replace')[:200]}")
        time.sleep(delay); delay *= 2


def search(rtype=None, page=1, size=15, content=None):
    """→ ([(id, dep)], page_count)。

    **0 条不等于「没有」**：急促请求会被限流，而限流的样子就是返回 0 条。
    实测同一个「谿山行旅圖」在密集请求时返回 0、隔 6 秒后三次都稳定返回 2 条。
    调用方须自行重试——见 pick() 的第二道防线。
    """
    st, h = fetch(SEARCH, {
        "RegisterType": rtype, "IndexYear": None, "WestBeginYear": 0,
        "WestEndYear": 0, "YearDisplay": None, "SearchContent": content,
        "RegisterTypeEng": None,
        "PageInfo": {"PageIndex": page, "PageSize": size, "PageCount": 7244}})
    if st != 200:
        return [], 0
    # 连 dep 一起取。**只认带 mode=full 的完整链接形态**——
    # 上一版用松正则 `\?dep=` 匹配，把 dep 丢了，于是全部按 U 请求。
    items = re.findall(
        r"/opendata/Pub/Detail/(\d+)\?dep=([A-Za-z]+)&(?:amp;)?mode=full", h)
    pc = re.search(r'"PageCount":(\d+)', h)
    return list(dict.fromkeys(items)), int(pc.group(1)) if pc else 0


NAV = re.compile(r'<!--\s*<div class="nav-title">(.*?)</div>\s*-->')


class _Rows(HTMLParser):
    """抽一个区段里的表格行，**保留原始列数，不压成两列**。

    第一版把所有 `<tr>` 当成「字段名 + 值」，于是《國立故宮博物院－文房聚英》
    被当成字段名（它是參考資料表的書名列），「背」「器身」「面」也被当成字段名
    （它们是款識表的識文位置列）。**三张不同形状的表被压成了一个 dict。**

    这个错值得记下来：我在上一版注释里写「用 HTML 解析器而不是正则，
    免得安静地少一个字段」，而我的 HTML 解析器安静地把三张表并成了一张。
    **结构性错误不是正则独有的，根源是「假定单一结构」。**
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.head = [], []
        self._cells, self._buf, self._in, self._th = [], [], False, False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in, self._th, self._buf = True, tag == "th", []
        elif tag == "br" and self._in:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in:
            self._cells.append("".join(self._buf).strip())
            self._in = False
        elif tag == "tr":
            if self._cells:
                (self.head if self._th else self.rows).append(self._cells)
            self._cells, self._th = [], False

    def handle_data(self, d):
        if self._in:
            self._buf.append(d)


def _clean(s):
    return re.sub(r"[ \t]*\n[ \t]*", "\n", html.unescape(s or "")).strip()


def parse(h):
    marks = [(m.start(), _clean(m.group(1))) for m in NAV.finditer(h)]
    out = {"_img": re.findall(
        r"/opendata/Image/GetImage\?imageId=(\d+)&(?:amp;)?randomCode=(\d+)", h)}
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(h)
        p = _Rows(); p.feed(h[pos:end])
        rows = [[_clean(c) for c in r] for r in p.rows]
        if name == "基本資料":
            kv = {}
            for r in rows:
                if len(r) >= 2 and r[0] and r[1]:
                    kv.setdefault(re.sub(r"\s+", "", r[0]), r[1])
            out[name] = kv
        else:
            out[name] = [r for r in rows if any(r)]
    return out


def _split(v):
    """基本資料 的值里用多个 <br/> 分隔中／英名、朝代／公元年。→ 非空段列表。"""
    return [x for x in (p.strip() for p in (v or "").split("\n")) if x]


def _artists():
    idx = {}
    for f in (WIKI / "data" / "artists").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        idx[d["id"]] = d.get("period")
    return idx


ARTISTS = None


def artist_period(author):
    """由馆方 `作者` 字段的**罗马化**反查本库艺术家条目的分期。

    馆方作者写作「范寬,Fan Kuan」——中文是繁体，而本库艺术家名是简体，
    **按字形比对必在繁简上失效**（范寬／范宽、趙孟頫／赵孟頫、黃居寀／黄居寀
    实测全部对不上）。而罗马化转成连字符正是本库的 artist id 写法（`fan-kuan`），
    **拿它作键就绕开了繁简整件事**——L11 的补充规则说可靠证据是稳定标识符
    而不是字形，这里的罗马名就是那个稍稳定些的东西。

    仍会失效（馆方偶用威妥玛拼法，本库也未必有该艺术家），
    **失效时返回 None 让调用方跳过并记原因，不猜。**
    """
    global ARTISTS
    if ARTISTS is None:
        ARTISTS = _artists()
    for part in re.split(r"[,，;；/]", author or ""):
        part = part.strip()
        if not part or not re.fullmatch(r"[A-Za-z' \-.]+", part):
            continue
        aid = re.sub(r"[^a-z]+", "-", part.lower()).strip("-")
        if aid in ARTISTS and ARTISTS[aid]:
            return aid, ARTISTS[aid]
    return None, None


# 题名前缀的朝代。書畫 类**没有任何年代字段**，朝代只写在品名开头
# （「宋范寬谿山行旅圖　軸」「五代南唐董源龍宿郊民圖　軸」）。
# 「五代南唐」必须整体在前，否则会被当成「五代」+「南」的复合前缀而判不出。
TITLE_DYN = [("五代南唐", "five-dynasties"), ("五代十國", "five-dynasties")] + [
    (a, b) for a, b in DYN]
# 复合前缀防护：「元明人畫山水集景」的前缀是两朝连写，按「元」判就错（作者实为明文嘉）。
NEXT_DYN = set("新石商周春秋戰戰秦漢汉三晉晋南北魏隋唐五宋遼辽金元明清民代國")


def title_dyn(title):
    t = re.sub(r"^(傳|传|舊傳)", "", re.sub(r"[\s　]+", "", title or ""))
    for zh, pid in TITLE_DYN:
        if t.startswith(zh):
            rest = t[len(zh):]
            if rest and rest[0] in NEXT_DYN and zh not in ("五代南唐", "五代十國"):
                return None, f"复合前缀「{zh}{rest[0]}」，不据题名判"
            return pid, f"馆方题名以「{zh}」起"
    return None, "题名无朝代前缀"


def period_of(b):
    """→ (period, why)。**判不出返回 None，不猜。**

    **年代字段名随分類变**：器物类作 `時代`（「清 康熙五十五年」），
    拓片类无 `時代` 而作 `創作時間`（「元惠宗至正八年（1348）」）。
    校准时只读 `時代`，于是拓片全部判成「無時代」——**字段词表按类而异，
    写死一个名字就会安静地全数漏掉。**
    """
    era = b.get("時代") or b.get("創作時間") or b.get("年代") or ""
    head = _split(era)[0] if era else ""
    if not head:
        return None, "無時代"
    for rx in AMBIG:
        if rx.search(head):
            return None, f"「{head}」判不出（跨代／不分南北／馆方自称不詳／传仿）"
    for zh, pid in DYN:
        if head.startswith(zh):
            return pid, f"馆方時代以「{zh}」起"
    return None, f"「{head}」无对应分期"


def resolve_period(b, title):
    """分期三路，依次尝试。**每一路都记下凭什么，写进条目。**

    本库对断代要求「凭什么定这个年代」独立成块，**分期本身同样该说清来源**——
    馆方明写的朝代、题名前缀、和据本库艺术家条目反推，可靠度依次递减。

    一、`時代`／`創作時間` 字段——馆方明写的朝代归属，最硬。器物类有，書畫类没有。
    二、**题名前缀**——書畫 类唯一的线索（「五代南唐董源龍宿郊民圖」）。
    三、**据作者反查本库艺术家条目**——只在题名前缀是「宋」「周」这类
       本库明定判不出的朝代时才用。馆方把宋画一律题作「宋」，而本库的规则是
       单「宋」不分南北；此时范寬是北宋、李唐是南宋这件事，**本库的艺术家条目
       已经记着**，用它比留空好，但必须注明分期不是馆方给的。
    """
    pid, why = period_of(b)
    if pid:
        return pid, why
    pid, why2 = title_dyn(title)
    if pid:
        return pid, why2
    t = re.sub(r"[\s　]+", "", title or "")
    if t[:1] in ("宋", "周"):
        aid, ap = artist_period(b.get("作者", ""))
        # 只接受与该朝相容的分期，防止把后代摹本按原作者朝代归位。
        ok = {"宋": {"northern-song", "southern-song"},
              "周": {"western-zhou", "spring-autumn-warring"}}[t[:1]]
        if ap in ok:
            return ap, (f"馆方题名仅作「{t[:1]}」而本库明定其不分南北；"
                        f"据本库艺术家条目 {aid} 定为 {ap}——**此分期非馆方所给**")
        return None, f"题名仅作「{t[:1]}」，而作者 {b.get('作者','')!r} 在本库无可用分期"
    return None, why2


def survey():
    """各分類的件数，与馆方自述对账。**并做对照查验。**"""
    print(f"{'分類':<10}{'实测页数':>8}{'×15 ≈':>9}{'馆方自述':>9}{'差':>7}  dep")
    tot = 0
    for t, stated in TYPES.items():
        items, pc = search(t, 1); time.sleep(GAP)
        est = pc * 15
        tot += stated
        flag = "" if abs(est - stated) <= 15 else "　← 对不上！"
        print(f"{t:<10}{pc:>8}{est:>9}{stated:>9}{est-stated:>7}{flag}")
    _, pc_all = search(None, 1); time.sleep(GAP)
    print(f"\n{'全量':<10}{pc_all:>8}{pc_all*15:>9}{tot:>9}{pc_all*15-tot:>7}")
    print("\n—— 对照查验 ——")
    for bad in ("納尼亞", "ZZZ"):
        items, pc = search(bad, 1); time.sleep(GAP)
        print(f"   RegisterType={bad!r:<10} 条目 {len(items)} · 页数 {pc}"
              f"{'　← 与有效值无异，筛选无效！' if len(items) else '　← 合格'}")


def probe():
    items, pc = search("拓片", 1)
    print(f"拓片：{pc} 页；第 1 页 (id, dep) = {items[:5]}\n")
    for i, dep in items[:2]:
        st, h = fetch(DETAIL.format(id=i, dep=dep)); time.sleep(GAP)
        d = parse(h)
        print(f"—— id={i} dep={dep} HTTP {st} · 图 {len(d['_img'])} 张 ——")
        for k, v in (d.get("基本資料") or {}).items():
            print(f"   基本 {k:<10} {v[:72].replace(chr(10), ' ┃ ')}")
        for r in (d.get("款識") or [])[:6]:
            print(f"   款識 {' ┃ '.join(x[:36] for x in r)}")
        for r in (d.get("參考資料") or [])[:3]:
            print(f"   參考 {' ┃ '.join(x[:26] for x in r)}")
        for r in (d.get("保存維護") or [])[:3]:
            print(f"   保存 {' ┃ '.join(x[:36] for x in r)}")
        pid, why = period_of(d.get("基本資料") or {})
        print(f"   → 分期 {pid}（{why}）\n")


# 款識 種類 → 是否可作断代依据。**人名款与年號款是款识，題銘与收藏款不是。**
DATING_INS = {"年號款", "人名款"}


def build(i, dep, d, rtype):
    b = d.get("基本資料") or {}
    sn = b.get("文物統一編號") or ""
    names = _split(b.get("品名"))
    title = names[0] if names else ""
    if not title or not sn:
        return None, "無品名或編號"
    kind = KIND.get(rtype)
    if not kind:
        return None, SKIP_TYPE.get(rtype, "分類無對應 kind")
    pid, why = resolve_period(b, title)
    if not pid:
        return None, why

    era = _split(b.get("時代") or b.get("創作時間") or "")
    # **基本資料 全量透传。**字段集随分類而异（器物有時代／尺寸，
    # 拓片有創作時間／書體／釋文／作品語文），挑几个写死就会安静地丢字段。
    rows = [["馆方分類", rtype]]
    for k, v in b.items():
        if k == "品名":
            continue          # 已作 title
        rows.append([k, v.replace(chr(10), "　")[:300]])
    if len(names) > 1 and names[-1] != names[0]:
        rows.append(["馆方原题（英）", names[-1]])

    basics = [{"t": "kv", "rows": rows},
              {"t": "p", "text": "馆方资料页："
                                 + DETAIL.format(id=i, dep=dep)}]
    if b.get("說明"):
        basics.append({"t": "p", "text": b["說明"]})
    basics.append({"t": "kv", "rows": [["本库分期依据", why]]})
    basics.append({"t": "stmt", "state": "pend",
                   "text": "**本条为著录级**：照录馆方开放资料，未作阐释。"
                           "具体年代、尺寸与款识释文均以馆方记录为准，本库未另行考订。"})

    secs = {"basics": basics}

    # **摹本／仿作要当场标出来。**馆方题名里的「倣」「仿」「摹」「臨」是它自己的声明，
    # 而本库最核心的关切之一正是「所据为摹本还是原作」——canon 对魏晋南北朝的
    # dispute 原话是「顧愷之風格」「王羲之書風」由若干摹本与刻帖互相定义、存在循环论证。
    # 精选检索按名索取时尤其容易撞上：查「漢宮春曉圖」（仇英，明）
    # 实得「清丁觀鵬倣仇英漢宮春曉圖」——**它是一件真物，但不是所求的那一件。**
    # 包含子串不等于同一件物：同批里「百駿圖」还匹配到了「百駿圖圓墨」（一方墨）。
    if re.search(r"[倣仿摹臨]", title):
        secs["basics"].append({
            "t": "stmt", "state": "disp",
            "text": f"**本件题名含「倣／仿／摹／臨」，是摹本或仿作而非原作**"
                    f"（馆方原题「{title}」）。本库一律标明所据为摹本还是原作——"
                    f"摹本自有其年代与作者，不可当作被摹原作的替代品；"
                    f"若按名检索所得，须留意**所求与所得可能不是同一件物**。"})

    # 釋文 是碑刻／拓本的题记全文，**是这一类最要紧的一栏**，
    # 落成 colophon 块而不是塞进键值表——塞进去就查不了、统计不了。
    if b.get("釋文"):
        secs.setdefault("colophons" if kind in ("画作", "书法") else "record", []
                        ).append({"t": "colophon", "by": "馆方釋文",
                                  "text": b["釋文"][:1800]})

    # 款識 → colophon。**这是要这个渠道的主要理由，不许摊平进散文。**
    ins = d.get("款識") or []
    if ins:
        col = []
        for r in ins:
            pos = r[0] if len(r) > 0 else ""
            typ = r[1] if len(r) > 1 else ""
            txt = r[2] if len(r) > 2 else ""
            if not txt:
                continue
            col.append({"t": "colophon",
                        "by": f"{typ}·{pos}" if pos else (typ or "款識"),
                        "text": txt})
        if col:
            secs["colophons" if kind in ("画作", "书法") else "inscription"] = col
    # **断代依据独立成节。**器物／石刻／窟寺／雕塑四组的契约要求 dating 节里
    # 有 dating 块或 gap——「只写年份等于把不同等级的证据抹平」。
    # 有年號款／人名款就落 dating 块（basis 款识）；
    # 没有就落 gap：**馆方给了年代而未说明凭什么，这正是 gap 该说的话。**
    dat = [r for r in ins if len(r) > 1 and r[1] in DATING_INS
           and len(r) > 2 and r[2]]
    if dat:
        secs["dating"] = [{
            "t": "dating", "basis": "款识", "conf": "较可靠",
            "text": "馆方著录的款识：" + "；".join(f"{r[1]}「{r[2]}」" for r in dat)
                    + "。**款识可作断代依据，但本库未核对原件字迹**，"
                      "故不标确证；后加款的可能性未排除。"}]
    else:
        secs["dating"] = [{
            "t": "gap",
            "text": f"馆方定年为「{era[0] if era else '未记'}」而未在开放资料中"
                    f"说明依据，本库亦未考订——**年代与断代依据是两件事**，"
                    f"此处只有前者"}]

    # 石刻组的「拓本谱系」必备 rubbing 块或 gap——契约原话是
    # 「不注明所据拓本，字口存损无从判断」。馆方开放资料不注明拓本等级，
    # **那就如实立 gap，不假造一个等级。**
    if kind in ("碑刻", "摩崖", "画像石", "经幢"):
        secs["rubbings"] = [{
            "t": "gap",
            "text": "馆方开放资料未注明所据拓本等级（宋拓／明拓／清拓／近拓／翻刻／"
                    "原石现状），本库亦未核——**拓本等级直接决定字口存损的可判性**，"
                    "缺这一栏则本条只能当作「馆藏一份拓本」的记录，"
                    "不能据以讨论字口"}]

    # 參考資料 → 著录书目。**这是西方馆 API 一概没有的一栏**，本库著录链正需要它。
    ref = [r for r in (d.get("參考資料") or []) if r and r[0]]
    if ref:
        blk = {"t": "kv", "rows": [
            [r[0][:60], "　".join(x for x in r[1:] if x)[:80] or "—"]
            for r in ref[:8]]}
        # 石刻组有专设的「著录与考订」一节；书画组没有，落在基础信息里。
        secs.setdefault("record" if kind == "拓片" else "basics", []).append(blk)

    return {
        # **id 用 (dep, id) 而不是文物統一編號**：编号带中文前缀（故琺／贈拓／購拓／
        # 故瓷／中文…），过不了契约的 `^[a-z0-9-]+$`，而为它维护一张罗马化表
        # 是无谓的负担。(dep, id) 本就是馆方的真键，且天然是 ASCII。
        # 编号本身照录在基础信息的键值表里，不丢。
        "schema": "work/1", "id": f"npm-{dep.lower()}-{i}",
        "depth": "record", "title": title, "kind": kind, "period": pid,
        "holder": HOLDER,
        "year": era[-1] if len(era) > 1 else (era[0] if era else ""),
        "size": b.get("尺寸", ""),
        "one_line": f"台北故宫藏{rtype}，馆方時代作「{era[0] if era else '不詳'}」。"
                    f"**本条为著录级**：照录馆方开放资料，未作阐释。",
        # 台北故宫是藏家本身，故 museum 成立——与 CAOD（聚合方）的分别必须守住。
        "verification": "museum", "updated": "2026-08-05",
        "sources": [{"ref": "npm-taipei",
                     "note": f"馆方开放资料页，文物統一編號 {sn}"}],
        "image_status": "pending",
        "sections": secs,
    }, None


def run(rtype, n, apply=False, size=15):
    have = {p.stem for p in (WIKI / "data" / "works").glob("*.json")}
    items, pc = search(rtype, 1)
    if not pc:
        raise SystemExit(f"分類 {rtype!r} 查不到——检查取值是否在 TYPES 里")
    print(f"分類 {rtype}：{pc} 页（≈{pc*15} 件），本次取前 {n} 件"
          f"{'（落盘）' if apply else '（试跑，不落盘）'}\n")
    done = skip = wrote = 0
    reasons = collections.Counter()
    page = 1
    while done < n and page <= pc:
        if page > 1:
            items, _ = search(rtype, page, size); time.sleep(GAP)
        for i, dep in items:
            if done >= n:
                break
            st, h = fetch(DETAIL.format(id=i, dep=dep)); time.sleep(GAP)
            done += 1
            if st != 200:
                reasons[f"HTTP {st}"] += 1; skip += 1; continue
            d = parse(h)
            w, why = build(i, dep, d, rtype)
            if not w:
                reasons[why] += 1; skip += 1; continue
            if w["id"] in have:
                reasons["已存在"] += 1; skip += 1; continue
            if apply:
                (WIKI / "data" / "works" / f"{w['id']}.json").write_text(
                    json.dumps(w, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                have.add(w["id"])
            wrote += 1
        page += 1
    print(f"读 {done} 件 · 建 {wrote} 条 · 跳 {skip} 条")
    if reasons:
        print("跳过原因：")
        for k, v in reasons.most_common():
            print(f"   {v:>4}  {k}")


# ── 精选 ──────────────────────────────────────────────────────────────────
#
# 用户定的方式：**台北故宫用精选，不必求量。**这与本库一路要纠正的毛病正相反——
# 此前填充顺序由 API 可得性决定，于是库的形状变成了开放数据计划的形状。
# 精选是把顺序交还给美术史：**先问该有什么，再问接口给不给。**
#
# 查询串一律用**繁体**。实测简体基本查不到（「早春图」0 条 vs「早春圖」2 条），
# 而且不止繁简：「溪山行旅圖」与「谿山行旅圖」是异体字之别，连繁体也要写对那一个。
# ——L11 的补充规则说按名比对必在繁简与别名两处失效，这里两处都撞上了，
# 所以这份清单是**人工按馆方写法写的**，不做自动转换。假装能自动转是更坏的选择。
PICKS = [
    # 书画 · 本库正典链上的名件
    "谿山行旅圖", "早春圖", "萬壑松風圖", "雙喜圖", "山鷓棘雀圖", "龍宿郊民圖",
    "快雪時晴帖", "祭姪文稿", "自敘帖", "黃州寒食詩", "花氣薰人帖", "蜀素帖",
    "鵲華秋色圖", "富春山居圖", "牧馬圖", "漢宮春曉圖", "百駿圖", "秋林群鹿",
    "四庫全書", "谿山秋霽", "寫生珍禽圖", "溪山清遠",
    # 器物 · 青铜与陶瓷的正典位
    "毛公鼎", "散氏盤", "宗周鐘", "蟠龍方壺",
    "蓮花式溫碗", "嬰兒枕", "青瓷無紋水仙盆", "青瓷葵花式碗",
    # 玉与雕刻
    "翠玉白菜", "肉形石", "玉辟邪", "雕橄欖核舟", "多寶格",
    # 珐琅与漆
    "掐絲琺瑯番蓮紋", "剔紅",
]


def _norm(t):
    """题名去掉馆方的朝代／作者前缀与形制后缀，用于与本库已有条目比对。"""
    t = re.sub(r"[\s　]+", "", t or "")
    return re.sub(r"(軸|卷|冊|頁|屏|扇|一軸|一卷)$", "", t)


def _have_titles():
    out = {}
    for f in (WIKI / "data" / "works").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[_norm(d.get("title"))] = d["id"]
    return out


def pick(apply=False, limit=0):
    """按精选清单逐条检索并摄入。

    三道防线，每一道都是撞过的坑换来的：
    一、**限速 5 秒**。急促请求会被限流，而限流的样子是「返回 0 条」。
    二、**0 条必重试一次**。否则会把「被限流」记成「馆里没有这件」——
        把一件真实存在的名作写成缺藏，比漏摄严重得多。
    三、**返回题名必须真含查询串**。检索是模糊的：
        实测「快雪時晴帖」一度返回首条「古側釐紙」。不验就会摄错物。
    """
    have = _have_titles()
    hit = miss = dup = wrote = 0
    rows = []
    todo = PICKS[:limit] if limit else PICKS
    for kw in todo:
        items, pc = search(None, 1, 15, content=kw)
        time.sleep(5.0)
        if not items:
            # 第二道防线：0 条先当作可能被限流，隔久一点再问一次。
            time.sleep(8.0)
            items, pc = search(None, 1, 15, content=kw)
            time.sleep(5.0)
        if not items:
            miss += 1
            rows.append((kw, "—", "查无（已重试一次）"))
            continue
        got = None
        for i, dep in items[:6]:
            st, h = fetch(DETAIL.format(id=i, dep=dep)); time.sleep(GAP)
            if st != 200:
                continue
            d = parse(h)
            b = d.get("基本資料") or {}
            title = (_split(b.get("品名")) or [""])[0]
            # 第三道防线：题名必须真含查询串。
            if kw not in re.sub(r"[\s　]+", "", title):
                continue
            got = (i, dep, d, title, b.get("分類", ""))
            break
        if not got:
            miss += 1
            rows.append((kw, "—", f"检索有 {len(items)} 条但题名均不含该串"))
            continue
        i, dep, d, title, cat = got
        hit += 1
        if _norm(title) in have:
            dup += 1
            rows.append((kw, title[:26], f"本库已有 {have[_norm(title)]}"))
            continue
        w, why = build(i, dep, d, cat)
        if not w:
            rows.append((kw, title[:26], f"建不出：{why}"))
            continue
        w["one_line"] = (w["one_line"].replace("**本条为著录级**",
                         "**本条为精选著录级**"))
        w["sections"]["basics"].append({
            "t": "stmt", "state": "pend",
            "text": "**本条属精选摄入**：由本库按美术史正典位点名索取，"
                    "而非按接口顺序取得——**先问该有什么，再问接口给不给**。"
                    "内容仍是照录馆方开放资料，未作阐释，是待升为完整级的候选。"})
        if apply:
            (WIKI / "data" / "works" / f"{w['id']}.json").write_text(
                json.dumps(w, ensure_ascii=False, indent=2) + chr(10),
                encoding="utf-8")
            have[_norm(title)] = w["id"]
        wrote += 1
        rows.append((kw, title[:26], f"→ {w['id']}　{cat}／{w['period']}"))

    print(f"{'查询串':<16}{'馆方题名':<28}结果")
    for a, b_, c in rows:
        print(f"{a:<16}{b_:<28}{c}")
    print()
    print(f"清单 {len(todo)} 条 · 命中 {hit} · 查无 {miss} · 本库已有 {dup} · "
          f"新建 {wrote}{'（落盘）' if apply else '（试跑）'}")


def main():
    ap = argparse.ArgumentParser(description="台北故宫开放资料摄入")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("survey")
    r = sub.add_parser("run")
    r.add_argument("--type", required=True)
    r.add_argument("--n", type=int, default=40)
    r.add_argument("--apply", action="store_true")
    k = sub.add_parser("pick")
    k.add_argument("--apply", action="store_true")
    k.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "probe":
        probe()
    elif a.cmd == "survey":
        survey()
    elif a.cmd == "pick":
        pick(a.apply, a.limit)
    else:
        run(a.type, a.n, a.apply)


if __name__ == "__main__":
    main()

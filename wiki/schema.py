"""中国美术史 · 数据契约与校验器。

六类实体，data/ 下的 JSON 是唯一真相源：

    artist   人    画家书家篆刻家雕塑家
    work     物    一切单件——书画、青铜、陶瓷、玉器、石刻、壁画、塑像…
    site     地    遗址与窟龛，用 parent 自嵌套（莫高窟 → 第 45 窟）
    class    类    器类、窑口、书体、画科、流派、技法、纹样、材质、制度、考古学文化
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

# Windows 终端默认走本地码页，中文报错会变乱码——一个看不懂的报错等于没报错，
# 撰写者会跳过它。这里直接把 stdout 定死 utf-8，不再要求调用方记得设环境变量。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

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
    # **「后世题跋」是第十一档，由数据逼出来的。**
    # 它既不是「款识」（那是作者本人留在物上的），也不是「文献著录」（那是物之外的文本）——
    # **它写在这件物上，却出自后来另一个人之手。**这三者的证据性质不同：
    # 款识是自证；著录是旁证；**题跋是「后人在物上留下的判断」，它有物证的外观而无同时代的效力。**
    #
    # 本库已三次撞上同一形态而不得不硬塞别的档：
    # 《上阳台帖》归李白靠宋徽宗题签；《青卞隐居图》的正典地位靠董其昌题「天下第一王叔明」；
    # 《江山秋色图》归赵伯驹靠明初朱标跋——**距作品年代两百余年。**
    # 分出这一档，是为了让「归属靠一条题跋」这件事在数据里可见，
    # **而不是被「文献著录」四个字盖住。**
    # 注意：本档同样不宜判「确证」，理由与风格比对相近但不相同——
    # 题跋可以确凿存在而其判断仍然是错的。
    "后世题跋": "作品上有后人题跋而无作者款印，归属或年代由该题跋给出",
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
#
# **「唐拓」是这一波填书法缺口时被数据逼出来的第七档。**
# 原六档以「宋拓」封顶，而实际撞上三件都在它之上：温泉铭（敦煌藏经洞出唐拓，
# 现藏法国国家图书馆）、孔子庙堂碑三井本、孟法师碑日藏孤本。
# 一路代理如实用 `gap` 块写「词表无法覆盖，不强行归类」而没有硬塞「宋拓」——
# **那是对的做法，而它同时说明词表少了一档。**
# 唐拓存世以孤本计，且多数出自敦煌一路，与宋拓不是「更早一点」的关系，
# **它往往是某碑唯一未经重刻的证据链起点。**
RUBBING_EDITIONS = {
    "唐拓": "存世以孤本计，多出敦煌——常是某碑唯一未经重刻的证据起点",
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
    # 以下两类是馆方档案层，与 colophon 互补而不重叠：
    # colophon 是**刻在物件上**的证据（题跋、鉴藏印），中国书画独有、可目验；
    # prov／exhib 是**物件之外的档案记录**（买卖、捐赠、入藏、参展），
    # 由机构登记而非物件自身携带。二者证据性质不同，不可混为一栏。
    "prov": {"when", "text"},                 # 递藏（馆方 provenance 档案）
    "exhib": {"when", "title"},               # 展览史
}

# 展览史为何是一等证据而非装饰：
# 一件物参加过哪些展览、何时何地，是**有年月、有主办方、有图录可查**的外部记录，
# 证据强度远高于本库承认的最弱一档「风格比对」。而且展览史就是接受史——
# 1935 年伦敦中国艺术国际展览会挑走的那批东西，此后八十年一直被当作中国艺术的
# 代表作，「名作」名单有相当部分是展览造出来的，不是自古如此。
# 不记展览史，就看不见这层塑造。

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
# 原为四档（绘画/书法/篆刻/雕塑）——那是传统画史的四门，而本库的门类轴有十六门。
# 填缺口时当场撞上：刘秉忠是都城规划者、吕彦直是建筑师、唐英是督陶官，
# **一档都不属**，而 cat 是必填，于是代理只能强制映射并自标「权宜处理」。
# **让撰写者为词表的不足去凑一个错答案，是词表的问题不是撰写者的问题。**
# 补两档：
#   建筑 —— 吕彦直、刘秉忠、样式雷一系。本库提纲第 3 门即建筑，
#           而它此前既无 work.kind 也无 artist.cat，人与物两头都没有位置。
#   工艺 —— 唐英一类督陶官、陆子冈一类琢玉匠。
#           **工艺家进入美术史的方式与文人画家完全不同**（靠款识、御窑档案与后世追慕，
#           而非著录品第），给他们一个自己的门类，才不必把督陶官写成书法家。
# **这个字段回答的是「此人以何种身份进入美术史」，不是「他会做哪一门艺术」。**
# 这层区别是被数据逼出来的：忽必烈是敕命赞助人、建安虞氏与余象斗是刻书书坊、
# 曹寅是江宁织造监督官、端方与吴大澂是收藏兼考订者——**他们都在美术史里占位置，
# 而一个都不是「做画的人」**。此前代理只能把忽必烈归「雕塑」、把书坊归「绘画」，
# 并各自标注「退而求其次」。
#
# 而本库对非制作者本就不该含糊：**「谁出钱谁进画面」是它反复处理的主题之一**
# （莫高窟第 98 窟的供养人像尺幅对应现实权力位阶、永乐宫画工题记、
# 广胜寺寺方以壁画售款修庙）。赞助者若在数据里只能伪装成雕塑家，这条线就断了。
#
# 三档非制作者身份：
#   赞助 —— 敕命者、功德主、供养人。**出资与决策同样塑造了作品，而这不是手艺。**
#   鉴藏 —— 收藏、考订、著录者。本库已立一条:**收藏家的贡献与他所藏之物的真伪
#            是两件事，不要互相佐证。**
#   出版 —— 书坊主与刊刻组织者。版画是画、刻、印三方分工，
#            **而书坊主既不画也不刻**，把他归「绘画」等于把出版说成创作。
ARTIST_CAT = {"绘画", "书法", "篆刻", "雕塑", "建筑", "工艺",
              "赞助", "鉴藏", "出版"}

# 六类实体都可带一个可选的 `alias`（字符串列表）：**本条目还被叫作哪些名字。**
#
# 加它的理由是同一个错撞了四次：提纲点「牛河梁」而条目名作「牛河梁遗址」、
# 点「殷墟发掘」而条目题「殷墟十五次发掘」、点「越窑青瓷」而已有「越窑」、
# 台北故宫作者作「范寬」而本库艺术家名作「范宽」——**每次覆盖率都少算，
# 而每次我都另发明一条规则去绕（通名后缀表、罗马名反查）。**
# 规则各有失效点，而 alias 是**撰写者亲手认定的等同关系**：
# 它把「这两个名字指同一物」从推测变成写下来的事实。
# L11 的补充规则说可靠证据是稳定标识符而非字形，alias 就是那个可写下来的标识。
#
# 只在**确实同指一物**时写。近似、师承、同名异物一律不写——
# 写错一条 alias 会让两件不同的东西在对账里合成一件，比少算更坏。

# holder 的已知别名。**不设封闭词表**——藏家是开放集合，把它封闭才是错的；
# 但同一家机构写成两个名字，会让「哪个馆藏了什么」的统计直接失真。
# 实测已经裂了两处：「台北故宫博物院」8 条对「国立故宫博物院（台北）」59 条、
# 「故宫博物院」6 条对「故宫博物院（北京）」10 条。
# 与 facet 是同一类问题：**必填而不校验取值的自由文本字段一定会漂。**
# `holder` 是必填，而有些物**确实没有单一藏所**：原址不可移动、原迹已佚只存拓本、
# 一个题材系列分藏多家、下落不明。必填而无合法的「不详」写法，撰写者就只能把
# 整句解释塞进机构名字段——实测已发生六七处，如
# 「原刊本本库未核实具体现藏机构；后世翻刻本与现代影印本分散流传……」。
# **让撰写者为契约的不足去凑一个错答案，是契约的问题**，与 artist.cat 只有四门时
# 逼人把督陶官写成书法家是同一形态。
#
# 以下哨兵不是我新拟的，是**撰写者已经自发用起来并且用得一致的**，此处只作追认：
#   现藏未定（见争议字段）   —— 传世而现藏不明
#   原址（<具体地点>）        —— 不可移动，仍在原处
#   已佚 / 原石已佚          —— 原物不存，所存为拓本或摹本
#   分藏多处（<说明>）        —— 无单一藏所
# 用哨兵时，缘由写进条目的 stmt／gap，**不写进这个字段**。
HOLDER_SENTINEL = ("现藏未定", "原址", "已佚", "分藏多处", "下落不明")

HOLDER_ALIAS = {
    "台北故宫博物院": "国立故宫博物院（台北）",
    "台北故宫": "国立故宫博物院（台北）",
    "国立故宫博物院": "国立故宫博物院（台北）",
    "故宫博物院": "故宫博物院（北京）",   # 不加限定语时指北京，但本库要求写明
    "北京故宫博物院": "故宫博物院（北京）",
    "北京故宫": "故宫博物院（北京）",
    "上博": "上海博物馆",
    "国博": "中国国家博物馆",
    "大都会博物馆": "大都会艺术博物馆",
}

# work 的可选 `scope`：**这一条说的是一件物，还是一个作品族？**
#
# 被数据逼出来的，且是同一形态撞了两次：
#   《兰亭序》—— `wang-xizhi-lanting-xu` 涵盖唐摹诸本与定武刻帖两系统，
#                而 `lanting-xu-shenlongben` 是其中具体一本；
#   《洛神赋图》—— `gu-kaizhi-luoshenfu-tu` 挂在顾恺之名下而无尺寸无藏品号，
#                与之并列的是故宫本、辽博本，另有大英本（1930,1015,0.2）、
#                弗利尔乙本（F1968.12，另说归陆探微）。
#
# 这类条目**不是重复,也不是某一件物**：原作不存，所知只是一个由摹本与刻帖
# 共同支撑的作品概念。Wikidata 对此的处理与本库撞出的结构一致——
# Q22079066「洛神賦圖」只挂作者、既无 P217 藏品号也无 P195 收藏机构。
#
# 不给这层一个字段，后果有两个，都真实发生了：
#   一、audit 的重复检查只能一直报警（概念条目与其某一摹本同题同作者）；
#   二、**读者分不出「这是那件东西」与「这是那个作品」**——而对中国早期绘画，
#       这恰恰是最要紧的区别：顾恺之、王羲之名下所见皆非真迹。
#
#   object（默认，不写即是）—— 一件具体实物，有藏地、有尺寸
#   family              —— 作品族／概念条目，涵盖多摹本或多刻帖；
#                          它的图版必然借自其中某一本，图注须写明是哪一本
WORK_SCOPE = {"object", "family"}

# work 的维度按 kind 分派。不给每种材质造一套，归成五组。
WORK_KIND_GROUP = {
    # 「版画」是清点馆藏时才发现漏掉的：克利夫兰中国部抽样 500 件里 Print 占 98 件，
    # 全库估计约五百。佛经版画、明清小说插图、年画、《芥子园画传》一类图谱，
    # **是中国美术史的实质门类而本库原先整个没有**。归书画组：它是平面作品，
    # 那一组的维度（画面分析／技法与材料／题跋与鉴藏）正对得上。
    "画作": "书画", "书法": "书画", "篆刻": "书画", "版画": "书画",
    # 「珐琅」同为清点时补入：掐丝珐琅与画珐琅在明清宫廷工艺里自成一路，
    # 材质与工序都不能归入金银器或陶瓷。
    "青铜": "器物", "陶瓷": "器物", "玉器": "器物", "漆器": "器物",
    "织绣": "器物", "金银器": "器物", "家具": "器物", "竹木牙角": "器物",
    "珐琅": "器物",
    # 「玻璃器」补于第二轮填缺口时，**被三路代理各自独立顶出来**：
    # 清宫玻璃厂（纪理安主持）、法门寺地宫玻璃器（伊斯兰系统输入品）、套料玻璃。
    # 而本库提纲的第 9 门本就名为「金银器·珐琅·玻璃」——**门里写着玻璃，词表里没有玻璃**,
    # 于是三路都只能在 disputes 里记缺口，并且都明确拒绝了「勉强塞进金银器或珐琅了事」。
    # 三次独立确认，证据够了。
    #
    # 归器物组：它需要的正是那一组的维度（器形与纹饰／工艺／出土与著录／断代依据）。
    # 另注意本库已立的一条分辨：**法门寺地宫那批是输入品，与中国自制玻璃是两回事**。
    "玻璃器": "器物",
    # **「拓片」曾被立为独立 kind，现已撤销：拓本并入碑，作为碑的补充。**
    #
    # 撤销的理由不是拓本不重要，恰恰相反——是原设计把它摆错了位置。
    # 立它时的说法是「拓片与碑刻是两件物：碑是石，拓片是那块石在某一时刻的一份记录」，
    # 这个认识没错，但**它要表达的东西 `rubbing` 块已经在表达**：
    # 宋拓／明拓／清拓／近拓／翻刻／原石现状 六档，正是「你看的是哪一份记录」。
    # 拆成两个实体反而制造了一个新问题：**拓本自身往往没有制作年代。**
    # 实测台北故宫的拓片著录——题名里记着碑的年代（「漢武都太守李翕碑」），
    # 而拓本何时所拓一概不记；20 件试摄有 19 件因「無時代」落不了地。
    # 并入碑之后，年代归碑（可从碑主与立石纪年得），拓本归 `rubbing` 块，
    # 两件事各得其所——**而「早拓与近拓字口不同」这一层一点没丢。**
    #
    # 实施代价为零：撤销时 kind=拓片 的条目数为 0，该 kind 声明后从未落过条目。
    "碑刻": "石刻", "摩崖": "石刻", "画像石": "石刻", "经幢": "石刻",
    "壁画": "窟寺", "塑像": "窟寺",
    # 「铜佛」原名对器物断言了「佛教」，而实际装进来的有道教造像、湿婆、
    # 文殊骑狮、立马，甚至羊尊与镇——**根源是摄入映射把「Sculpture + bronze」
    # 一律判为铜佛，不问题材**，而词表的名字又替它把这个错说成了事实。
    # 改为宗教中立的「铜造像」。
    # 「陶俑」是填缺口时暴露出来的缺档。此前俑一律归「陶瓷」，而陶瓷走**器物组**
    # （器形与纹饰／铭文释读／工艺／出土与著录／断代依据／来源与流转），
    # **对一件俑来说「铭文释读」几乎永远是空的，而它真正需要的「形制与题材」没有位置。**
    # 俑是塑出来的人与物，与瓶罐盘碗是两回事；归雕塑组才对得上维度。
    # 触发点：将军俑被塞进陶瓷，而本库秦汉×雕塑造像那一格长期为 0——
    # **词表缺一档，就等于让一整格永远填不上。**
    "石雕": "雕塑", "木雕": "雕塑", "铜造像": "雕塑", "陶俑": "雕塑",
}

# 「刻本」单列，理由与上面同类但更强。
# 开宝藏、嘉兴藏、永乐北藏、房山石经这类是**跨数十年乃至百年的成套文本工程**，
# 不是一件平面作品；此前被权宜归入「版画」，而版画走书画组的「画面分析」一节
# ——**一部大藏经没有「画面」可分析**。
# 它们在美术史里的位置在于**刊刻组织、赞助结构与规模**，以及卷首图一类附图的样式。
# 仍归书画组（技法与材料、题跋与鉴藏、来源与流转都用得上），
# 但 kind 分开，好让「这是一部书而不是一张画」在数据里看得见。
WORK_KIND_GROUP["刻本"] = "书画"

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

# class 的分面。**此前这是自由文本字段**：README 与本文件开头都写着七个分面
# （器类、窑口、书体、画科、流派、技法、纹样），而校验器从不检查取值——
# 声明了词表却不强制，词表就会无声漂移。写 facet: "材质" 不报错，
# 于是它已经悄悄出现在两条条目里。**这里补上强制。**
#
# 同时补三格，都是被提纲点名而七格装不下的：
#   材质    —— 黄花梨、紫檀、红木。木料是家具史的首要分类轴，
#              而本库 19 件家具条目全靠自由文本 medium 字段记材质。
#   制度    —— 官搭民烧、工官制度、物勒工名、列鼎制度、礼制改革。
#              这些是生产与用器的组织方式，不是器形也不是技法。
#              「官搭民烧」曾被权宜塞进「技法」，那是把制度读成手艺。
#   考古学文化 —— 仰韶、马家窑、龙山、良渚、红山、大汶口、楚。
#              依据不是权宜：canon 的新石器 keys 原文即
#              「仰韶彩陶、马家窑、龙山黑陶、良渚玉器、红山玉器、陶寺」,
#              春秋战国 keys 里也直接写着「楚文化」——**该期本就按考古学文化组织**,
#              而朝代轴在史前根本不适用。
# 「流通」是第十一档，补于第二轮填缺口时——**同一类东西撞了五次**：
# 画廊、拍卖、古玩市场、独立策展人、景漂。它们都不是物的类别，也不是生产的组织方式；
# 一路代理把「古玩市场」归「制度」并自标为「把那一档从生产端扩到流通端，
# 是我判断最弱的一处」，提请复核——**提请得对。**
#
# 而本库对这条轴本就不含糊：它把展览史立为一等证据，原话是
# **「展览史就是接受史……『名作』名单有相当部分是展览造出来的，不是自古如此」**；
# `prov`（递藏）与 `exhib`（展览史）两个块也正是为这条轴设的。
# **物怎么流动、由谁定价、经谁展示，与物本身同为美术史的对象。**
# 这一档收市场、拍卖、画廊、策展、鉴藏交易与当代艺术区一类机制。
CLASS_FACETS = {"器类", "窑口", "书体", "画科", "流派", "技法", "纹样",
                "材质", "制度", "考古学文化", "流通"}

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
    "smithsonian", "met", "cleveland", "harvard", "princeton", "artic", "other",
    # 豆瓣相册转录的二维书画扫描。平面公版作品的忠实复制不产生新著作权，故可用；
    # 但这些扫描多为 600px 的网页显示件（已实测：豆瓣图床 raw 档与之字节相同，
    # 上游没有更大的），只能作图示，不足以论笔墨——见下方「图示与图版」。
    "douban",
}

# ── 范围：只以中国境内书画为主，禁止再引入海外藏品 ──────────────────────
#
# **这是项目所有者定的范围，不是技术判断，不得以「数据可得」为由绕过。**
#
# 缘由与本库既有的那条「不对称」正相反：西方馆有 API、有开放许可、有递藏与展览史，
# 于是**可得性会悄悄替代重要性**——工具好用的地方条目就多，而那恰恰不是
# 中国美术史的重心。实测后果已经发生：全库 3279 件作品里 2204 件（67%）是海外藏品，
# 其中 cma-／artic- 前缀 2019 条直接以克利夫兰与芝加哥的藏品号立目；
# 海外那批又以器物为主（陶瓷 802、青铜 229、玉器 180），而本库的重心本应是书画。
#
# **一件事能做，不等于它是该做的那件事。**取图与档案采集工具越顺手，
# 这个偏差就越隐蔽——它不表现为错误，只表现为库的重心悄悄挪走。
#
# 执行：
#   一、不再新增以海外机构为 holder 的条目；
#   二、`museum.py`（克利夫兰／芝加哥档案采集）与 `fetchimg.py` 的 cma／artic／met
#       连接器**不得再用于新增条目**，仅可用于已有条目的核校；
#   三、境内藏品缺图是权属现实（见 image_status），**不以「海外有图」为理由
#       改用海外同类物顶替**；
#   四、既有 2204 条海外条目**经项目所有者决定原样保留**，不删不改，
#       但冻结在 `data/overseas-baseline.json`；此后新增的海外藏品条目由校验器拦下。
#   五、「书画为主」的口径同经决定: 重心在画作与书法，**境内器物条目保留但不再扩充**。

OVERSEAS_HOLDER = re.compile(
    r"大都会|克利夫兰|芝加哥|波士顿|弗利尔|赛克勒|哈佛|普林斯顿|纳尔逊|阿特金|"
    r"大英|维多利亚与艾伯特|V&A|吉美|赛努奇|柏林|科隆|斯德哥尔摩|苏黎世|"
    r"东京国立|京都国立|奈良|大阪市立|根津|泉屋|静嘉堂|藤井|正倉院|正仓院|"
    r"首尔|韩国国立|旧金山|西雅图|明尼阿波利斯|底特律|印第安纳|圣路易斯|"
    r"华盛顿|史密森|美国|英国|法国|德国|日本|瑞典|瑞士|加拿大|澳大利亚|"
    r"Museum|Gallery|Institute|Collection")
# 境内标记优先：「故宫博物院（北京）」不含海外词，但「上海博物馆（Shanghai Museum）」
# 会命中 Museum——先判境内，命中即不再判海外，否则会把国内馆误判成海外。
DOMESTIC_HOLDER = re.compile(
    r"故宫|国家博物馆|上海博物馆|南京博物院|辽宁省|吉林省|湖北省|湖南|"
    r"河南|陕西|山西|甘肃|浙江|天津|首都博物馆|敦煌|云冈|龙门|麦积山|"
    r"大足|国家图书馆|中国美术馆|台北|原址|现藏未定|已佚|分藏多处")


def is_overseas(holder):
    h = str(holder or "")
    return bool(OVERSEAS_HOLDER.search(h)) and not DOMESTIC_HOLDER.search(h)


def _overseas_baseline():
    p = DATA / "overseas-baseline.json"
    if not p.exists():
        return None
    return set(json.loads(p.read_text(encoding="utf-8")).get("ids") or [])


# ── 图示与图版：本库的取舍 ────────────────────────────────────────────────
#
# **本库的图是图示（identification），不是图版（connoisseurship）。**
# 图示要回答的是「这件东西长什么样、是不是你以为的那一件」；
# 图版要回答的是「这一笔怎么走的、绢的织法如何、款印真伪」。后者需要极高分辨率
# 与色彩管理，本库不承担，也不假装承担。
#
# 这个取舍有两个后果，必须都认：
# 一、**有图示胜于无图。**一件宋画有 600px 的忠实翻拍，就该放上去；
#    以「不够清晰」为由留白，是把图版的标准用在图示的位置上。
# 二、**低分辨率必须让读者看见。**600px 认得出是《浮玉山居图》，
#    但据此谈钱选的用笔就是空话。所以 render.py 对宽度不足 1000px 的图
#    **自动**打「图示级」标记——自动而非靠撰写者记得，因为忘记加标记的那一次
#    正是最需要它的那一次。
#
# 需要高分辨率的地方另有出路：IIIF（`image.iiif`）与馆方原图链接（`image.full`）
# 都在字段里，读者要细看可以点过去。本库不复制那份带宽。

IMAGE_STATUS = {
    "copyright": "仍在版权期或馆方未开放授权",
    "no-free-image": "已属公版，但未找到可用的自由授权复制品",
    "lost": "原物已佚或已毁",
    # 原先写作「原址不可移动**且**馆方未开放影像」，把两件事用「且」绑死了，这是错的：
    # 「原址不可移动」是物的状态，「拍不到」才是缺图的理由。莫高窟窟内禁止摄影，
    # 这一档成立；云冈、龙门、大足露天者谁都拍得到，标这一档就是拿物的状态
    # 替缺图开脱。**用此档必须说明是哪一种,不能只写「原址」。**
    "in-situ": "原址不可移动，且现场禁摄或影像受管制——露天可摄者不属此档，那是欠账",
    "pending": "尚未补图",
}

# 关于取图边界，本库的立场（写在这里而不是散在各条目里）：
#
# 一、**二维书画的忠实翻拍不产生新的著作权。**宋画早已进入公有领域，馆方对翻拍件
#    主张的权利在平面忠实复制上站不住（Bridgeman v. Corel 一系判例、多国实践与
#    Wikimedia 政策均如此处理）。所以这类作品标 no-free-image 多半是本库欠账，
#    不是权属现实。
# 二、**立体器物的新摄影有摄影者的独创性。**后母戊鼎本身无著作权，国博拍的那张
#    照片有。不能因站点非营利就直接取用——**非营利不是著作权豁免**，中国著作权法
#    的合理使用是列举式的，公开传播全图不在其中。
#
#    **判准是「有没有明确的自由许可」，不是「谁拍的」。**这一条原先写成
#    「要用参观者自摄，不要用馆方摄影」，是拿代理指标替代真正的理由，而代理指标
#    会给出错的答案：台北故宫器物典藏系统释出的毛公鼎照片，该馆自己标示
#    CC BY 4.0——授权明确、可验证，正是本库要的那种，却被那条字面指示挡掉了。
#    所以次序是：**先看许可，许可自由即可用，不问出自馆方还是参观者**；
#    无明确许可时，参观者自摄并以 CC BY-SA 发布的照片才是那条可行的退路。
# 三、**仍在保护期的近现代作品**（如作者卒于 1986 者至 2036）一律不取整图。
#    保护期为作者终生加死后五十年，合作作品自最后死亡的合作者起算。

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
    # 著录级只要 basics，且**不要求 disputes**——它不作阐释，就没有可争之处；
    # 强求一个争议字段只会逼出空话或编造的争议。
    if depth_of(obj) == "record":
        spec, need_disputes = RECORD_SECTIONS, False
    else:
        need_disputes = True
    known = {s for s, _ in spec} | {DISPUTES[0]}
    for sid, label in (list(spec) + ([DISPUTES] if need_disputes else [])):
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


# ── 条目深度：两种体裁，不是完整与残缺 ────────────────────────────────────
#
# 要把本库做到数千条，不可能每条都是十二维、每个断言带四态的写法——
# 而**若不把深度差别标出来，整套认知标注就成了装饰**：读者会以为五千条都经过了
# 那种审查，实则绝大多数只是照录了馆方说了什么。
#
# 所以分两级，并且要认清它们的分别不在「写得多少」，而在**有没有人做过判断**：
#
#   full    完整级。十二维／五组维度俱全，断言带四态，断代附依据与可靠度，
#           争议并陈。**有人读过材料、做过取舍。**
#   record  著录级。只记结构化来源实际说了什么：题名、年代、现藏、材质、尺寸、
#           藏品号、图、出处链接。**不写论述、不作阐释、不下四态判断**——
#           因为确实没人做过，写了就是假的。
#
# 关键的一条：**著录级不是完整级的残缺版，是另一种体裁。**它断言得少，
# 就不该看起来断言得多。同一条原则本库已用过两次——
# 「图示级」标记（600px 认得出是哪件、不足以论笔墨），
# `no-free-image` 与 `copyright` 的分别（一个断言已公版、一个断言仍在保护期）。
#
# 著录级仍受那三条硬约束（断代／重修层／拓本谱系），但可用 `gap` 满足，
# 而那个 gap 是真话：**馆方给了年代，却没有公布这个年代依据什么**。
DEPTH = {
    "full": "完整级：维度俱全，断言带四态，断代附依据",
    "record": "著录级：只照录结构化来源，未作阐释与判断",
}
RECORD_SECTIONS = [("basics", "基础信息")]


def depth_of(obj):
    """缺 `depth` 视为 full——现有条目都是手写的完整条目，默认不能反过来。"""
    return obj.get("depth") or "full"


_OVERSEAS_OK = None


def validate(c):
    global _OVERSEAS_OK
    _OVERSEAS_OK = _overseas_baseline()
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

    # 藏品号相同 → ERROR。**这一条比同题同作者硬得多，因为它比的是稳定标识符。**
    # 实测: 与故宫藏品总目对账时，标题+作者匹配漏掉了 21 件已有条目，
    # 而藏品号一项全部查出。漏的原因全是字形差异——
    #   《洛神赋图》馆方定名「顾恺之洛神图卷」而本库题「宋人洛神赋图卷」（归属写法不同）；
    #   李公麟那卷馆方作「仿韦偃牧放图」而本库作「临韦偃牧放图」（一字之差）；
    #   赵佶那轴馆方作「锦鸡芙蓉图」而本库作「芙蓉锦鸡图」（词序颠倒）。
    # **可靠证据是稳定标识符而非字形**（同 alias 那段的道理）。
    # 只取键名含「藏品号」的 kv 行，不扫全文——正文里为比较而提及别件的藏品号，
    # 不该被当作本条的标识。
    _acc = {}
    for wid, wk in c.works.items():
        for b in (wk.get("sections", {}).get("basics") or []):
            if not isinstance(b, dict) or b.get("t") != "kv":
                continue
            for row in b.get("rows") or []:
                if not (isinstance(row, list) and len(row) == 2):
                    continue
                if "藏品号" not in str(row[0]):
                    continue
                no = str(row[1]).strip()
                # 须像个标识符。**不能要求四位连码**——大都会的号形如「47.18.116」
                # 「02.18.438」，是真标识符却无四位连码，那样判会把它们全漏掉。
                # 改为: 含数字、不含「未核／不详／未见／本库」一类措辞、且不过长。
                # 实测 32 条把占位话填进了「藏品号」字段（「本库未核实」
                # 「本库未见馆方页公布正式编号」），与 holder 那个问题同形——
                # 必填字段没有合法的「不详」写法，撰写者只能塞话进去。
                if (not no or len(no) > 30 or not re.search(r"\d", no)
                        or re.search(r"未核|不详|未见|待核|本库|无编号", no)):
                    continue
                # **键必须是「馆 + 号」而不是号本身。**实测撞出假阳性:
                # 克利夫兰 1961.90 与芝加哥 1961.90 是两件不同的物——
                # 各馆自成编号体系，号相同纯属巧合。只按号查重会拦住正当条目。
                hold = str(wk.get("holder") or "")
                for _k, _v in HOLDER_ALIAS.items():
                    if _k in hold:
                        hold = _v
                        break
                key_acc = (hold, no)
                if key_acc in _acc and _acc[key_acc] != wid:
                    out.append(Problem("ERROR", f"work/{wid}",
                                       f"藏品号「{no}」与 work/{_acc[key_acc]} 相同"
                                       f"（同属「{hold}」）——"
                                       f"**同一藏品号即同一件物，两条条目必有一条多余**。"
                                       f"若为册页的不同开，藏品号须带开次（如 -3/10）；"
                                       f"若一条涵盖多摹本，标 scope=family。"))
                else:
                    _acc.setdefault(key_acc, wid)

    # 同一作者名下同题作品 → ERROR。**audit 只报告，拦不住新增。**
    # 本会话已因此三次撞车: 手写路径与 wd- 采集路径撞 9 组（已合并）；
    # 我据抽样委托新写 14 件，其中 8 件本库已有、另 2 件与 wd- 条目重复。
    # 三条立目路径互不知晓，而重复条目会让「本库有多少件」这个数字本身失真。
    # 放在校验器里，是因为**报告可以被忽略，ERROR 不能**。
    #
    # 只在作者明确时判：同题异作在中国画史极多（历代都有《山水图》《墨竹图》），
    # 作者为空时无从分辨，报出来只会淹掉真重复。
    # scope=family 的作品族条目本就该与其某一摹本同题，豁免。
    _seen = {}
    for wid, wk in c.works.items():
        if wk.get("scope") == "family":
            continue
        a = wk.get("artist")
        if not a or not wk.get("title"):
            continue
        key = (str(wk["title"]).strip(), a)
        if key in _seen:
            out.append(Problem("ERROR", f"work/{wid}",
                               f"与 work/{_seen[key]} 同为「{a}」名下的「{wk['title']}」——"
                               f"**疑为同一件物立了两条**。本库有三条立目路径（手写／wd- 采集／"
                               f"按清单委托），互不去重已撞车三次。若确为两件不同的物"
                               f"（如唐摹本与宋摹本），请在 title 里写明区别；"
                               f"若一件涵盖多摹本，请标 scope=family。"))
        else:
            _seen[key] = wid

    for wid, wk in c.works.items():
        w = f"work/{wid}"
        _check_common(wk, w, c, out, ["title", "kind", "period", "holder"])
        if is_overseas(wk.get("holder")) and _OVERSEAS_OK is not None                 and wid not in _OVERSEAS_OK:
            out.append(Problem("ERROR", w,
                               f"holder {wk.get('holder')!r} 判为海外机构，而本条不在"
                               f"`data/overseas-baseline.json` 的冻结名单中。"
                               f"**项目范围是只以中国境内书画为主，禁止再引入海外藏品**——"
                               f"这不是技术判断，不得以「数据可得」为由绕过。"
                               f"确需例外须显式改那个文件，那是一次有意的决定。"))
        if wk.get("scope") and wk["scope"] not in WORK_SCOPE:
            out.append(Problem("ERROR", w, f"scope 须为 {sorted(WORK_SCOPE)} 之一，"
                                          f"得到 {wk['scope']!r}"))
        if depth_of(wk) not in DEPTH:
            out.append(Problem("ERROR", w, f"depth {wk.get('depth')!r} 须为 {sorted(DEPTH)} 之一"))
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
        if wk.get("holder") in HOLDER_ALIAS:
            out.append(Problem("ERROR", w, f"藏家 {wk['holder']!r} 是别名，请写 {HOLDER_ALIAS[wk['holder']]!r}——同一家写成两个名字，馆藏统计就失真"))
        if not wk.get("image") and wk.get("image_status") not in IMAGE_STATUS:
            out.append(Problem("WARN", w, f"无 image 且未说明原因——请标 image_status"))

    for sid, s in c.sites.items():
        w = f"site/{sid}"
        _check_common(s, w, c, out, ["name", "one_line", "periods"])
        _check_sections(s, SITE_SECTIONS, w, c, out)
        # **遗址断代跨期是常态而非例外**，所以是列表不是单值：龙门自北魏凿到唐末、
        # 莫高窟自十六国到元、大足历晚唐五代两宋、紫禁城明清两朝。
        # 给这些一个单期，就是本库在别处一直拒绝犯的那种抹平——
        # 与「只填一个年份等于把三种证据抹平」是同一条。实测 37 条里 11 条跨期。
        ps = s.get("periods")
        if ps is not None:
            if not isinstance(ps, list) or not ps:
                out.append(Problem("ERROR", w, "periods 须为非空列表——遗址常跨期"))
            else:
                for p in ps:
                    if p not in c.periods:
                        out.append(Problem("ERROR", w, f"periods 含未知分期 {p!r}"))
        if s.get("period"):
            # 单数字段是撰写者按 work/artist 的样子类推出来的，遗址不适用。
            out.append(Problem("ERROR", w, "遗址用 periods（列表）而非 period——"
                                          "单值装不下跨期营建"))
        if s.get("parent"):
            if s["parent"] not in c.sites:
                out.append(Problem("ERROR", w, f"parent {s['parent']!r} 无对应条目"))
            elif s["parent"] == sid:
                out.append(Problem("ERROR", w, "parent 指向自身"))

    for cid, cl in c.classes.items():
        w = f"class/{cid}"
        _check_common(cl, w, c, out, ["name", "facet", "one_line"])
        if cl.get("facet") and cl["facet"] not in CLASS_FACETS:
            out.append(Problem("ERROR", w, f"facet {cl['facet']!r} 不在词表内——声明了词表却不强制，词表就会无声漂移"))
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

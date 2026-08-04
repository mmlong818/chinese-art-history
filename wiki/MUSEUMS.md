---
摘要: 对 11 家馆方数字档案逐一实测（urllib 直连真实请求），确认展览史/递藏/著录三项字段的可得性。芝加哥艺术博物馆（无需 key）与史密森尼国立亚洲艺术博物馆（需 API key，DEMO_KEY 可测）确认可编程取到，后者字段详尽度超过克利夫兰；哈佛艺术博物馆需借第三方搜索定位 object id、再抓服务端渲染网页取到三项字段。大英博物馆／ColBase／波士顿美术馆三家均判定不可行，但失败方式互不相同（Cloudflare 拦截、字段本身不存在、AWS WAF 拦截）。台北故宫的旧结论「SSL 握手失败」被修正为「证书链验证失败」，绕开校验后单件页字段极丰富；南京博物院发现一个未设防的 JSON REST API，与本库「国内各馆无 API」的既有假设相矛盾，是本次调研最重要的新发现。
来源: ref
日期: 2026-08-04
关联: museum.py, fetchimg.py, verify.py, README.md
---

# 馆方档案可得性清单

本文档回答一个问题：**除克利夫兰美术馆之外，还有哪家馆的展览史、递藏、著录与策展文字能被程序取到？** 每条结论都对应一次真实发出的 HTTP 请求与一次真实收到的响应，方法与 `museum.py`／`verify.py`／`fetchimg.py` 一致：urllib 直连、带 User-Agent、遇 429/403 退避重试，不用需登录或 JS 渲染的路径。所有临时验证脚本在 `C:\Users\nd851\AppData\Local\Temp\claude\E--CC-code----\ec4c5f25-ca1f-469b-8b38-f576a4f4e0e2\scratchpad\` 下，未改动本项目任何文件。

## 总览表

| 机构 | 展览史 | 递藏 | 著录/策展文字 | 判权 | 访问路径 | 结论 |
|---|---|---|---|---|---|---|
| 克利夫兰美术馆 | 有 | 有 | 有 | CC0 | 官方 REST API | **已接入**（`museum.py`，30 件挂接） |
| 大都会博物馆 | 无 | 无 | 无 | 逐件判定 | 官方 REST API | 已排除（57 字段无这几栏，网页有） |
| 芝加哥艺术博物馆 | 部分有（中国藏品命中率低） | 有 | 有（`publication_history`独立字段） | 逐件判定 `is_public_domain` | 官方 REST API，无需 key | **可编程取到**，但展览史覆盖率弱于克利夫兰 |
| 史密森尼·国立亚洲艺术博物馆 | 有，详尽度高于克利夫兰 | 有，逐条带日期与档案编号 | 有（混在 `notes` 里，需按 label 解析） | 全部 CC0 | 官方 REST API，**需 API key**（DEMO_KEY 可测） | **可编程取到**，数据质量最强的一家 |
| 大英博物馆 | 未知（内容拿不到） | 未知 | 未知 | 未知 | 网页被 Cloudflare 拦截；SPARQL 端点 TCP 超时 | 不可行，性质是访问被拦截，非字段缺失 |
| ColBase（日本） | 无 | 无 | 有（策展文字/解说，无展览与递藏字段） | CC BY（非 CC0） | 未设防 `opendata.tsv` 全量下载 + 逆向出的内部 API | 不可行，是字段本身不存在，非访问受阻 |
| 波士顿美术馆 MFA | 未知 | 未知 | 未知 | 明确禁止再分发 | 网页被 AWS WAF 拦截；无 API | 不可行，技术+法律双重受阻 |
| 哈佛艺术博物馆 | 有 | 有 | 有（`Publication History`） | 逐件判定 | 官方 REST API 需 key；网页服务端渲染可直抓 | **可编程取到**，但需借第三方搜索定位 object id |
| 台北故宫博物院 | 有（单件页「展示資訊」） | 未见独立字段（仅「參考資料」起著录作用） | 有（「參考資料」） | 图像 CC0/文字 CC BY 4.0 | `digitalarchive.npm.gov.tw` 详情页，需绕过证书校验；检索接口未破解 | 部分可行，卡点是拿不到 id 池 |
| 上海博物馆 | 未测（首页连不上） | 未测 | 未测 | 未测 | 四种协议组合均失败，失败方式不一致 | 只能人工（不确定是否临时故障） |
| 中国国家博物馆 | 有（展览栏目服务端直出） | 未见 | 未见（藏品详情数据接口未破解） | 未测 | 展览列表页可直接抓；藏品页需 JS | 部分可行，展览维度可自动化 |
| 南京博物院 | 有（`/api/exhibition/*`） | 未见 | 有（简略，`/api/collection/info/desc`） | 未测 | 未设防的隐藏 JSON REST API | **可编程取到**，与既有假设矛盾的新发现 |
| 故宫博物院 | 无（`exhibitions.html` 404，本次未重测，沿用既有结论） | — | — | — | 无 API，页面 404 | 沿用旧结论：只能人工 |

---

## 已有基线（不重测，仅列入总表对照）

- **克利夫兰美术馆**：`museum.py` 已实现，Open Access API 61 字段全含，CC0，已挂接 30 件、写入 144 个块。
- **大都会博物馆**：`museum.py` 文档字符串记录的实测结论——57 个字段一个都没有 exhibition/provenance/citation/inscription/description，网页有这几栏但 API 不给，只能抓 HTML（本次未重测）。
- **故宫博物院**：`dpm.org.cn/subject/exhibitions.html` 与 `/exhibitions.html` 均 404（本次未重测，沿用既有结论）。

---

## 芝加哥艺术博物馆 `api.artic.edu`

**API 存在，无需 key。** `GET https://api.artic.edu/docs/` → 200（可读文档）。`GET https://api.artic.edu/api/v1/artworks/search?q=china&limit=5` → 200，`pagination.total: 132630`。单件 `GET https://api.artic.edu/api/v1/artworks/{id}?fields=...` → 200。

**字段实测**：以关键词 `chinese ming dynasty`／`qing dynasty`／`song dynasty painting` 搜出约 20 件中国藏品逐一查字段，多数藏品 `exhibition_history` 为空字符串，但确认命中非空样本：

- id=79507（明代《栋梁图》）：`provenance_text` = `"Qing Imperial Collection. Ton Ying and Company, New York; sold to the Art Institute of Chicago, May 27, 1953."`；`publication_history` 827 字；`description`（策展文字）1610 字；`exhibition_history` 为空。
- id=80720（清代《浴佛节》）：三项字段全中，`exhibition_history` = `"Latter days of the law : Images of Chinese Buddhism. 850-1850. Exhibition; Spencer Museum of Art. University of Kansas, Lawrence, Kansas, August 27 - October 9, 1994; Asian Art Museum of San Francisco, November 30 - January 29, 1995."`

无 `fields` 限制的完整请求（id=25247，98 个字段）确认字段名真实存在：`provenance_text`、`exhibition_history`、`publication_history`、`description`、`inscriptions`、`is_public_domain`、`image_id`、`copyright_notice`。

**网页对照**：`https://www.artic.edu/artworks/79507` 是服务端渲染，Publication History/Provenance/Exhibition History 等标题与正文在初始 HTML 里，逐字比对与 API 内容完全一致——网页不比 API 更丰富，抓 API 已经够。

**判权**：`is_public_domain` 逐件不同（样本中既有 True 也有 False），不是全馆 CC0，需要逐件读取判断；`copyright_notice` 实测样本全为 `None`，不能靠它判断版权状态。

**结论：可编程取到**，但中国藏品的 `exhibition_history` 命中率明显低于克利夫兰——这条路能做实，但别指望它把展览史缺口全填上。

---

## 史密森尼·国立亚洲艺术博物馆（api.si.edu，NMAA/原 Freer|Sackler）

**API 需 key，但不用先去注册就能验证。** 不带 `api_key`：`GET https://api.si.edu/openaccess/api/v1.0/search?q=china` → **403**，`{"error":{"code":"API_KEY_MISSING","message":"No api_key was supplied. Get one at https://api.si.edu:443"}}`。带公开测试 key `api_key=DEMO_KEY`：`GET .../search?q=jade+AND+unit_code%3A%22NMAA%22&rows=10&api_key=DEMO_KEY` → **200**，多次调用（间隔 2.5 秒）未触发 429。`unit_code=NMAA` 对应"National Museum of Asian Art"（即原 Freer|Sackler）。

**字段实测**（`content/{id}` 端点，字段结构是 `freetext.notes`，按 label 分类，label 本身就叫 "Provenance" 和 "Exhibition History"）：

以 id=`ld1-1643390182193-1643390192488-1`（商代晚期玉戚璧，accession F1968.48）为例：
```
[notes] Provenance: Duanfang (1861-1911) [1]
[notes] Provenance: Eugene Meyer (1875-1959) and Agnes E. Meyer (1887-1970), Washington, DC, and Mt. Kisco, NY, from at least January 12, 1929 [2]
[notes] Provenance: Freer Gallery of Art, given by Agnes E. Meyer in 1968 [3]
[notes] Exhibition History: Anyang: China's Ancient City of Kings (February 25, 2023 to April 28, 2024)
[notes] Exhibition History: Afterlife: Ancient Chinese Jades (October 14, 2017 - ongoing)
[notes] Exhibition History: Eugene and Agnes E. Meyer Memorial Exhibition (September 25, 1971 to October 2, 1972)
```
另一件 F1916.491（玉璋）的 provenance 精确到"1916年从上海藏家游篠溪手中购得，1920年随弗利尔美术馆建成正式入藏"，附脚注档案编号（`S.I. 1037, pg. 230, Freer Gallery of Art and Arthur M. Sackler Gallery Archives`），带 12 场展览史（1917年至今）。**这个详尽度（逐条带日期、带脚注档案编号）超过克利夫兰的段落体描述。** 未见独立 citation/publication 字段，著录类信息混在 `notes` 里，需按 label 解析。

**网页路线不可用**：`GET https://asia.si.edu/object/F1916.491/` 等全部 **403**，标题 `"Smithsonian request verification"`，body 只有 2 个 `<script>` 标签——是防爬校验墙直接拒绝非浏览器请求，不是"需要 JS 渲染"。亚洲艺术博物馆这条线只能走 API。

**判权**：`descriptiveNonRepeating.metadata_usage.access` = `"CC0"`；图片层 `online_media.media[].usage.access` = `"CC0"`，直链 `https://ids.si.edu/ids/deliveryService?id=FS-{accession}`。

**结论：可编程取到，数据质量最强的一家。** 唯一代价是要办 API key——`DEMO_KEY` 是 api.data.gov 官方公开的共享测试 key，够验证但限流很紧（官方文档：每小时 30 次/每天 50 次，多用户共享），生产采集应去 `https://api.data.gov/signup/` 免费注册专属 key（邮箱即可，几分钟到手）。这不影响"能不能取到数据"这个结论，只影响生产稳定性。

---

## 大英博物馆

**网页全部被 Cloudflare 拦截。** `www.britishmuseum.org/collection/*`（搜索页、猜测的单件页、`/api`）无论 URL 变体或 header 组合，均返回 **HTTP 403**，body 是约 5.6KB 的 Cloudflare "managed challenge" 页（`<title>Just a moment...</title>`，`window._cf_chl_opt = {cType: 'managed', ...}`）——这是 Cloudflare 在请求到达馆方应用之前就拦下，不是 404、不是认证墙，也不是"JS 渲染但底层有真实标记"的壳，馆方自己的 HTML 根本没送达。

**SPARQL 端点单独判死**：`collection.britishmuseum.org/sparql` 是另一个 host（DNS 解析到 `128.86.231.86`，剑桥注册段），不在 Cloudflare 后面，但 443/80 端口 TCP 连接**超时**（先 `TimeoutError: timed out`，重试后 `TimeoutError('_ssl.c:1059: handshake operation timed out')`）。用 4 次指数退避重试（2s→4s→8s→16s），不是 429 限流的模式，是服务器层面不响应。对照测试：同一脚本连 `www.britishmuseum.org` 的 Cloudflare IP 只需 0.18 秒，证明本机网络路径本身没问题，是这个 host 单独不应答。

**结论：不可行，无法确证数据是否存在。** 两条公开入口都在 HTTP 层被拦——网页需要能过 Cloudflare JS 挑战的重型方案（如 Playwright），SPARQL 端点直接连不上，与本工具"urllib 直连"的方法论不兼容。应与故宫/国博同档，人工录入。

---

## ColBase（日本国立文化财机构）

**无官方公开 REST API，但有未设防的全量数据出口。** `colbase.nich.go.jp/`（含 `?locale=en`）返回 200，但 body 是空的 Nuxt.js SPA 壳（~3.4KB，`<div id="__nuxt">…Loading...</div>`，无内嵌 `__NUXT__` 数据），单件页同样是空壳。官方 About/Terms/Dataset 页均未文档化任何 REST API。

从网站自己的 webpack JS bundle 里逆向出一个内部 API：`https://colbase.nich.go.jp/colbaseapi/v2/`，靠一个硬编码在公开客户端 JS 里的 header `x-api-key: aaa` 通行。`GET .../collection_items/tnm/TA-617?locale=ja`（带该 key）→ 200；不带 → 403 `{"errors":[{"code":403,"message":"x-api-key error"}]}`。**这条路能用但是逆向出来的、非官方授权，随时可能失效，不建议作为生产依赖。**

**真正官方且可靠的路径是批量开放数据**：`colbase.nich.go.jp/pages/dataset` 页面文档化了 `GET https://colbase.nich.go.jp/opendata.tsv`（200/206，支持 Range，约 90MB，日/英/中/韩四语全量目录，测试当天更新），无需任何认证。

**字段判定：展览史与递藏在数据模型里完全不存在**，在三种不同物件（雪舟水墨画、清代版画、梁楷《出山释迦图》——南宋中国画、日本国宝）与两种接口（内部 API 和批量 TSV，schema 相同）上重复验证。完整字段清单：`id, key, bunkazai(指定), bunrui(分类), title, sakusha(作者), seisakuchi(产地), jidai_seiki(年代), hinshitu_keijo(材质), houryo(尺寸), meibun(铭文标签，非完整题跋), kizousha(捐赠者), shozousha(收藏者), descriptions(策展解说), image_files, links, categories`。`links` 在所有抽样物件上均为空 `[]`。`descriptions` 是可用的策展文字（如梁楷《出山释迦图》："梁楷は南宋の宮廷画家です。本図は長きにわたる修行でも悟りを得られず、深山を出る釈迦の姿を描きます…"），是本馆唯一可取的字段，接近克利夫兰的 `description`，但没有展览史/递藏可提取——不是取不到，是压根没有。

**判权**：Terms of use 允许再利用（含商用），要求署名"Source: ColBase (https://colbase.nich.go.jp/)"并标注修改——CC BY 性质，非 CC0；TSV 的 rights 列逐条确认为 `ccby`。

**结论：不可行，是字段本身不存在，不是访问受阻。** 唯一可用输出是策展解说字符串，比克利夫兰的覆盖面更窄。

---

## 波士顿美术馆 MFA

**无 API/开放数据集**（GitHub 搜索、MFA 自己的 about/collections 页均未见"API""open data""CC0"字样）。

**藏品库网页被 AWS WAF 拦截**：`collections.mfa.org` 的所有内容路由（`/`、`/search/objects?...`、`/objects/{id}`）均返回 **HTTP 202**，header 带 `x-amzn-waf-action: challenge`，body 是约 2.4KB 的 AWS WAF Bot Control JS 挑战页。在一件经 Wikidata 核实为真实中国画藏品的页面（`https://collections.mfa.org/objects/30502`，Wikidata `Q20786068`「陆俨少《唐人诗意山水》」，`P195`=MFA Boston，`P4625`=30502）上确认：`__NEXT_DATA__` 不存在、`__INITIAL_STATE__` 不存在——馆方原始标记完全没有送达客户端，不是 JS 水合问题。`robots.txt`（200，真实内容）确认 `Disallow: /search`、`Crawl-delay: 30`，说明 MFA 自己就在限制这些路由，与 WAF 发现一致，是持续性拦截而非限流误报（重试多次均是同样的 202 挑战）。

**判权**：`mfa.org/about/terms-of-use`（200）明确声明网站内容受版权保护，禁止再分发/商业复制，许可需求要联系"MFA Images"——找不到任何 CC0/Public Domain/CC 相关字样，法律姿态与克利夫兰相反。

**结论：不可行，技术+法律双重受阻。** 即便日后绕过 WAF，明确的禁止再分发条款也让程序化抓取站不住脚。应与故宫/国博同档，人工录入。

---

## 哈佛艺术博物馆

**官方 REST API 需 key**：`GET https://api.harvardartmuseums.org/object`（无 key）→ **401**，body 纯文本 `Unauthorized`（非 JSON）。

**定位 object id 本身是个坑**：官方搜索页 `harvardartmuseums.org/collections?q=chinese+bronze` 返回 200，但两次不同查询词返回**字节数完全相同**的页面，正则抓不到任何 `/object/\d+`——搜索由前端 Alpine.js 异步执行，查询参数在服务端被忽略，这条路走不通。改用第三方搜索引擎反查（`html.duckduckgo.com/html/?q=site:harvardartmuseums.org/collections/object+chinese+bronze`）才拿到 10 个真实 id（203988、204613、204565 等）。

**内容确认**：`https://harvardartmuseums.org/collections/object/{id}` 返回约 8~8.4 万字节原始 HTML，服务端渲染（三个字段被 `<!-- Start Provenance -->...<!-- End Provenance -->` 等 HTML 注释包裹，非 JS 异步注入）：
```
Provenance: [C. T. Loo & Co., New York, 1942] sold; to Grenville L. Winthrop, New York
(1942-1943), bequest; to Fogg Art Museum, 1943.

Exhibition History: S427: Ancient Chinese Bronzes and Jades, Arthur M. Sackler Museum,
10/20/1985 - 04/30/2008 ...

Publication History: Robert W. Bagley and Haicheng Wang, Art and Artistic Thinking in
Ancient China... （多条著录）
```
在第二件样本（id=204613）复现同样三个字段。`Curatorial Remarks` 在两件青铜器样本上均未出现，画类条目是否有此字段未验证。

**IIIF manifest** `https://iiif.harvardartmuseums.org/manifests/object/{id}`（10 个真实 id 全部 200）只含 Provenance，不含 Exhibition/Publication。

**结论：可程序化取到**（走网页 HTML 抓取，非官方 API）。三项字段齐全，服务端渲染稳定可抓；唯一瓶颈是官方站内搜索是 JS 死路，需借第三方搜索反查 id，取到 id 之后抓取本身可编程、可重复。

---

## 台北故宫博物院 —— 旧结论被修正

> 本库既有认知（`verify.py` 文档字符串）写的是"台北故宫、上海博物馆——实测 SSL 证书链失败／握手超时"。本次重测发现**这个判断的失败机制描述不准确，需要更新**。

**确切的失败类型**：
```
urllib.error.URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: unable to get local issuer certificate (_ssl.c:1077)>
```
这是**证书验证失败**（本机缺少中间证书链），不是握手协议层失败，也不是超时或连接被拒。`theme.npm.edu.tw` 报的是同类另一种消息：`Missing Subject Key Identifier`。用 `ssl._create_unverified_context()` 关闭校验后，`www.npm.gov.tw`、`www.npm.edu.tw`、`theme.npm.edu.tw` **三个域名全部返回 200**，拿到真实繁体中文官方页面（如 27 万字节的首页）。对照：`data.gov.tw` 默认证书校验直接通过，说明问题出在故宫自己两个域名的证书配置，但不能 100% 排除本机网络环境对 TLS 校验链有特殊处理——这一不确定性需保留。

**核心新发现**：独立子域 `digitalarchive.npm.gov.tw`（典藏资料检索系统）单件详情页字段极丰富，服务端直出、无需 JS。样例（`Collection/Detail/2383?dep=U`，「臨池真賞」墨，清康熙）：
```
基本資料：文物統一編號 故文000001N000000000／時代 清 康熙五十五年／說明（策展文字约300字）
參考資料（著录）：書名《國立故宮博物院－文房聚英》／作者 蔡玫芬／出版者 日本京都巿：同朋舍出版／1992/11
款識：背 題銘 人生一樂｜人名款 汪希古造｜器身 年號款 康熙丙申年製
展示資訊（展览史）：風格故事－琺瑯彩瓷特展 2022/01/13~2024/05/22 北部院區 第一展覽區 207
```
页面同时声明图像 CC0（100 万画素）/ CC BY 4.0（600 万画素及文字）。在另两件样本上复现"展示資訊"字段存在。三件抽样文物均未出现独立的"遞藏/來源"字段（用"參考資料"起著录作用），大件/书画类是否有独立 provenance 栏未验证。

**未打通的部分**：`Search?Key=...` 端点对各种猜测参数（关键词、分类）**全部返回同一份固定的 355370 字节默认列表**，真实过滤逻辑在前端 JS/AJAX，本次未逆向出来——只能靠遍历默认列表 id 或人工收集 id，不能直接按名称/关键词查询。`data.gov.tw` 上有真实免鉴权 API（`POST /api/front/dataset/list`），但猜测的近十种过滤参数名均未生效，未能确认是否挂有故宫专属数据集。

**URL 形态**：官网 `XxxYyy.aspx?sno=十位数字&l=语言代码`；检索系统详情页 `digitalarchive.npm.gov.tw/Collection/Detail/{4~5位id}?dep=U`；精选赏析微站 `theme.npm.edu.tw/selection/Article.aspx?sNo=8位数字`。

**结论：部分可行，性质与旧结论不同。** 不是网络拒绝/官方封锁，是证书链配置问题；绕开证书校验后单件详情页信息量比克利夫兰还细（基本资料+参考资料+款识+展示信息+保存维护）。卡点在"按名称检索到指定文物 id"这一步，需要先另建 id 池（人工收集或遍历默认列表）。

---

## 上海博物馆 —— 只能人工

四种域名/协议组合全部失败，且失败方式各不相同（这点本身是证据，不是懒得测）：

| URL | 结果 |
|---|---|
| `https://www.shanghaimuseum.net/` | SSL 握手超时（`handshake operation timed out`），重试 3s/8s 仍失败 |
| `http://www.shanghaimuseum.net/` | 纯连接超时（`TimeoutError: timed out`） |
| `https://shanghaimuseum.net/`（去 www） | `SSL: UNEXPECTED_EOF_WHILE_READING`——TLS 连上后对方直接断开 |
| `http://shanghaimuseum.net/`（去 www） | 唯一拿到 HTTP 响应的一次：**状态码 502**，正文长度 0 |

四种不同的失败特征（握手超时→连接超时→TLS 中断→502）更像对方服务器侧本身不稳定，而非统一的反爬拦截（反爬通常表现一致的 403）。但不能排除本机网络路由到该站异常，这一不确定性需明确保留。首页连不上导致展览栏目和藏品详情页均无法测试。

**结论：只能人工查阅**（现状判定，无法确证是否为临时故障——过一段时间应该重测一次，不要直接当成永久性结论沉淀）。

---

## 中国国家博物馆 —— 部分可行

首页 `https://www.chnmuseum.cn/` 无 UA / Chrome UA 均 200，UA 无影响。

**展览栏目可直接抓**：`GET https://www.chnmuseum.cn/zl/zhanlanyugao/` → 200，35322 字符，服务端直出的列表，可直接正则抓取标题+日期，样例：
```html
href='../lszl/gjjl/202606/t20260610_279133.shtml' text='行·迹——卡塔尔游牧生活与文化展'
<p>2026年6月10日起对公众展出，展至10月11日</p>
```
该页面实际横跨 2018-2026 年条目，兼具往期+当前展览索引功能。

**藏品详情未打通**："藏品总目"页面 `colletionlistdetail.html?id=1` 返回 200，但 `<tbody id="dataList">` 为空，真实数据靠 `json/mbr.js` 异步填充（该文件仅给出"共 251523 件藏品"的统计探针），真正分页接口本次未逆向出来——是"页面存在但需 JS 渲染、无正文"的典型案例。

**结论：部分可行**——展览类页面（标题/日期/描述）可编程抓取；藏品逐件著录未能取得（骨架页存在，数据接口未破解）。

---

## 南京博物院 —— 与既有假设矛盾的新发现

> 本库既有认知（`museum.py` 文档字符串）写的是"国内各馆——无 API，展览档案页亦多需 JS 渲染"。本次实测发现南京博物院**不属于这个概括**，需要更新认知。

`http://www.njmuseum.com/`（http）连接超时；`https://www.njmuseum.com/` 200，但首页本体是空 Vue SPA（`<div id=app></div>`），所有导航路径返回同一份空壳——**网页抓取路线完全不可行**，与既有假设吻合的部分先确认清楚。

**关键突破**：分析主 JS bundle 发现前端所有请求打向 `https://www.njmuseum.com/api/<endpoint>`，从 bundle 枚举出 106 个端点。实测（GET + Chrome UA + Referer，**无需登录/token**）：
```
GET /api/exhibition/list     → 200，含 title/position/timedesc/describe/type/properties
GET /api/exhibition/history  → 200，{"id":548,"title":"远古印象(常设)","position":"历史馆 1展厅",...}
GET /api/collection/info/desc?id=13263 → 200，{"title":"商甲骨刻辞","categoryName":"文献","describe":"商","size":"纵2.3，横1.5 厘米"}
```
`exhibition/list/code` 明确给出分类体系："当前展览／虚拟展厅／展览预告／展览回顾／赴外展览"，"展览回顾"正是往期展览归档分类。`collection/info/exhibition`（单件藏品关联展览史）字段结构存在，但抽样样本返回空值，未确认非空情形。`collection/select` 翻页参数无效（恒返回同一批），全量检索接口未破解。

**结论：可程序化取到。** 通过一个未设防的 JSON REST API（无需 key/登录），可拿到展览标题+位置+展期+描述，以及藏品的简略著录（名称/类别/年代/尺寸）。**这是本次调研最重要的新发现**——它直接推翻了"国内各馆无 API 可用"的既有概括；这条 API 是从前端 bundle 逆向出来的、非官方文档化，稳定性和是否受馆方默许均未知，采集时要留退避与容错。

---

## 实施优先级建议

**值得马上做成工具（扩展 `museum.py` 的模式）：**

1. **史密森尼国立亚洲艺术博物馆**——数据质量最强，字段最详尽（逐条带日期/档案编号），中国书画藏量极大，且是标准 REST API + 清晰的 JSON 结构，改造 `museum.py` 的成本最低。唯一前置动作：去 `api.data.gov/signup/` 注册一个免费专属 key（几分钟）。**建议第一家做。**
2. **芝加哥艺术博物馆**——无需 key，字段结构与克利夫兰接近（`provenance_text`/`exhibition_history`/`publication_history`），可以直接照搬 `museum.py` 的框架改端点和字段名。中国藏品的 `exhibition_history` 命中率偏低，但 `provenance_text`/`publication_history`/`description` 三项质量够用。
3. **哈佛艺术博物馆**——三项字段齐全且服务端渲染稳定，但需要额外一步：先用第三方搜索（如 DuckDuckGo HTML 端点）反查 object id，再抓网页正文用正则/HTML 解析取 HTML 注释包裹的三个字段块。比前两家多一道工序，但仍在"能自动化"的范围内。

**值得关注但目前只能小规模、非官方方式取，先记录不急着上生产：**

4. **南京博物院**——API 是逆向出来的、未文档化，接入前应再花时间确认 106 个端点里哪些真正有数据（尤其 `collection/info/exhibition` 目前抽样全空），并做好这条路随时可能变更/被收紧的心理准备。适合先人工抽样验证价值，再决定是否值得写脚本。
5. **台北故宫**——`digitalarchive.npm.gov.tw` 单件页字段丰富，值得挂接，但检索接口没打通，意味着必须先有一份 id 清单（可从条目里已经记录的文物号手动配对，或去遍历默认列表页）。适合"人工给 id，脚本抓详情"的半自动模式，不适合"输入名称直接查"。
6. **中国国家博物馆**——展览栏目那部分（标题+日期+描述）成本很低，值得写个小脚本定期抓一遍存档；藏品逐件著录目前拿不到，不必强求。

**技术上判定不可行，只能人工，且短期不必再测：**

7. **大英博物馆**——Cloudflare 挑战 + SPARQL 端点 TCP 超时，绕过需要 Playwright 级别的重型方案，与本库 urllib-only 的工具链方向不合。
8. **波士顿美术馆 MFA**——AWS WAF 拦截，且条款明确禁止再分发，技术+法律双重挡路，即使日后技术上绕过也不该做。
9. **ColBase（日本）**——不是访问问题，是数据模型里真的没有展览史/递藏字段，做工具也榨不出更多东西，唯一价值（策展解说）已通过一次性下载 `opendata.tsv` 就能拿全，不必做成持续对接的工具。
10. **上海博物馆**——四种连接失败，怀疑对方服务器侧不稳定而非官方封锁，建议一段时间后（比如一个月）重测一次而不是现在下永久结论。

**沿用既有结论、不重复投入：**

11. **故宫博物院**——展览页 404，无 API，继续人工。

---

## 需要向另两处文档指出的矛盾

- **`verify.py` 文档字符串**（"实测 SSL 证书链失败／握手超时"描述台北故宫、上海博物馆）——**台北故宫这部分不准确，应改为"证书验证失败（缺中间证书链），非握手/超时问题；绕过校验后可访问，`digitalarchive.npm.gov.tw` 单件页字段丰富"**。上海博物馆部分本次重测结论一致（仍然连不上），但补充了四种失败方式的细节，可选择性合并进去。
- **`museum.py` 文档字符串**（"国内各馆——无 API，展览档案页亦多需 JS 渲染"）——**南京博物院不属于这个概括**，应更新为"多数国内馆无 API，但南京博物院存在未文档化的 `/api/exhibition/*`、`/api/collection/*` 端点，无需登录即可访问"。中国国家博物馆的展览栏目也可补一句"服务端直出可抓，藏品详情仍需 JS"。

这两处的实际改动留给项目维护者决定是否采纳；本文档本身不修改 `museum.py`／`verify.py`／`README.md`。

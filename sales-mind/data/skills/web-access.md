---
name: web-access
version: "2.5.0-salesmind"
description:
  销销 联网能力 skill。所有需要访问互联网的操作（读取网页、搜索本地 Chrome 资源、浏览器交互）都通过此 skill 处理。
  底层实现：HTTP 直连 + Jina Reader + CDP Proxy 浏览器自动化 + Chrome 书签/历史检索。
domain: world
triggers: ["打开网页", "看看这个链接", "截图", "抓取网页", "读取网页", "之前看过的", "搜一下历史", "公司那个系统"]
memory_deps:
  - knowledge_embeddings
care_deps:
  - safety_db.extreme_content_flags
---

# web-access Skill（销销 适配版）

> 原始设计：https://github.com/eze-is/web-access
> 适配原则：保留 eze-is 的浏览哲学和工具选择策略，用 Python 工具层替代 Claude Code 的 bash/curl 调用方式。

## 浏览哲学

**像人一样思考，兼顾高效与适应性。**

执行任务时不会过度依赖固有印象所规划的步骤，而是带着目标进入，边看边判断，遇到阻碍就解决，发现内容不够就深入——全程围绕「我要达成什么」做决策。

**① 拿到请求** — 先明确用户要做什么，定义成功标准：什么算完成了？需要获取什么信息、执行什么操作、达到什么结果？这是后续所有判断的锚点。

**② 选择起点** — 根据任务性质、平台特征、达成条件，选一个最可能直达的方式作为第一步去验证。一次成功当然最好；不成功则在③中调整。

工具选择决策树：
- 用户问时事/新闻/政策/热点，没有给具体 URL → **search_web**（SearXNG 聚合搜索，发现信息来源）
- 用户给了具体 URL，且是文章/博客/公告/文档等静态页面 → **fetch_webpage**（最快、最省 token）
- 同上，但想进一步省 token 且页面是文章结构 → **jina_reader**（转 Markdown）
- 动态渲染、反爬严格、需要登录态、需要交互 → **browse_open**（真实浏览器）
- 用户说"之前看过的那个讲 X 的页面"、"公司那个 XX 系统" → **find_chrome_url**（搜本地 Chrome 书签/历史）
- 需要操作页面（点击、填表、上传、滚动）→ **browse_open** + browse_click/fill/scroll

**③ 过程校验** — 每一步的结果都是证据，不只是成功或失败的二元信号。用结果对照①的成功标准，更新你对目标的判断：路径在推进吗？结果的整体面貌（质量、相关度、量级）是否指向目标可达？发现方向错了立即调整，不在同一个方式上反复重试——fetch_webpage 没拿到内容不等于"还没找对方法"，也可能是"目标需要浏览器才能访问"；API 报错、页面缺少预期元素、重试无改善，都是在告诉你该重新评估方向。

遇到弹窗、登录墙等障碍，判断它是否真的挡住了目标：挡住了就处理，没挡住就绕过——内容可能已在页面 DOM 中，交互只是展示手段。

**④ 完成判断** — 对照定义的任务成功标准，确认任务完成后才停止，但也不要过度操作，不为了"完整"而浪费代价。

## 联网工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| 用户问时事/新闻/政策/热点，没有给 URL | **search_web** | SearXNG 聚合搜索（baidu/bing/sogou/360/zhihu/bilibili），发现信息来源 |
| URL 已知，需要读取页面文字内容 | **fetch_webpage** | 直接 HTTP GET，返回标题+正文。最快最省 token，但不支持 JS 动态内容 |
| URL 已知，想进一步省 token（文章/博客/文档） | **jina_reader** | 第三方服务将网页转 Markdown，限 20 RPM。对非文章结构可能提取错误 |
| 非公开内容，或反爬严格/需要登录态的平台 | **browse_open** | 真实 Chrome 浏览器，天然携带登录态，支持动态页面和交互 |
| 需要操作网页（点击、填表、截图、滚动） | **browse_open** + browse_click/fill/screenshot/scroll | CDP 浏览器自动化 |
| 用户指向"之前看过的页面"或"内部系统" | **find_chrome_url** | 检索本地 Chrome 书签/历史，按关键词/时间窗/访问频度定位 URL |

**fetch_webpage 与 browse_open 的取舍**：
- fetch_webpage 是"程序化方式"：构造请求直接拿 HTML，速度快、精确，但对网站来说不是正常用户行为，可能触发反爬。
- browse_open 是"GUI 交互"：真实浏览器访问，网站不会限制正常 UI 操作，确定性最高，但步骤多、速度慢。
- **优先 fetch_webpage，受阻立即换 browse_open** ——不要在一个方式上反复重试。

## 补充：本地 Chrome 资源

用户指向**本人访问过的页面**（"我之前看的那个讲 X 的文章"、"上次打开过的 XX 面板"）或**组织内部系统**（"我们的 XX 平台"、"公司那个 YY 系统"等公网搜不到的目标）时，用 find_chrome_url 检索本地 Chrome 书签/历史。

## 工具详情

### search_web(query, max_results=5)
- 用途：联网搜索实时信息（新闻、热点、公开资料、政策）
- 底层：本地 SearXNG 容器，聚合 baidu/bing/sogou/360search/zhihu/bilibili
- 特点：零成本、国内源、安全过滤已开启（适合销售场景）
- 局限：百度抓取可能限 IP（低频销售场景基本不会触发）；无结果时诚实告知用户
- 示例：`{"tool": "search_web", "args": {"query": "2026年5月民生新规", "max_results": 5}}`

### fetch_webpage(url)
- 用途：直接 HTTP 获取网页内容
- 适合：文章、博客、公告、文档等以文字为主的静态页面
- 返回：页面标题 + 清理后的正文文字
- 示例：`{"tool": "fetch_webpage", "args": {"url": "https://example.com/article"}}`

### jina_reader(url)
- 用途：将网页转为 Markdown，大幅节省 token
- 适合：文章、博客、文档、PDF 等以正文为核心的页面
- 注意：对数据面板、商品页等非文章结构可能提取到错误区块；限 20 RPM
- 示例：`{"tool": "jina_reader", "args": {"url": "https://example.com/article"}}`

### find_chrome_url(keywords, limit=10, since=None)
- 用途：从本地 Chrome 书签/历史中检索 URL
- 参数：
  - keywords: 搜索关键词，空格分隔（多词 AND 匹配 title + url）
  - limit: 返回条数上限，默认 10
  - since: 时间窗，如 "7d" / "24h" / "2026-04-01"（仅作用于历史）
- 示例：`{"tool": "find_chrome_url", "args": {"keywords": "财务报表", "since": "7d"}}`

### browse_open(url)
- 用途：用真实浏览器打开网页，返回标题和可见文字摘要
- 适合：动态渲染页面、需要登录态、需要交互的场景
- 示例：`{"tool": "browse_open", "args": {"url": "https://www.xiaohongshu.com"}}`

### browse_click(selector)
- 用途：点击页面元素（JS el.click()）
- selector 示例：`#submit`、`.btn-primary`、`a[href='/next']`

### browse_fill(selector, text)
- 用途：在输入框填写文字
- 示例：`{"tool": "browse_fill", "args": {"selector": "#search-input", "text": "Python教程"}}`

### browse_screenshot(filename)
- 用途：截图保存到工作目录 screenshots/ 子目录
- 示例：`{"tool": "browse_screenshot", "args": {"filename": "result.png"}}`

### browse_scroll(direction="down")
- 用途：滚动页面（触发懒加载）
- direction：down / up / top / bottom

### browse_text(selector="body")
- 用途：提取指定区域的文字
- 示例：`{"tool": "browse_text", "args": {"selector": "#article-content"}}`

## 浏览器 CDP 模式要点

- 所有操作在**后台 tab** 中进行，不操作用户已有 tab
- 完成任务后用 browse_close 关闭自己创建的 tab
- Proxy 持续运行，不建议主动停止
- 登录判断核心问题：**目标内容拿到了吗？** 只有确认无法获取且判断登录能解决时，才告知用户去 Chrome 中登录

## 技术事实

- 页面中存在大量已加载但未展示的内容（轮播非当前帧、折叠区块、懒加载占位）。以数据结构为单位思考，eval 可直接触达。
- DOM 中存在选择器不可跨越的边界（Shadow DOM、iframe）。eval 递归遍历可一次穿透所有层级。
- `/scroll` 到底部会触发懒加载。提取图片 URL 前若未滚动，部分图片可能尚未加载。
- 短时间内密集打开大量页面可能触发反爬风控。
- 平台返回的"内容不存在""页面不见了"等提示不一定反映真实状态，也可能是访问方式的问题。
- 站点内交互产生的链接是可靠的：通过用户视角中的可交互单元进行的站点内交互，自然到达的 URL 天然携带平台所需的完整上下文。手动构造的 URL 可能缺失隐式必要参数。

## 信息核实

核实的目标是**一手来源**，而非更多的二手报道。多个媒体引用同一个错误会造成循环印证假象。

| 信息类型 | 一手来源 |
|----------|---------|
| 政策/法规 | 发布机构官网 |
| 企业公告 | 公司官方新闻页 |
| 学术声明 | 原始论文/机构官网 |
| 工具能力/用法 | 官方文档、源码 |

找不到官网时：权威媒体的原创报道（非转载）可作为次级依据，但需向用户说明可能存在转述误差。单一来源时同样声明。

## 站点经验

操作中积累的特定网站经验，按域名存储在 `~/.claude/skills/web-access/references/site-patterns/` 下。

确定目标网站后，如果该目录中有匹配的站点文件，读取对应文件获取先验知识（平台特征、有效模式、已知陷阱）。经验内容标注了发现日期，当作"可能有效的提示"而非保证——如果按经验操作失败，回退通用模式。

## License

MIT · 原始作者：[一泽 Eze](https://github.com/eze-is) · 适配：销销

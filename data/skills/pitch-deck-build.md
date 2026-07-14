---
name: pitch-deck-build
description: |
  把已定稿的投放/营销策略内容，生成「可编辑 · 匹配参考 UI 风格 · 且经 PowerPoint 验证能打开」的比稿 PPTX。
  基于 python-pptx 生成原生可编辑形状（非贴图），侧重投流中台比稿交付：人群定向、预算追踪、关键词策略、甘特图、KPI 表等。
  触发词：做 pptx、出 slides、做 deck、把策略做成 ppt、生成交付稿、deck 排版、套 UI 风格、复刻 ppt 样式、比稿 ppt、投流方案 ppt。
  上游：先用 marketing-plan / account-research 把策略内容定稿；本 skill 不负责策略推导。
  不适用：纯文字策略推导（用 marketing-plan）；只改已有 ppt 的零星文字（直接改即可）；生成图片型海报（用 baoyu-slide-deck / ppt-master）。
---

# 比稿 PPTX 工程化

本 Skill 是「术」——把已定稿的策略内容做成可交付幻灯片。策略的「道」在 `marketing-plan` / `account-research`。

## 0. 前置

- **内容必须先定稿**：叙事框架 + 每页要点 + 关键数据。没定稿先回 `marketing-plan`，别边想边排版。
- 环境：`pip install python-pptx`；有 PowerPoint / Keynote 用于最终打开验证（Mac 可用 Keynote 或 Microsoft 365）。
- 参考脚手架：`/Users/cpp/ppt/a800_deck_ui.py`（海尔 A800 UI 版）——拷来改 palette 和内容最快。

## 1. 销销工作流

1. **接收素材**：用户上传的参考 PPTX / Excel / PDF / Word / 图片，统一用 `read_document` / `read_file` 读取。
2. **确认内容**：若策略未定稿，调用 `marketing-plan` 或 `account-research` 先生成 Markdown 方案。
3. **生成 PPTX**：按本 skill 方法用 python-pptx 写脚本生成 `.pptx`。
4. **验证交付**：在本地用 PowerPoint / Keynote 打开检查；必要时导出 PNG 肉眼看版式。
5. **回传文件**：把生成的 `.pptx` 放到 `data/uploads/<userId>/<threadId>/` 下，返回可下载 URL。

## 2. 工程化原则（可编辑 + palette 驱动）

- **全部用原生形状**（ROUNDED_RECTANGLE / 箭头 / table），**不贴死图**——客户要能直接改字改色挪位。
- **加粗边框用 shape.line**（`line.width` + `line.color`），可编辑；不要画第二个矩形冒充边框。
- 定义 **palette 常量 + helper**（rect/txt/pill/card/arrow/setrun），换风格 = 换 palette，不动结构。
- **中文字体**：除 `run.font.name='微软雅黑'`，必须设东亚字体 `<a:ea>`，否则中文可能不按预期渲染：
  ```python
  def setrun(r, t, sz, col, bold=False):
      r.text = t
      r.font.size = Pt(sz)
      r.font.bold = bold
      r.font.color.rgb = col
      r.font.name = "微软雅黑"
      rPr = r._r.get_or_add_rPr()
      rPr.append(rPr.makeelement(qn('a:ea'), {'typeface': "微软雅黑"}))
  ```
- **字号比屏幕稿放大**：HTML 8–12px 的，PPTX 用 11–20pt+，保证投屏可读。

## 3. 匹配参考 UI

1. **抽设计 token**：主题色解 `ppt/theme/theme*.xml` 的 clrScheme；真实色块用 python-pptx 遍历 `shape.fill` 统计高频色；字体同理。
2. **必须把参考渲染成 PNG 肉眼看**（Mac 可用 Keynote / PowerPoint 导出）——光读 XML 看不出版式、留白、层级。
3. **复刻**：palette + 字体 + 药丸/卡片/箭头/留白节奏。**单色系三阶比红绿紫高级**；"红红绿绿色块"是 AI 味，少用。

## 4. 致命坑（必记）

- ⚠️ **EMU/Inches 双重包裹**：`prs.slide_width` 是 **EMU**；若把它传进内部又 `Inches()` 包一层的 helper（如 `rect(s,0,0,SW,...)` 而 rect 内部 `Inches(SW)`）→ 超大坐标 → **PowerPoint 报「文件损坏」，但 python-pptx 不报错**。
  - 排查：helper 调用传字面数字（如 `13.333`），别把 EMU 变量当 inches 传。
- **python-pptx 能 save ≠ PowerPoint 能打开**。必须本地打开验证。
- 形状**宽/高算成 0 或负**（如 `(A-B)/n` 未夹住）同样会损坏 → 检查公式别出负。

## 5. Mac 验证方式

- 直接用 Microsoft PowerPoint for Mac 或 Keynote 打开生成的 `.pptx`。
- 若用 Keynote，注意部分原生形状/效果可能轻微差异，最终交付前建议用 PowerPoint 再打开一次。
- 无需 Windows COM，但需在本地安装 Office 或 Keynote 做肉眼检查。

## 6. 去 AI 味的对客文案

- 写**好处**不写**目的**：客户要"对我有什么好处"，不是"我们做了什么动作"。
- **具象**不空话：点名「指标 + 动作 + 结果」（如"按 A3 流转率复盘 → 砍低效点位 → CPA3 越投越低"），删"用数据说话""更精准"这种谁都能说的。
- **杀 AI tell**：删"不是 X，是 Y"对仗句；删 赋能/闭环/打造/助力；用**动词收尾**（打赢/长成/投成）；抽象词换具体把握。
- 每个元素**锚回承重墙**（那个原点指标，如高质量 A3）。

## 7. 典型比稿页面清单

| 页 | 内容 | 可视化建议 |
|---|---|---|
| 分品定位 | 品线、传播目的、目的分类、重点指标 | 四列卡片或矩阵 |
| 核心操盘思路 1 | 定向拆解与内容角度嵌合 | 人群 × 内容角度矩阵 |
| 核心操盘思路 2 | 日 × 点位预算 actual vs plan 追踪 | 趋势折线 + 计划/素材粒度 |
| 单品人群定向 | TA 分 3 segments + 人群包组 rotation | 三列卡片 + 标签云 |
| 关键词策略 | 按用户行为编组 + 轮换测试 | 漏斗/分组色块 |
| 执行甘特图 | 按月度分品 + 搜索/信息流权重 | 堆叠柱形图 |
| KPI 预估汇总 | 分品、分目的详情 | 表格 + 关键指标图形化 |

## 8. 交付前自检

- [ ] 全是原生可编辑形状，没贴死图；加粗边用 `line`？
- [ ] PowerPoint / Keynote 实际打开过，没「损坏」？
- [ ] 风格匹配参考（单色系、留白、字号够大）？
- [ ] 每页文案：好处>目的、具象>空话、无 AI tell、锚回主线？
- [ ] 数字标了"示意/待回填"，没把假数据当真？

## 9. 关联

- 上游：`marketing-plan`（定稿策略内容）、`account-research`（客户背景）
- 通用 PPT 生成：`ppt-master`（SVG 路线，适合非编辑型视觉稿）
- 实例脚手架：`/Users/cpp/ppt/a800_deck_ui.py`

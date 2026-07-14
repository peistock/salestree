---
name: pitch-deck-build
description: |
  把已定稿的投放/营销策略内容，生成「可编辑 · 匹配参考 UI 风格 · 且经 PowerPoint 验证能打开」的比稿 PPTX。
  基于 python-pptx 生成原生可编辑形状（非贴图），侧重投流中台比稿交付：人群定向、预算追踪、关键词策略、甘特图、KPI 表等。
  触发词：做 pptx、出 slides、做 deck、把策略做成 ppt、生成交付稿、deck 排版、套 UI 风格、复刻 ppt 样式、比稿 ppt、投流方案 ppt。
  上游：先用 marketing-plan / account-research 把策略内容定稿；本 skill 不负责策略推导。
  不适用：纯文字策略推导（用 marketing-plan）；只改已有 ppt 的零星文字（直接改即可）；生成图片型海报（用 baoyu-slide-deck / ppt-master）。
triggers:
  - 比稿ppt
  - 投流方案ppt
  - 做ppt
  - 做pptx
  - 出slides
  - 做deck
  - deck排版
  - 套ui风格
  - 复刻ppt
  - 生成ppt
  - 交付稿
  - 策略做成ppt
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

### 最小可用脚手架

以下代码可直接跑通两页 PPTX，后续按页复制 `add_slide_*` 函数扩展即可：

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

FONT = "微软雅黑"
BG = RGBColor(0xFB, 0xF8, 0xF4)
BROWN = RGBColor(0x6D, 0x43, 0x29)
ACC = RGBColor(0xB0, 0x70, 0x4A)
WT = RGBColor(0xF7, 0xF7, 0xF7)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

def C(h):
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def setrun(r, t, sz, col, bold=False):
    r.text = t
    r.font.size = Pt(sz)
    r.font.bold = bold
    r.font.color.rgb = col
    r.font.name = FONT
    rPr = r._r.get_or_add_rPr()
    rPr.append(rPr.makeelement(qn('a:ea'), {'typeface': FONT}))

def new_slide(bg=BG):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    r.shadow.inherit = False
    r.fill.solid()
    r.fill.fore_color.rgb = bg
    r.line.fill.background()
    return s

def rect(s, x, y, w, h, fill, line=None, lw=0.75, adj=0.04):
    shp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                             Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    shp.adjustments[0] = adj
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line:
        shp.line.color.rgb = line
        shp.line.width = Pt(lw)
    else:
        shp.line.fill.background()
    return shp

def txt(s, x, y, w, h, lines, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, sp=2):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for m in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'):
        setattr(tf, m, Pt(2))
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(sp)
        setrun(p.add_run(), ln[0], ln[1], ln[2], ln[3] if len(ln) > 3 else False)
    return tb

def pill(s, x, y, w, h, lab, fill=BROWN, tc=WT, sz=12, bold=False):
    shp = rect(s, x, y, w, h, fill)
    tf = shp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    setrun(p.add_run(), lab, sz, tc, bold)

# ---- 封面 ----
s = new_slide()
pill(s, 0.42, 0.34, 1.5, 0.46, "PITCH", fill=C("E5D8CD"), tc=BROWN, sz=14, bold=True)
txt(s, 0.6, 2.2, 12.0, 1.2,
    [("启初 H2 小红书投流方案", 36, BROWN, True)], align=PP_ALIGN.LEFT)
txt(s, 0.6, 3.6, 12.0, 0.8,
    [("高保湿霜 7-8 月 · 多重特润霜 9-12 月", 18, C("83634F"))],
    align=PP_ALIGN.LEFT)

# ---- 内容页 ----
s = new_slide()
pill(s, 0.42, 0.34, 1.5, 0.46, "01", fill=C("E5D8CD"), tc=BROWN, sz=14, bold=True)
txt(s, 2.05, 0.3, 8.5, 0.55, [("分品定位", 27, BROWN, True)],
    anchor=MSO_ANCHOR.MIDDLE)
# 示例：三列卡片
cards = [
    ("高保湿霜", "7-8 月", "心智种草", "互动成本"),
    ("多重特润霜", "9-12 月", "转化收割", "ROI"),
]
x0, y0 = 0.6, 1.5
for i, (title, time, goal, kpi) in enumerate(cards):
    x = x0 + i * 4.2
    rect(s, x, y0, 3.8, 3.5, C("F1E8DF"))
    txt(s, x + 0.2, y0 + 0.2, 3.4, 0.6, [(title, 18, BROWN, True)])
    txt(s, x + 0.2, y0 + 1.0, 3.4, 1.8,
        [(f"周期：{time}", 14, C("323232")),
         (f"目的：{goal}", 14, C("323232")),
         (f"重点指标：{kpi}", 14, C("323232"))],
        sp=8)

prs.save("/tmp/pitch_deck_demo.pptx")
```

> 若需更复杂版式，参考外部实例：`/Users/cpp/ppt/a800_deck_ui.py`。

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

---
name: guizang-html-deck
description: 调用 data/skills/guizang-ppt-skill 生成高质感横向翻页网页 PPT（单 HTML 文件）。提供两种视觉系统：① 电子杂志 × 电子墨水风（衬线、WebGL 流体背景、暖色）；② 瑞士国际主义风（无衬线、网格、高饱和锚点色）。适合分享、演讲、发布会、demo day 等场景。
triggers:
  - guizang
  - 做ppt
  - 做PPT
  - 生成PPT
  - 生成ppt
  - PPT
  - ppt
  - 幻灯片
  - 演示文稿
  - deck
  - presentation
  - slides
  - 网页ppt
  - 网页PPT
  - 网页幻灯片
  - html deck
  - html ppt
  - 演讲稿
  - 发布会
  - keynote
  - 路演
  - 横向翻页
  - 电子杂志
  - 杂志风
  - 瑞士风
  - swiss style
---

# Guizang HTML Deck 集成

## 定位

本 Skill 不是从零写 HTML，而是**驱动 `data/skills/guizang-ppt-skill/` 这套成熟模板**生成单文件横向翻页网页 PPT。它比 python-pptx 原生排版更注重视觉系统：字体对比、网格、主题色、动效、WebGL 背景。

## 何时使用

- 用户觉得 python-pptx 输出“太简单”、“像默认模板”
- 用户要“杂志风”、“瑞士风”、“设计感”、“演讲感”、“发布会风格”
- 用户接受输出为**单文件 HTML**（浏览器打开即可演示，不是 .pptx）

## 不何时使用

- 必须输出可编辑 .pptx（需要另做 python-pptx 模板）
- 内容是大段表格、培训课件、需要多人协作编辑

## 工作流

1. **读取完整 Skill 指导**：
   - `read_file` → `/Users/cpp/salestree/data/skills/guizang-ppt-skill/SKILL.md`
   - 根据用户要的风格，再读 `references/layouts.md` 或 `references/layouts-swiss.md`、`references/themes.md` 或 `references/themes-swiss.md`

2. **需求澄清（必须）**：
   - 风格 A（杂志风）还是 B（瑞士风）？
   - 受众与场景？
   - 时长/页数？
   - 有无原始素材、图片、数据？
   - 主题色偏好？

3. **生成 HTML**：
   - 复制对应 `assets/template.html` 或 `assets/template-swiss.html`
   - 替换 `:root` 主题色、标题、slide 内容
   - 严格使用 template 中预定义的 class，不发明新类名
   - **动画属性 `data-anim` 必须放在 slide 内部的子元素上**（如标题、段落、卡片），**绝不能直接写在 `<section class="slide">` 上**；`revealSlide()` 通过 `slide.querySelectorAll('[data-anim]')` 查找后代元素，不会命中 slide 自身，若写在 section 上会导致整页透明度为 0 而显示空白
   - 产出**单文件 HTML**（CSS/JS 可内联或引用本地 assets）

4. **保存与交付**：
   - 保存到 `data/uploads/<userId>/<threadId>/<uuid>-<safeName>.html`
   - 返回 `{ name, url, mimeType: "text/html", size }`
   - 如需要，可同时生成一张封面 PNG 预览

## 关键文件路径

- Skill 主文档：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/SKILL.md`
- 杂志风模板：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/assets/template.html`
- 瑞士风模板：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/assets/template-swiss.html`
- 杂志风布局：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/references/layouts.md`
- 瑞士风布局：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/references/layouts-swiss.md`
- 杂志风主题色：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/references/themes.md`
- 瑞士风主题色：`/Users/cpp/salestree/data/skills/guizang-ppt-skill/references/themes-swiss.md`

## 输出格式

```json
{
  "name": "名校堂抖音分销账号包投合作方案.html",
  "url": "/data/uploads/sales_001/thread_xxx/uuid-名校堂抖音分销账号包投合作方案.html",
  "mimeType": "text/html",
  "size": 123456
}
```

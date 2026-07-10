# 销销 Skill 索引

从 Lobster AI 迁移的技能库。部分技能需要额外工具支持（标注于下）。

## 通用技能（全局）

| Skill | 说明 | 依赖 |
|-------|------|------|
| account-research | 客户公司一页纸 battlecard：定位、财务、高管、招聘、竞品、口碑 | WebSearch/WebFetch |
| outreach-drafter | 销售触达文案：基于客户动态和联系人角色生成微信/邮件话术 | WebSearch/CRM |
| account-marketing | 客户营销触点研究：品牌、渠道、内容、投放、KOL、社媒 | WebSearch/WebFetch |
| sales-coach | 销售教练：模拟拜访/电话/异议处理，AI 当客户或销售，实时纠偏 | — |
| marketing-plan | 整合营销方案：基于客户背景输出营销策略与执行计划 | WebSearch/WebFetch |
| news-digest | 行业资讯库检索与客户分享：基于 wechat-digest 知识库生成可转发文案 | 向量知识库 |
| 纵横分析法skill | 横纵分析法深度研究（Khazix版），产出PDF报告 | WebSearch/WebFetch |
| web-access | 联网操作（搜索/抓取/CDP浏览器） | Chrome CDP, Node.js 22+ |
| self-improving | 自进化Skill，持续优化自身表现 | — |
| find-skills | 技能发现与检索 | — |
| ppt-master | PPT制作大师 | — |
| news-aggregator-skill | 新闻聚合与摘要 | WebSearch |
| skill-distillation-workflow | Skill蒸馏工作流 | — |

## 创作技能（Baoyu系列）

| Skill | 说明 | 依赖 |
|-------|------|------|
| baoyu-imagine | AI图像生成 | 图像生成API |
| baoyu-image-gen | 图像生成（高级版） | 图像生成API |
| baoyu-article-illustrator | 文章配图生成 | 图像生成API |
| baoyu-slide-deck | 幻灯片制作 | — |
| baoyu-infographic | 信息图制作 | 图像生成API |
| baoyu-comic | 漫画生成 | 图像生成API |
| baoyu-cover-image | 封面图制作 | 图像生成API |
| baoyu-translate | 翻译 | — |
| baoyu-format-markdown | Markdown格式化 | — |
| baoyu-markdown-to-html | Markdown转HTML | — |
| baoyu-url-to-markdown | URL转Markdown | WebFetch |
| baoyu-youtube-transcript | YouTube字幕提取 | — |
| baoyu-xhs-images | 小红书图片生成 | 图像生成API |
| baoyu-post-to-weibo | 发布到微博 | 微博API |
| baoyu-post-to-wechat | 发布到微信公众号 | 公众号API |
| baoyu-post-to-x | 发布到X/Twitter | X API |
| baoyu-compress-image | 图片压缩 | — |
| baoyu-danger-gemini-web | Gemini Web访问 | Gemini API |
| baoyu-danger-x-to-markdown | X/Twitter转Markdown | WebFetch |
| release-skills | 技能发布工作流 | — |

## 使用说明

- Skill按需加载：用户查询匹配触发词时，自动加载对应Skill内容注入Prompt
- 带 WebSearch/WebFetch 依赖的技能在当前环境可直接使用（Claude Code内置）
- 带图像生成API依赖的技能需要配置对应API Key
- 带社交媒体API依赖的技能需要配置对应平台授权

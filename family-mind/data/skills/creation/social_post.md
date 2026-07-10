---
name: social_post
domain: creation
triggers: ["文案", "祝福", "节日", "聚会", "随想", "转发", "家庭群"]
memory_deps:
  - user_profile.writing_patterns
  - user_profile.family_circle
  - user_profile.communication_style
care_deps:
  - body_memory.recent_mood_indicators
---

# Skill：朋友圈/家庭群文案

## 场景
老人想发节日祝福、聚会感言、生活随想，但不知道怎么写得自然得体。

## 分类
1. **节日祝福**：春节、中秋、生日、金婚纪念日
2. **聚会感言**：老同学聚会、家庭聚餐、单位联谊
3. **生活随想**：养花心得、做菜成果、读书感悟
4. **转发配文**：看到好文章想分享，加几句自己的话

## 风格原则
- 像老人自己写的，不要网络套话
- 根据沟通偏好调整：简洁型 30 字内，详细型可以分段
- 家庭群可以温馨一点，朋友圈可以稍微"晒"一点

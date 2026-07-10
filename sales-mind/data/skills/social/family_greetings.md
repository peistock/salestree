---
name: family_greetings
domain: social
triggers: ["祝福", "问候", "生日", "节日", "孙子", "儿子", "家里", "代际"]
memory_deps:
  - user_profile.family_circle
  - user_profile.communication_style
  - user_profile.writing_patterns
care_deps:
  - body_memory.recent_mood_indicators
---

# Skill：家庭问候与代际沟通

## 场景
1. 节日/生日给子女、孙辈发祝福
2. 想了解子女近况，但不知道问什么
3. 子女发了网络用语/新潮事物，老人看不懂

## 工作流
### 祝福类
- 问老人：想对谁说？什么场合？
- 根据家庭关系自动带入称呼
- 生成 2 个版本：温馨版 + 俏皮版（适合年轻辈）

### 沟通类
- 帮老人想话题：孙子最近考试、儿子工作、女儿旅行
- 提醒老人关心的点（根据画像中的家庭关系）

### 翻译类
- 解释网络用语："yyds""绝绝子""emo"
- 用老人熟悉的事物做类比解释

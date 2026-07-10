---
name: photo_caption
domain: creation
triggers: ["配文", "朋友圈", "照片", "写段话", "配文字", "拍照"]
memory_deps:
  - user_profile.writing_patterns
  - user_profile.life_experiences
  - creation_workspace
care_deps:
  - body_memory.today.steps
  - body_memory.recent_mood_indicators
---

# Skill：照片配文

## 场景
老人拍了旅游照片、聚会照片、风景照片，想配一段文字发朋友圈或家庭群。

## 工作流
1. 问老人：这张照片是在哪里拍的？当时发生了什么？
2. 根据老人的描述 + 照片内容，生成 2-3 个配文选项
3. 每个选项分短版（适合朋友圈）和长版（适合家庭群详细分享）
4. 请老人选一个，或再调整

## 风格原则
- 不要网络流行语，要老人自己的语气
- 可以带点人生感悟，但不要强行升华
- 提及地点时，可以带一点当地特色

## 示例
老人：这是在张家界拍的。
配文选项1（短）："张家界云雾里，走一步看一步，风景不等人，人也不等风景。"
配文选项2（短）："七十岁来看山，山还是那座山，看山的人，换了一副老花镜。"

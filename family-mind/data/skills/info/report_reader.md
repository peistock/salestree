---
name: report_reader
domain: knowledge
triggers: ["报告", "解读", "体检", "政策", "公告", "看不懂", "什么意思"]
memory_deps:
  - knowledge_embeddings
  - user_profile.health_notes
care_deps:
  - body_memory.health_notes
  - safety_db.extreme_content_flags
---

# Skill：报告/文件解读

## 场景
老人拿到体检报告、政策通知、物业公告等，看不懂或想快速了解重点。

## 工作流
1. 老人发图片或文字 → 读取内容
2. 分三部分解读：
   - 「一句话总结」：最核心的信息
   - 「详细说明」：各项指标/条款的意思
   - 「建议」：下一步该做什么
3. 涉及健康异常 → 加一句"建议咨询医生确认"
4. 涉及费用/权益 → 提醒老人保留凭证

## 注意
- 不要制造恐慌，异常指标用温和的措辞
- 专业术语要翻译成大白话
- 如果报告内容不全，告诉老人缺了哪部分

"""
技能自安装器（Phase 2.5.3）
- 从 URL 抓取页面提取 skill
- 从用户发送的文本中提取 skill
- 自动补全 YAML frontmatter
- 保存到 data/skills/ 目录
"""
import os
import re
import logging
from pathlib import Path
from typing import Optional, Dict

from mind.llm_client import chat

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(os.getenv("DATA_DIR", "./data")) / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def install_from_url(url: str) -> Dict:
    """从 URL 抓取并安装 skill"""
    try:
        import requests
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        return {"status": "error", "message": f"抓取失败: {e}"}

    return _process_skill_text(text, source=url)


def install_from_text(text: str, skill_name: str = None) -> Dict:
    """从用户发送的文本中提取并安装 skill"""
    return _process_skill_text(text, skill_name=skill_name)


def _process_skill_text(text: str, skill_name: str = None, source: str = "text") -> Dict:
    """处理 skill 文本，提取 frontmatter，保存文件"""
    # 1. 提取 YAML frontmatter
    frontmatter, body = _extract_frontmatter(text)

    # 2. 如果没有 frontmatter，用 LLM 生成
    if not frontmatter:
        frontmatter = _generate_frontmatter(text)
        if not frontmatter:
            return {"status": "error", "message": "无法提取或生成 skill frontmatter"}
        # 组合：新生成的 frontmatter + 原文本
        full_text = f"---\n{frontmatter}\n---\n\n{text}"
    else:
        full_text = text

    # 3. 确定文件名
    filename = _determine_filename(frontmatter, skill_name, text)
    if not filename:
        return {"status": "error", "message": "无法确定 skill 文件名"}

    # 4. 保存
    skill_path = SKILLS_DIR / filename
    # 防覆盖：如果已存在，加版本号
    if skill_path.exists():
        stem = skill_path.stem
        suffix = skill_path.suffix
        counter = 1
        while skill_path.exists():
            skill_path = SKILLS_DIR / f"{stem}_v{counter}{suffix}"
            counter += 1

    try:
        skill_path.write_text(full_text, encoding="utf-8")
        logger.info(f"Skill 已安装: {skill_path} (来源: {source})")
        return {
            "status": "success",
            "message": f"Skill 安装成功 ✅\n文件名: {skill_path.name}\n路径: {skill_path}",
            "filename": skill_path.name,
            "path": str(skill_path),
        }
    except Exception as e:
        return {"status": "error", "message": f"保存失败: {e}"}


def _extract_frontmatter(text: str) -> tuple:
    """提取 YAML frontmatter，返回 (frontmatter_dict_or_str, body)"""
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, text, re.DOTALL)
    if not match:
        return None, text

    yaml_text = match.group(1).strip()
    body = match.group(2).strip()

    # 简单解析 YAML（不需要完整解析器）
    frontmatter = {}
    for line in yaml_text.split('\n'):
        line = line.strip()
        if ':' in line and not line.startswith('#'):
            key, val = line.split(':', 1)
            frontmatter[key.strip()] = val.strip()

    return frontmatter, body


def _generate_frontmatter(text: str) -> Optional[str]:
    """用 LLM 从文本中提取/生成 YAML frontmatter"""
    prompt = f"""请从以下 skill 文本中提取关键信息，生成标准 YAML frontmatter。

必须包含的字段：
- name: skill 的英文标识名（小写，用下划线连接）
- version: "1.0.0"
- description: 一句话描述这个 skill 做什么
- domain: 所属域（creation/knowledge/expression/social/care/admin/world 之一）
- triggers: 触发关键词列表（JSON 数组格式）
- memory_deps: 依赖的记忆类型（可选，JSON 数组）
- care_deps: 关怀依赖（可选，JSON 数组）

输出格式（只输出 YAML，不要解释）：
---
name: xxx
version: "1.0.0"
description: xxx
domain: xxx
triggers: ["关键词1", "关键词2"]
---

文本内容：
{text[:3000]}
"""
    try:
        result = chat(
            system="你是一个 skill 解析助手。严格只输出 YAML frontmatter，不要有任何额外解释。",
            user_prompt=prompt,
            max_tokens=500,
            temperature=0.3,
        )
        # 提取 YAML 部分
        if "---" in result:
            yaml_part = result.split("---")[1] if result.startswith("---") else result
            return yaml_part.strip()
        return result.strip()
    except Exception as e:
        logger.warning(f"LLM 生成 frontmatter 失败: {e}")
        return None


def _determine_filename(frontmatter, skill_name: str = None, text: str = "") -> Optional[str]:
    """确定 skill 文件名"""
    if skill_name:
        name = skill_name.lower().replace(" ", "_").replace("-", "_")
        return f"{name}.md"

    if isinstance(frontmatter, dict) and frontmatter.get("name"):
        name = frontmatter["name"].lower().replace(" ", "_").replace("-", "_")
        return f"{name}.md"

    # 从文本第一行提取标题
    first_line = text.strip().split('\n')[0]
    if first_line.startswith('# '):
        name = first_line[2:].strip().lower().replace(" ", "_")
        return f"{name}.md"

    # 兜底
    return None


def list_installed_skills() -> str:
    """列出已安装的 skills"""
    if not SKILLS_DIR.exists():
        return "（skills 目录不存在）"

    files = [f.name for f in SKILLS_DIR.rglob("*.md")]
    if not files:
        return "（暂无已安装 skill）"
    return "已安装 skills：\n" + "\n".join(f"- {f}" for f in sorted(files))

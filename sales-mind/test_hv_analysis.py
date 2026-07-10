"""测试 hv-analysis 技能分析小米公司（完整流程 + 调试）"""
import os
import sys
import logging

from dotenv import load_dotenv
load_dotenv()

# 设置日志级别为 DEBUG
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

from mind.agent import FamilyAgent

agent = FamilyAgent(user_id="ChenPei", user_name="测试销售")
result = agent.run("用 hv analysis 技能分析一下小米公司")
print("\n" + "="*60)
print("最终结果:")
print("="*60)
print(repr(result["reply"]))
print("="*60)

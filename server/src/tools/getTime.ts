import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";

export const getTimeTool: AgentTool = {
  name: "get_time",
  label: "Get Time",
  description: "返回当前日期和时间",
  parameters: Type.Object({}),
  async execute() {
    return {
      content: [{ type: "text" as const, text: new Date().toLocaleString("zh-CN") }],
      details: {},
    };
  },
};

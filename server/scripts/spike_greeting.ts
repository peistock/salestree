import "dotenv/config";

import { Agent } from "@earendil-works/pi-agent-core";
import { createLlmModel } from "../src/llm/provider.ts";
import { createTools } from "../src/tools/Toolkit.ts";

const { model, streamFn } = createLlmModel();
const tools = createTools("sales_001");

const agent = new Agent({
  initialState: {
    systemPrompt: "你是销销，一个销售助手。用中文简洁回答。",
    model,
    thinkingLevel: "off",
    tools,
    messages: [],
  },
  streamFn,
});

const chunks: string[] = [];
agent.subscribe((event) => {
  console.log(`[event] ${event.type}`);
  if (event.type === "message_update") {
    const e = event.assistantMessageEvent;
    if (e?.type === "text_delta") chunks.push(e.delta);
    if (e?.type === "thinking_delta") process.stdout.write(`[think]${e.delta}`);
  }
  if (event.type === "agent_end") {
    console.log("\n[final]", chunks.join(""));
  }
});

await agent.prompt("你好，介绍一下你自己");

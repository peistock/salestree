import "dotenv/config";

import { Agent } from "@earendil-works/pi-agent-core";
import { Type, createModels, createProvider, envApiKeyAuth } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import type { Model } from "@earendil-works/pi-ai";

function createLmStudioProvider() {
  const baseUrl = process.env.LLM_BASE_URL?.replace(/\/$/, "");
  const modelId = process.env.MODEL_DAILY ?? "qwen/qwen3.6-27b";

  const provider = createProvider({
    id: "lmstudio",
    name: "LM Studio",
    baseUrl,
    auth: { apiKey: envApiKeyAuth("LM Studio API key", ["LLM_API_KEY"]) },
    models: [
      {
        id: modelId,
        name: modelId,
        api: "openai-completions",
        provider: "lmstudio",
        baseUrl: baseUrl ?? "",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32768,
        maxTokens: 8192,
      } satisfies Model<"openai-completions">,
    ],
    api: openAICompletionsApi(),
  });

  const models = createModels();
  models.setProvider(provider);
  const model = models.getModel("lmstudio", modelId);
  if (!model) throw new Error(`Model ${modelId} not found`);
  return { models, model };
}

const { models, model } = createLmStudioProvider();

const getTimeTool = {
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

const agent = new Agent({
  initialState: {
    systemPrompt: "你是销销，一个销售助手。需要获取时间时请使用 get_time 工具。",
    model,
    thinkingLevel: "off",
    tools: [getTimeTool],
    messages: [],
  },
  streamFn: (model, context, options) => models.streamSimple(model, context, options as any),
});

const chunks: string[] = [];
agent.subscribe((event) => {
  if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
    chunks.push(event.assistantMessageEvent.delta);
  }
  if (event.type === "tool_execution_start") {
    console.log(`[tool start] ${event.toolName}`);
  }
  if (event.type === "tool_execution_end") {
    console.log(`[tool end] ${event.toolName} -> ${event.result?.content?.[0]?.text ?? ""}`);
  }
  if (event.type === "agent_end") {
    console.log("\n[final reply]", chunks.join(""));
  }
});

await agent.prompt("现在几点？");

import { createModels, createProvider, envApiKeyAuth, hasApi } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import type {
  Api,
  AssistantMessage,
  AssistantMessageEvent,
  AssistantMessageEventStream,
  Context,
  Model,
  Models,
  SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import OpenAI from "openai";
import type { ChatCompletionMessageParam, ChatCompletionTool } from "openai/resources/chat/completions";
import { config } from "../config.ts";

const USE_NON_STREAMING_PROVIDERS = new Set(["agnes", "kimi"]);

function pickProviderId(baseUrl: string | undefined): string {
  if (baseUrl?.includes("agnes-ai")) return "agnes";
  if (baseUrl?.includes("kimi")) return "kimi";
  return "custom";
}

function toOpenAIMessages(context: Context): ChatCompletionMessageParam[] {
  const out: ChatCompletionMessageParam[] = [];
  if (context.systemPrompt?.trim()) {
    out.push({ role: "system", content: context.systemPrompt });
  }
  for (const msg of context.messages) {
    if (msg.role === "user") {
      if (typeof msg.content === "string") {
        out.push({ role: "user", content: msg.content });
      } else {
        out.push({
          role: "user",
          content: msg.content.map((part) =>
            part.type === "text"
              ? { type: "text" as const, text: part.text }
              : { type: "image_url" as const, image_url: { url: `data:${part.mimeType};base64,${part.data}` } }
          ),
        });
      }
    } else if (msg.role === "assistant") {
      const text = msg.content
        .filter((b): b is { type: "text"; text: string } => b.type === "text")
        .map((b) => b.text)
        .join("");
      const toolCalls = msg.content
        .filter((b): b is { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown> } => b.type === "toolCall")
        .map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: { name: tc.name, arguments: JSON.stringify(tc.arguments) },
        }));
      const assistantMsg: ChatCompletionMessageParam = {
        role: "assistant",
        content: text || null,
      };
      if (toolCalls.length > 0) {
        (assistantMsg as any).tool_calls = toolCalls;
      }
      out.push(assistantMsg);
    } else if (msg.role === "toolResult") {
      const text = msg.content
        .filter((b): b is { type: "text"; text: string } => b.type === "text")
        .map((b) => b.text)
        .join("\n");
      out.push({ role: "tool", content: text || "(no output)", tool_call_id: msg.toolCallId });
    }
  }
  return out;
}

function toOpenAITools(context: Context): ChatCompletionTool[] | undefined {
  if (!context.tools || context.tools.length === 0) return undefined;
  return context.tools.map((tool) => ({
    type: "function" as const,
    function: {
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters as any,
    },
  }));
}

function buildFinalMessage(response: OpenAI.Chat.Completions.ChatCompletion, model: Model<Api>): AssistantMessage {
  const choice = response.choices[0];
  const rawFinish = choice?.finish_reason ?? "stop";
  const stopReason = rawFinish === "tool_calls" ? "toolUse" : (rawFinish as AssistantMessage["stopReason"]);
  const text = choice?.message?.content ?? "";
  const content: AssistantMessage["content"] = [];
  if (text) content.push({ type: "text", text });
  for (const tc of choice?.message?.tool_calls ?? []) {
    if (tc.type !== "function") continue;
    let args: Record<string, unknown> = {};
    try {
      args = JSON.parse(tc.function.arguments);
    } catch {
      args = {};
    }
    content.push({ type: "toolCall", id: tc.id, name: tc.function.name, arguments: args });
  }
  const usage = response.usage ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  return {
    role: "assistant",
    content,
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: {
      input: usage.prompt_tokens,
      output: usage.completion_tokens,
      totalTokens: usage.total_tokens,
      cacheRead: 0,
      cacheWrite: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason,
    timestamp: Date.now(),
  };
}

function nonStreamingStreamFn(
  model: Model<Api>,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  const client = new OpenAI({
    apiKey: options?.apiKey || config.llm.apiKey,
    baseURL: model.baseUrl,
    dangerouslyAllowBrowser: true,
    maxRetries: 0,
  });

  const messages = toOpenAIMessages(context);
  const tools = toOpenAITools(context);

  const run = async (): Promise<AssistantMessage> => {
    const requestOptions: OpenAI.RequestOptions = {
      ...(options?.timeoutMs !== undefined ? { timeout: options.timeoutMs } : {}),
    };
    const response = await client.chat.completions.create(
      {
        model: model.id,
        messages,
        tools,
        tool_choice: tools ? "auto" : undefined,
        stream: false,
        temperature: model.provider === "kimi" ? 1 : 0.3,
        max_tokens: model.maxTokens ?? 4096,
      } as any,
      requestOptions,
    );
    return buildFinalMessage(response, model);
  };

  let finalMessagePromise: Promise<AssistantMessage> | undefined;
  const events: AssistantMessageEvent[] = [];

  const stream = {
    [Symbol.asyncIterator]: async function* () {
      if (!finalMessagePromise) finalMessagePromise = run();
      const finalMessage = await finalMessagePromise;
      if (events.length === 0) {
        events.push({ type: "start", partial: finalMessage });
        for (let i = 0; i < finalMessage.content.length; i++) {
          const block = finalMessage.content[i];
          if (block.type === "text") {
            events.push({ type: "text_start", contentIndex: i, partial: finalMessage });
            events.push({ type: "text_end", contentIndex: i, content: block.text, partial: finalMessage });
          } else if (block.type === "toolCall") {
            events.push({ type: "toolcall_start", contentIndex: i, partial: finalMessage });
            events.push({ type: "toolcall_end", contentIndex: i, toolCall: block, partial: finalMessage });
          }
        }
        const reason = finalMessage.stopReason === "toolUse" ? "toolUse" : "stop";
        events.push({ type: "done", reason, message: finalMessage });
      }
      for (const e of events) yield e;
    },
    result: async () => {
      if (!finalMessagePromise) finalMessagePromise = run();
      return finalMessagePromise;
    },
  };

  return stream as unknown as AssistantMessageEventStream;
}

export function createLlmModel(): { models: Models; model: Model<"openai-completions">; streamFn: StreamFn } {
  const baseUrl = config.llm.baseUrl;
  const modelId = config.llm.modelDaily;
  const providerId = pickProviderId(baseUrl);

  const provider = createProvider({
    id: providerId,
    name: providerId,
    baseUrl,
    auth: { apiKey: envApiKeyAuth("LLM API key", ["LLM_API_KEY"]) },
    models: [
      {
        id: modelId,
        name: modelId,
        api: "openai-completions",
        provider: providerId,
        baseUrl: baseUrl ?? "",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32768,
        maxTokens: 4096,
      } satisfies Model<"openai-completions">,
    ],
    api: openAICompletionsApi(),
  });

  const models = createModels();
  models.setProvider(provider);
  const model = models.getModel(providerId, modelId);
  if (!model) throw new Error(`Model ${modelId} not found`);
  if (!hasApi(model, "openai-completions")) throw new Error(`Model ${modelId} is not openai-completions`);

  const streamFn: StreamFn = (m, ctx, opts) => {
    if (USE_NON_STREAMING_PROVIDERS.has(m.provider)) {
      return nonStreamingStreamFn(m, ctx, opts);
    }
    return models.streamSimple(m, ctx, opts as any) as unknown as AssistantMessageEventStream;
  };

  return { models, model: model as Model<"openai-completions">, streamFn };
}

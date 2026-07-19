import {
  createAssistantMessageEventStream,
  createModels,
  createProvider,
  envApiKeyAuth,
  hasApi,
} from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import type {
  Api,
  AssistantMessage,
  AssistantMessageEvent,
  AssistantMessageEventStream,
  Context,
  Model,
  Models,
  Provider,
  SimpleStreamOptions,
  ToolCall,
} from "@earendil-works/pi-ai";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import OpenAI from "openai";
import type { ChatCompletionMessageParam, ChatCompletionTool } from "openai/resources/chat/completions";
import { config } from "../config.ts";

const USE_NON_STREAMING_PROVIDERS = new Set(["agnes"]);

interface ProviderConfig {
  id: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  modelId: string;
}

function pickProviderId(baseUrl: string): string {
  if (baseUrl.includes("agnes-ai")) return "agnes";
  if (baseUrl.includes("kimi")) return "kimi";
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

function openAiStreamingStreamFn(
  model: Model<Api>,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  const output = createAssistantMessageEventStream();

  const run = async () => {
    const client = new OpenAI({
      apiKey: options?.apiKey || config.llm.apiKey,
      baseURL: model.baseUrl,
      dangerouslyAllowBrowser: true,
      maxRetries: 0,
    });

    const messages = toOpenAIMessages(context);
    const tools = toOpenAITools(context);

    const requestOptions: OpenAI.RequestOptions = {
      ...(options?.timeoutMs !== undefined ? { timeout: options.timeoutMs } : {}),
      ...(options?.signal ? { signal: options.signal } : {}),
    };

    try {
      const response = await client.chat.completions.create(
        {
          model: model.id,
          messages,
          tools,
          tool_choice: tools ? "auto" : undefined,
          stream: true,
          temperature: model.provider === "kimi" ? 1 : 0.3,
          max_tokens: model.maxTokens ?? 4096,
          stream_options: { include_usage: true },
        } as any,
        requestOptions,
      );

      const content: AssistantMessage["content"] = [];
      let contentIndex = 0;
      let textStarted = false;
      let textContentIndex = -1;
      let textAccumulated = "";
      let thinkingStarted = false;
      let thinkingContentIndex = -1;
      let thinkingAccumulated = "";
      const toolCallStates = new Map<number, { id: string; name: string; arguments: string }>();
      const toolCallStarted = new Set<number>();
      const toolCallContentIndices = new Map<number, number>();
      let inputTokens = 0;
      let outputTokens = 0;
      let finishReason: string | null | undefined = null;

      const buildPartial = (): AssistantMessage => ({
        role: "assistant",
        content,
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: {
          input: inputTokens,
          output: outputTokens,
          totalTokens: inputTokens + outputTokens,
          cacheRead: 0,
          cacheWrite: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: finishReason === "tool_calls" ? "toolUse" : finishReason === "length" ? "length" : "stop",
        timestamp: Date.now(),
      });

      output.push({ type: "start", partial: buildPartial() });

      for await (const chunk of response as unknown as AsyncIterable<OpenAI.Chat.Completions.ChatCompletionChunk>) {
        // OpenAI 流式接口常把 usage 放在 choices 为空的最终 chunk 里，必须先捕获
        if (chunk.usage) {
          inputTokens = chunk.usage.prompt_tokens ?? inputTokens;
          outputTokens = chunk.usage.completion_tokens ?? outputTokens;
        }

        const choice = chunk.choices[0];
        if (!choice) continue;

        const delta = choice.delta as {
          content?: string | null;
          reasoning_content?: string | null;
          tool_calls?: Array<{
            index?: number;
            id?: string;
            type?: string;
            function?: { name?: string; arguments?: string };
          }>;
        };

        if (choice.finish_reason) {
          finishReason = choice.finish_reason;
        }

        if (delta.content) {
          if (!textStarted) {
            textStarted = true;
            textContentIndex = contentIndex++;
            content[textContentIndex] = { type: "text", text: "" };
            output.push({ type: "text_start", contentIndex: textContentIndex, partial: buildPartial() });
          }
          const textDelta = delta.content;
          textAccumulated += textDelta;
          (content[textContentIndex] as { text: string }).text = textAccumulated;
          output.push({ type: "text_delta", contentIndex: textContentIndex, delta: textDelta, partial: buildPartial() });
        }

        if (delta.reasoning_content) {
          if (!thinkingStarted) {
            thinkingStarted = true;
            thinkingContentIndex = contentIndex++;
            content[thinkingContentIndex] = { type: "thinking", thinking: "" };
            output.push({ type: "thinking_start", contentIndex: thinkingContentIndex, partial: buildPartial() });
          }
          const thinkingDelta = delta.reasoning_content;
          thinkingAccumulated += thinkingDelta;
          (content[thinkingContentIndex] as { thinking: string }).thinking = thinkingAccumulated;
          output.push({
            type: "thinking_delta",
            contentIndex: thinkingContentIndex,
            delta: thinkingDelta,
            partial: buildPartial(),
          });
        }

        if (delta.tool_calls && delta.tool_calls.length > 0) {
          for (const tcDelta of delta.tool_calls) {
            const idx = tcDelta.index ?? 0;
            if (!toolCallStates.has(idx)) {
              toolCallStates.set(idx, {
                id: tcDelta.id || "",
                name: tcDelta.function?.name || "",
                arguments: tcDelta.function?.arguments || "",
              });
            } else {
              const state = toolCallStates.get(idx)!;
              if (tcDelta.id) state.id = tcDelta.id;
              if (tcDelta.function?.name) state.name += tcDelta.function.name;
              if (tcDelta.function?.arguments) state.arguments += tcDelta.function.arguments;
            }

            if (!toolCallStarted.has(idx)) {
              toolCallStarted.add(idx);
              const ci = contentIndex++;
              toolCallContentIndices.set(idx, ci);
              const state = toolCallStates.get(idx)!;
              content[ci] = { type: "toolCall", id: state.id, name: state.name, arguments: {} };
              output.push({ type: "toolcall_start", contentIndex: ci, partial: buildPartial() });
            } else {
              const ci = toolCallContentIndices.get(idx)!;
              const state = toolCallStates.get(idx)!;
              (content[ci] as ToolCall).name = state.name;
              output.push({ type: "toolcall_delta", contentIndex: ci, delta: state.arguments, partial: buildPartial() });
            }
          }
        }
      }

      if (textStarted) {
        output.push({ type: "text_end", contentIndex: textContentIndex, content: textAccumulated, partial: buildPartial() });
      }
      if (thinkingStarted) {
        output.push({
          type: "thinking_end",
          contentIndex: thinkingContentIndex,
          content: thinkingAccumulated,
          partial: buildPartial(),
        });
      }

      for (const [idx, ci] of toolCallContentIndices) {
        const state = toolCallStates.get(idx)!;
        let args: Record<string, unknown> = {};
        try {
          args = JSON.parse(state.arguments);
        } catch {
          args = {};
        }
        const toolCall: ToolCall = { type: "toolCall", id: state.id, name: state.name, arguments: args };
        content[ci] = toolCall;
        output.push({ type: "toolcall_end", contentIndex: ci, toolCall, partial: buildPartial() });
      }

      const finalMessage = buildPartial();
      output.push({ type: "done", reason: finalMessage.stopReason as "stop" | "length" | "toolUse", message: finalMessage });
      output.end(finalMessage);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[llm] streaming error:", message);
      const errorMessage: AssistantMessage = {
        role: "assistant",
        content: [{ type: "text", text: message }],
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: { input: 0, output: 0, totalTokens: 0, cacheRead: 0, cacheWrite: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        stopReason: "error",
        errorMessage: message,
        timestamp: Date.now(),
      };
      output.push({ type: "error", reason: "error", error: errorMessage });
      output.end(errorMessage);
    }
  };

  run();
  return output;
}

function buildProviderConfigs(): ProviderConfig[] {
  const primary: ProviderConfig = {
    id: "primary",
    name: "primary",
    baseUrl: config.llm.baseUrl ?? "http://127.0.0.1:1234/v1",
    apiKey: config.llm.apiKey,
    modelId: config.llm.modelDaily,
  };
  const configs: ProviderConfig[] = [primary];

  const n = Math.max(config.llm.fallbackUrls.length, config.llm.fallbackKeys.length);
  for (let i = 0; i < n; i++) {
    const url = config.llm.fallbackUrls[i];
    const key = config.llm.fallbackKeys[i];
    if (!url || !key) continue;
    configs.push({
      id: `fallback_${i + 1}`,
      name: config.llm.fallbackNames[i] || `fallback_${i + 1}`,
      baseUrl: url,
      apiKey: key,
      modelId: config.llm.fallbackModels[i] || primary.modelId,
    });
  }

  return configs;
}

function createProviderFromConfig(cfg: ProviderConfig): Provider<Api> {
  const providerId = pickProviderId(cfg.baseUrl);
  return createProvider({
    id: cfg.id,
    name: cfg.name,
    baseUrl: cfg.baseUrl,
    auth: { apiKey: envApiKeyAuth("LLM API key", ["LLM_API_KEY"]) },
    models: [
      {
        id: cfg.modelId,
        name: cfg.modelId,
        api: "openai-completions",
        provider: providerId,
        baseUrl: cfg.baseUrl,
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32768,
        maxTokens: 16384,
      } satisfies Model<"openai-completions">,
    ],
    api: openAICompletionsApi(),
  });
}

function streamOnce(
  models: Models,
  model: Model<Api>,
  context: Context,
  options: SimpleStreamOptions | undefined,
  apiKey: string,
): AssistantMessageEventStream {
  const opts: SimpleStreamOptions = { ...options, apiKey };
  if (USE_NON_STREAMING_PROVIDERS.has(model.provider)) {
    return nonStreamingStreamFn(model, context, opts);
  }
  return openAiStreamingStreamFn(model, context, opts);
}

function createFailoverStreamFn(
  models: Models,
  modelOrder: Model<Api>[],
  apiKeys: Map<string, string>,
): StreamFn {
  const modelKey = (m: Model<Api>) => `${m.provider}:${m.id}`;

  return async (model, context, options) => {
    const startIndex = Math.max(
      0,
      modelOrder.findIndex((m) => m.provider === model.provider && m.id === model.id)
    );

    const output = createAssistantMessageEventStream();

    const tryProvider = async (index: number) => {
      if (index >= modelOrder.length) {
        const errorMessage: AssistantMessage = {
          role: "assistant",
          content: [{ type: "text", text: "所有 LLM provider 均已耗尽" }],
          api: model.api,
          provider: model.provider,
          model: model.id,
          usage: { input: 0, output: 0, totalTokens: 0, cacheRead: 0, cacheWrite: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
          stopReason: "error",
          timestamp: Date.now(),
        };
        output.push({
          type: "error",
          reason: "error",
          error: errorMessage,
        });
        output.end(errorMessage);
        return;
      }

      const candidate = modelOrder[index];
      const apiKey = apiKeys.get(modelKey(candidate)) || config.llm.apiKey;
      const buffer: AssistantMessageEvent[] = [];
      try {
        const stream = streamOnce(models, candidate, context, options, apiKey);
        let failed = false;
        for await (const event of stream) {
          buffer.push(event);
          if (event.type === "error") {
            failed = true;
          }
        }
        if (failed) {
          console.warn(`[llm] provider=${candidate.provider}/${candidate.id} 流式失败，尝试下一个 provider`);
          await tryProvider(index + 1);
        } else {
          for (const event of buffer) output.push(event);
          output.end();
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.warn(`[llm] provider=${candidate.provider}/${candidate.id} 异常: ${message}`);
        await tryProvider(index + 1);
      }
    };

    tryProvider(startIndex).catch((err) => {
      console.error("[llm] failover 异常:", err);
      output.end();
    });

    return output;
  };
}

export function createLlmModel(): { models: Models; model: Model<"openai-completions">; streamFn: StreamFn } {
  const providerConfigs = buildProviderConfigs();
  const models = createModels();
  const modelOrder: Model<Api>[] = [];
  const apiKeys = new Map<string, string>();
  const modelKey = (m: Model<Api>) => `${m.provider}:${m.id}`;

  for (const cfg of providerConfigs) {
    const provider = createProviderFromConfig(cfg);
    models.setProvider(provider);
    const m = models.getModel(cfg.id, cfg.modelId);
    if (!m) throw new Error(`Model ${cfg.modelId} not found for provider ${cfg.id}`);
    if (!hasApi(m, "openai-completions")) throw new Error(`Model ${cfg.modelId} is not openai-completions`);
    apiKeys.set(modelKey(m), cfg.apiKey);
    modelOrder.push(m);
  }

  if (modelOrder.length === 0) {
    throw new Error("No LLM providers configured");
  }

  const primaryModel = modelOrder[0] as Model<"openai-completions">;
  const streamFn = createFailoverStreamFn(models, modelOrder, apiKeys);

  return { models, model: primaryModel, streamFn };
}

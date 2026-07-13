import fs from "node:fs";
import path from "node:path";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const readFeishuMessagesSchema = Type.Object({
  account: Type.Optional(Type.String({ description: "客户名称，例如'环平保险'", default: "环平保险" })),
  max_messages: Type.Optional(Type.Number({ description: "最多返回几条最近消息", default: 100 })),
});

interface FeishuMessage {
  chat_name?: string;
  sender?: { name?: string; id?: string };
  create_time?: string;
  content?: string;
}

interface MessagesFile {
  data?: { messages?: FeishuMessage[] };
}

function loadMessages(account: string): MessagesFile | null {
  const baseDir = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    "../../",
    "data/projects",
  );
  if (!fs.existsSync(baseDir)) return null;

  // 1. 优先精确匹配
  const safeName = account.replace(/[^a-zA-Z0-9一-龥]/g, "_");
  const exactPath = path.join(baseDir, `${safeName}_messages.json`);
  if (fs.existsSync(exactPath)) {
    try {
      const raw = fs.readFileSync(exactPath, "utf-8");
      return JSON.parse(raw) as MessagesFile;
    } catch (err) {
      console.error("[readFeishuMessages] 读取飞书消息失败:", err);
      return null;
    }
  }

  // 2. fallback：找任意 *_messages.json
  const files = fs.readdirSync(baseDir).filter((f) => f.endsWith("_messages.json"));
  if (files.length === 0) return null;
  const fallbackPath = path.join(baseDir, files[0]);
  try {
    const raw = fs.readFileSync(fallbackPath, "utf-8");
    return JSON.parse(raw) as MessagesFile;
  } catch (err) {
    console.error("[readFeishuMessages] 读取飞书消息失败:", err);
    return null;
  }
}

export const readFeishuMessagesTool: AgentTool<typeof readFeishuMessagesSchema> = {
  name: "read_feishu_messages",
  label: "Read Feishu Messages",
  description:
    "读取指定客户的飞书群原始聊天记录。当用户询问客户群/内部群细节、追问某条信号背景、或需要基于原始聊天内容分析时应调用。",
  parameters: readFeishuMessagesSchema,
  async execute(_toolCallId, params: Static<typeof readFeishuMessagesSchema>) {
    const account = params.account?.trim() || "环平保险";
    const maxMessages = params.max_messages ?? 100;
    const data = loadMessages(account);
    if (!data) {
      return {
        content: [{ type: "text" as const, text: `暂无 ${account} 的飞书群消息记录。` }],
        details: {},
      };
    }

    const messages = (data.data?.messages || []).slice(0, maxMessages);
    if (messages.length === 0) {
      return {
        content: [{ type: "text" as const, text: `${account} 的飞书群消息为空。` }],
        details: {},
      };
    }

    const lines: string[] = [];
    lines.push(`【${account}】飞书群最近 ${messages.length} 条消息`);
    for (const m of messages) {
      const chat = m.chat_name || "未知群";
      const sender = m.sender?.name || m.sender?.id || "未知发送人";
      const time = m.create_time || "";
      const content = m.content || "";
      lines.push(`\n[${chat}] ${time} ${sender}:\n${content}`);
    }

    return {
      content: [{ type: "text" as const, text: lines.join("\n") }],
      details: { messageCount: messages.length },
    };
  },
};

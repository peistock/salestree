import fs from "node:fs";
import path from "node:path";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const readWechatMessagesSchema = Type.Object({
  account: Type.Optional(Type.String({ description: "客户/项目名称，例如'示例微信客户'", default: "示例微信客户" })),
  max_messages: Type.Optional(Type.Number({ description: "最多返回几条最近消息", default: 100 })),
});

interface WechatMessage {
  chat_name?: string;
  sender_name?: string;
  sender?: { name?: string; id?: string } | string;
  create_time?: string;
  createTime?: string;
  content?: string;
}

interface MessagesFile {
  data?: { messages?: WechatMessage[] };
}

function loadMessages(account: string): MessagesFile | null {
  const baseDir = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    "../../..",
    "data/projects",
  );
  if (!fs.existsSync(baseDir)) return null;

  const safeName = account.replace(/[^a-zA-Z0-9一-龥]/g, "_");
  const exactPath = path.join(baseDir, `${safeName}_messages.json`);
  if (fs.existsSync(exactPath)) {
    try {
      const raw = fs.readFileSync(exactPath, "utf-8");
      return JSON.parse(raw) as MessagesFile;
    } catch (err) {
      console.error("[readWechatMessages] 读取微信消息失败:", err);
      return null;
    }
  }

  const files = fs.readdirSync(baseDir).filter((f) => f.endsWith("_messages.json"));
  if (files.length === 0) return null;
  const fallbackPath = path.join(baseDir, files[0]);
  try {
    const raw = fs.readFileSync(fallbackPath, "utf-8");
    return JSON.parse(raw) as MessagesFile;
  } catch (err) {
    console.error("[readWechatMessages] 读取微信消息失败:", err);
    return null;
  }
}

export const readWechatMessagesTool: AgentTool<typeof readWechatMessagesSchema> = {
  name: "read_wechat_messages",
  label: "Read WeChat Messages",
  description:
    "读取指定客户/项目的微信群原始聊天记录。当用户询问微信客户群/项目群细节、追问某条信号背景、或需要基于原始聊天内容分析时应调用。",
  parameters: readWechatMessagesSchema,
  async execute(_toolCallId, params: Static<typeof readWechatMessagesSchema>) {
    const account = params.account?.trim() || "示例微信客户";
    const maxMessages = params.max_messages ?? 100;
    const data = loadMessages(account);
    if (!data) {
      return {
        content: [{ type: "text" as const, text: `暂无 ${account} 的微信群消息记录。` }],
        details: {},
      };
    }

    const messages = (data.data?.messages || []).slice(0, maxMessages);
    if (messages.length === 0) {
      return {
        content: [{ type: "text" as const, text: `${account} 的微信群消息为空。` }],
        details: {},
      };
    }

    const lines: string[] = [];
    lines.push(`【${account}】微信群最近 ${messages.length} 条消息`);
    for (const m of messages) {
      const chat = m.chat_name || "未知群";
      let sender = "未知发送人";
      const s = m.sender;
      if (typeof s === "object" && s && (s.name || s.id)) {
        sender = s.name || s.id || "未知发送人";
      } else if (typeof s === "string") {
        sender = s;
      } else if (m.sender_name) {
        sender = m.sender_name;
      }
      const time = m.create_time || m.createTime || "";
      const content = m.content || "";
      lines.push(`\n[${chat}] ${time} ${sender}:\n${content}`);
    }

    return {
      content: [{ type: "text" as const, text: lines.join("\n") }],
      details: { messageCount: messages.length },
    };
  },
};

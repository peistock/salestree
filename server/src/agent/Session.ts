import { Agent } from "@earendil-works/pi-agent-core";
import type { AgentMessage } from "@earendil-works/pi-agent-core";
import { createLlmModel } from "../llm/provider.ts";
import { createTools } from "../tools/Toolkit.ts";
import { scanAndNotify } from "../tools/careScanner.ts";
import { Memory } from "../memory/Memory.ts";
import { AssociationEngine } from "../association/AssociationEngine.ts";
import { SkillLoader } from "../skills/SkillLoader.ts";
import { VectorStore } from "../knowledge/VectorStore.ts";
import { ConversationStore } from "../memory/ConversationStore.ts";
import { IterationBudget } from "./IterationBudget.ts";

export interface OutgoingMessage {
  type: "status" | "token" | "event" | "result" | "error" | "history";
  content?: string;
  event_type?: string;
  message?: string;
  reply?: string;
  files?: string[];
  found_files?: string[];
  hint?: string;
  thread_id?: string;
  messages?: { role: "user" | "assistant"; content: string }[];
}

export interface Attachment {
  name: string;
  url: string;
  mimeType: string;
  size: number;
  data?: string; // base64 for images
}

export class AgentSession {
  private agent: Agent;
  private chunks: string[] = [];
  private finalText = "";
  private turnCount = 0;
  private userId: string;
  private memory: Memory;
  private association: AssociationEngine;
  private skillLoader: SkillLoader;
  private vectorStore: VectorStore;
  private budget: IterationBudget = new IterationBudget();
  private onBudgetExceeded?: (reason: string) => void;
  private threadId: string;
  private conversationStore: ConversationStore;

  constructor(userId: string, userName = "", threadId: string, conversationStore: ConversationStore) {
    this.userId = userId;
    this.threadId = threadId;
    this.conversationStore = conversationStore;
    const { model, streamFn } = createLlmModel();
    this.agent = new Agent({
      initialState: {
        systemPrompt: this.buildSystemPrompt(),
        model,
        thinkingLevel: "off",
        tools: createTools(userId),
        messages: [],
      },
      streamFn,
      afterToolCall: async ({ toolCall, result }) => {
        const text = (result.content ?? [])
          .filter((b): b is { type: "text"; text: string } => b.type === "text")
          .map((b) => b.text)
          .join("\n");
        await scanAndNotify(userId, `tool:${toolCall.name}`, text);
        return undefined;
      },
    });
    this.memory = new Memory(userId, userName);
    this.association = new AssociationEngine();
    this.skillLoader = new SkillLoader();
    this.vectorStore = new VectorStore();
  }

  private buildSystemPrompt(): string {
    return `你是销销，一个面向互联网广告/营销公司销售团队的 AI 助手。

当前可用工具：
- get_time：返回当前日期和时间
- search_web：联网搜索实时信息（新闻、公开资料、政策等）
- fetch_webpage / browse_open / jina_reader：获取指定网页内容
- search_industry_news：按关键词在销销知识库/资讯工作台搜索文章（适合查某个客户/主题相关的文章）
- get_news_digest：读取资讯看板（/wechat_kb）整体最新摘要，包含主题、文章标题摘要、线索库。当用户问'总结资讯看板'、'今天有什么资讯'、'最近行业动态'时优先调用
- read_feishu_messages：读取指定客户的飞书群原始聊天记录。销销会定期通过飞书 CLI 从客户群、内部群拉取消息，原始记录保存在 data/projects/{客户名}_messages.json。当用户提及'飞书群'、'群里'、'客户群'、'内部群'、追问某条项目看板信号背景、或需要基于原始聊天内容分析时优先调用
- get_contacts / get_deals / get_activities：查询联系人、商机、活动记录
- log_activity：将跟进活动写入 CRM
- save_account_research：把研究摘要写回客户资料的 research_summary 字段
- todo：管理当前销售的待办任务（create / update / list / clear）
- plan：创建或推进多步骤执行计划（create / advance / progress）
- read_file：读取用户上传的本地文件内容（HTML、TXT、MD、JSON、CSV 等），路径以 /data/uploads/ 开头

要求：
1. 回答简洁专业，用中文。
2. 需要实时信息时，先调用 search_web，再基于结果回答；search_web 对同一问题最多调用 1 次，禁止反复搜索。
3. 需要当前时间时，调用 get_time；如果上下文中已提供当前时间，不要重复调用。
4. 涉及客户/联系人/商机时，优先调用 CRM 工具核实，不要编造。
5. 生成跟进文案或客户研究时，优先使用 CRM 内部信息；除非用户明确要求，否则不要主动搜索外部新闻。
6. 客户研究（account-research）时，search_web 最多调用 1 次，get_account 最多调用 1 次，最后必须调用 save_account_research 保存摘要。
7. 引用外部信息必须标注来源。
8. 如果提供了客户/商机/联系人背景信息，回答时要结合这些信息。
9. 用户上传文档后，如果用户的问题需要基于文档内容回答，必须调用 read_file 读取内容，不要以"无法读取本地文件"为由拒绝。`;
  }

  async run(
    query: string,
    attachments: Attachment[],
    send: (msg: OutgoingMessage) => void,
  ): Promise<OutgoingMessage> {
    this.chunks = [];
    this.finalText = "";
    this.turnCount = 0;
    this.budget = new IterationBudget();
    this.onBudgetExceeded = (reason: string) => {
      send({ type: "status", message: `已超出 ${reason}，正在整理结果…` });
    };

    await scanAndNotify(this.userId, "user_message", query);

    // 持久化用户原始消息（包含附件描述），刷新页面后也能看到
    const attachmentDesc = attachments.length
      ? "\n\n[附件]\n" + attachments.map((a) => `- ${a.name}：${a.url}`).join("\n")
      : "";
    const persistedUserMessage = query ? `${query}${attachmentDesc}` : attachmentDesc.trim();
    await this.conversationStore.addMessage(this.userId, this.threadId, "user", persistedUserMessage).catch((err) => {
      console.error("[session] 持久化用户消息失败:", err);
    });

    // 把附件元数据追加到线程级 files_json
    if (attachments.length > 0) {
      try {
        const thread = await this.conversationStore.getThread(this.userId, this.threadId);
        const existing = (thread?.files_json as Attachment[]) ?? [];
        const merged = [...existing, ...attachments];
        await this.conversationStore.updateThreadMeta(this.userId, this.threadId, { files_json: merged });
      } catch (err) {
        console.error("[session] 更新线程附件失败:", err);
      }
    }

    const contextText = await this.buildContextText(query);
    const docAttachments = attachments.filter((a) => !a.mimeType.startsWith("image/"));
    const docList = docAttachments.length
      ? "\n\n用户上传的文档（仅文件名和链接，需自行读取）：\n" +
        docAttachments.map((a) => `- ${a.name} (${a.url})`).join("\n")
      : "";
    const promptText = `${contextText}\n\n用户问题：${query}${docList}`;

    const now = Date.now();
    const imageAttachments = attachments.filter((a) => a.mimeType.startsWith("image/") && a.data);
    const promptContent: any[] = [{ type: "text", text: promptText }];
    for (const img of imageAttachments) {
      promptContent.push({ type: "image", data: img.data, mimeType: img.mimeType });
    }
    const promptMessages = [{ role: "user", content: promptContent, timestamp: now }] as AgentMessage[];

    this.agent.subscribe((event) => {
      if (event.type === "turn_end") {
        this.turnCount++;
        const msg = event.message;
        let responseContent = "";
        if (msg.role === "assistant") {
          const text = msg.content
            .filter((b): b is { type: "text"; text: string } => b.type === "text")
            .map((b) => b.text)
            .join("");
          if (text.length > 0) this.finalText = text;
          responseContent = text;

          // 持久化助手回复（只保存最终答案，不含思维链）
          this.conversationStore.addMessage(this.userId, this.threadId, "assistant", this.finalText).catch((err) => {
            console.error("[session] 持久化助手消息失败:", err);
          });
        }

        const stopReason = this.budget.check(this.turnCount, this.agent.state.messages as any, responseContent);
        if (stopReason) {
          this.budget.markStopped(stopReason);
          this.onBudgetExceeded?.(stopReason);
          this.agent.abort();
        }
      }

      if (event.type === "message_update") {
        const e = event.assistantMessageEvent;
        // 只把最终答案文本流给用户，思维链（thinking_delta）不展示
        if (e?.type === "text_delta") {
          this.chunks.push(e.delta);
          send({ type: "token", content: e.delta });
        }
      } else if (event.type === "tool_execution_start") {
        send({
          type: "event",
          event_type: "TOOL_EXECUTION_START",
          message: `正在调用 ${event.toolName}...`,
        });
      } else if (event.type === "tool_execution_end") {
        send({
          type: "event",
          event_type: "TOOL_EXECUTION_END",
          message: `${event.toolName} 完成`,
        });
      }
    });

    try {
      await this.agent.prompt(promptMessages);
      return { type: "result", reply: this.finalText || this.chunks.join(""), files: [], found_files: [] };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { type: "error", message };
    }
  }

  private async buildContextText(query: string): Promise<string> {
    const [memoryCtx, associationCtx, knowledgeChunks] = await Promise.all([
      this.memory.load(),
      this.association.buildContext(this.userId, query),
      this.vectorStore.search(query, 3).catch(() => []),
    ]);

    const skill = this.skillLoader.match(query);

    const parts: string[] = [];
    parts.push(memoryCtx.core);
    if (memoryCtx.profile) parts.push(memoryCtx.profile);
    if (memoryCtx.business) parts.push(memoryCtx.business);
    if (memoryCtx.briefing) parts.push(memoryCtx.briefing);
    if (memoryCtx.override) parts.push(memoryCtx.override);
    if (memoryCtx.episodes) parts.push(memoryCtx.episodes);
    if (associationCtx) parts.push(associationCtx);
    if (skill) {
      parts.push(`【当前 Skill】${skill.name}\n${skill.description}`);
      if (skill.content.length <= 3000) parts.push(skill.content);
      else parts.push(`Skill 内容较长，已外置。如需完整指导，请用 read_file 读取 ${skill.filePath}`);
    }
    if (knowledgeChunks.length > 0) {
      parts.push("【相关知识库片段】");
      for (const c of knowledgeChunks) {
        parts.push(`[${c.filename}] ${c.chunkText.slice(0, 300)}`);
      }
    }

    return parts.join("\n\n");
  }

  abort(): void {
    this.agent.abort();
  }
}

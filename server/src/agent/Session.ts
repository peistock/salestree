import { Agent } from "@earendil-works/pi-agent-core";
import type { AgentMessage } from "@earendil-works/pi-agent-core";
import { createLlmModel } from "../llm/provider.ts";
import { createTools } from "../tools/Toolkit.ts";
import { scanAndNotify } from "../tools/careScanner.ts";
import { Memory } from "../memory/Memory.ts";
import { AssociationEngine } from "../association/AssociationEngine.ts";
import { SkillLoader } from "../skills/SkillLoader.ts";
import { VectorStore } from "../knowledge/VectorStore.ts";

export interface OutgoingMessage {
  type: "status" | "token" | "event" | "result" | "error";
  content?: string;
  event_type?: string;
  message?: string;
  reply?: string;
  files?: string[];
  found_files?: string[];
}

export class AgentSession {
  private agent: Agent;
  private chunks: string[] = [];
  private finalText = "";
  private turnCount = 0;
  private readonly maxTurns = 4;
  private userId: string;
  private memory: Memory;
  private association: AssociationEngine;
  private skillLoader: SkillLoader;
  private vectorStore: VectorStore;

  constructor(userId: string, userName = "") {
    this.userId = userId;
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
- search_industry_news：在销销知识库/资讯工作台搜索行业文章
- get_account / list_accounts：查询 CRM 客户公司
- get_contacts / get_deals / get_activities：查询联系人、商机、活动记录
- log_activity：将跟进活动写入 CRM
- save_account_research：把研究摘要写回客户资料的 research_summary 字段
- todo：管理当前销售的待办任务（create / update / list / clear）
- plan：创建或推进多步骤执行计划（create / advance / progress）

要求：
1. 回答简洁专业，用中文。
2. 需要实时信息时，先调用 search_web，再基于结果回答；search_web 对同一问题最多调用 1 次，禁止反复搜索。
3. 需要当前时间时，调用 get_time；如果上下文中已提供当前时间，不要重复调用。
4. 涉及客户/联系人/商机时，优先调用 CRM 工具核实，不要编造。
5. 生成跟进文案或客户研究时，优先使用 CRM 内部信息；除非用户明确要求，否则不要主动搜索外部新闻。
6. 客户研究（account-research）时，search_web 最多调用 1 次，get_account 最多调用 1 次，最后必须调用 save_account_research 保存摘要。
7. 引用外部信息必须标注来源。
8. 如果提供了客户/商机/联系人背景信息，回答时要结合这些信息。`;
  }

  async run(query: string, send: (msg: OutgoingMessage) => void): Promise<OutgoingMessage> {
    this.chunks = [];
    this.finalText = "";
    this.turnCount = 0;

    await scanAndNotify(this.userId, "user_message", query);

    const contextText = await this.buildContextText(query);
    const now = Date.now();
    const promptText = `${contextText}\n\n用户问题：${query}`;
    const promptMessages: AgentMessage[] = [
      { role: "user", content: promptText, timestamp: now },
    ];

    this.agent.subscribe((event) => {
      if (event.type === "turn_end") {
        this.turnCount++;
        if (this.turnCount >= this.maxTurns) {
          this.agent.abort();
        }
        const msg = event.message;
        if (msg.role === "assistant") {
          const text = msg.content
            .filter((b) => b.type === "text" || b.type === "thinking")
            .map((b) => (b.type === "text" ? (b as { text: string }).text : (b as { thinking: string }).thinking))
            .join("");
          if (text.length > 0) this.finalText = text;
        }
      }

      if (event.type === "message_update") {
        const e = event.assistantMessageEvent;
        if (e?.type === "text_delta" || e?.type === "thinking_delta") {
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

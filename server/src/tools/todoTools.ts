import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { query } from "../db/index.ts";

type TodoItem = { id: string; content: string; status: "pending" | "in_progress" | "completed" | "cancelled" };

const todoItemSchema = Type.Object({
  id: Type.Optional(Type.String({ description: "任务 ID，更新时必填" })),
  content: Type.Optional(Type.String({ description: "任务内容" })),
  status: Type.Optional(Type.String({ description: "状态：pending / in_progress / completed / cancelled" })),
});

const todoSchema = Type.Object({
  action: Type.String({ description: "操作：create / update / list / clear" }),
  items: Type.Optional(Type.Array(todoItemSchema, { description: "create 时传入任务列表" })),
  id: Type.Optional(Type.String({ description: "update 时指定任务 ID" })),
  status: Type.Optional(Type.String({ description: "update 时指定新状态" })),
});

const VALID_STATUSES = new Set(["pending", "in_progress", "completed", "cancelled"]);

async function loadTodos(userId: string): Promise<TodoItem[]> {
  const rows = (await query(`SELECT todos_json FROM todos WHERE store_key=$1`, [userId])) as Record<string, any>[];
  if (!rows.length) return [];
  try {
    return JSON.parse(rows[0].todos_json as string) as TodoItem[];
  } catch {
    return [];
  }
}

async function saveTodos(userId: string, todos: TodoItem[]): Promise<void> {
  await query(
    `INSERT INTO todos (store_key, user_id, todos_json, updated_at) VALUES ($1,$2,$3::jsonb,NOW())
     ON CONFLICT (store_key) DO UPDATE SET todos_json=$3::jsonb, updated_at=NOW()`,
    [userId, userId, JSON.stringify(todos)],
  );
}

export function createTodoTool(userId: string): AgentTool<typeof todoSchema> {
  return {
    name: "todo",
    label: "Todo",
    description: "创建、更新、查看或清空当前销售的待办任务列表",
    parameters: todoSchema,
    async execute(_toolCallId, params: Static<typeof todoSchema>) {
      const action = params.action;
      if (action === "list") {
        const todos = await loadTodos(userId);
        const active = todos.filter((t) => t.status === "pending" || t.status === "in_progress");
        const text = active.length === 0
          ? "当前没有待办任务。"
          : ["【待办任务】", ...active.map((t) => `  - [${t.status === "in_progress" ? ">" : " "}] ${t.id}. ${t.content}`)].join("\n");
        return { content: [{ type: "text" as const, text }], details: {} };
      }

      if (action === "clear") {
        await saveTodos(userId, []);
        return { content: [{ type: "text" as const, text: "待办列表已清空。" }], details: {} };
      }

      if (action === "create") {
        const existing = await loadTodos(userId);
        const newItems: TodoItem[] = (params.items ?? [])
          .filter((i) => i.content)
          .map((i, idx) => ({
            id: i.id || `${Date.now()}-${idx}`,
            content: i.content!,
            status: (VALID_STATUSES.has(i.status ?? "") ? i.status! : "pending") as TodoItem["status"],
          }));
        await saveTodos(userId, [...existing, ...newItems]);
        return { content: [{ type: "text" as const, text: `已创建 ${newItems.length} 个任务。` }], details: {} };
      }

      if (action === "update") {
        if (!params.id) {
          return { content: [{ type: "text" as const, text: "更新任务需要提供 id。" }], details: {} };
        }
        const todos = await loadTodos(userId);
        const item = todos.find((t) => t.id === params.id);
        if (!item) {
          return { content: [{ type: "text" as const, text: `未找到任务 ${params.id}。` }], details: {} };
        }
        if (params.status && VALID_STATUSES.has(params.status)) {
          item.status = params.status as TodoItem["status"];
        }
        if (params.items && params.items[0]?.content) {
          item.content = params.items[0].content;
        }
        await saveTodos(userId, todos);
        return { content: [{ type: "text" as const, text: `任务 ${params.id} 已更新。` }], details: {} };
      }

      return { content: [{ type: "text" as const, text: "未知操作，请使用 create / update / list / clear。" }], details: {} };
    },
  };
}

const planSchema = Type.Object({
  action: Type.String({ description: "操作：create / advance / progress" }),
  steps: Type.Optional(Type.Array(Type.Object({
    id: Type.String({ description: "步骤 ID" }),
    description: Type.String({ description: "步骤描述" }),
  }), { description: "create 时传入步骤列表" })),
  step_id: Type.Optional(Type.String({ description: "advance 时指定完成的步骤 ID" })),
});

type Plan = { steps: { id: string; description: string; status: TodoItem["status"] }[]; current_step_index: number };

export function createPlanTool(userId: string): AgentTool<typeof planSchema> {
  const planKey = `${userId}:plan`;
  return {
    name: "plan",
    label: "Plan",
    description: "创建或推进一个多步骤执行计划，自动同步到 todo 列表",
    parameters: planSchema,
    async execute(_toolCallId, params: Static<typeof planSchema>) {
      const raw = (await query(`SELECT todos_json FROM todos WHERE store_key=$1`, [planKey])) as Record<string, any>[];
      let plan: Plan = raw.length ? (JSON.parse(raw[0].todos_json as string) as Plan) : { steps: [], current_step_index: 0 };

      if (params.action === "create") {
        const steps = (params.steps ?? []).map((s, i) => ({ id: s.id || String(i + 1), description: s.description, status: "pending" as TodoItem["status"] }));
        if (steps.length) steps[0].status = "in_progress" as const;
        plan = { steps, current_step_index: 0 };
        await query(
          `INSERT INTO todos (store_key, user_id, todos_json, updated_at) VALUES ($1,$2,$3::jsonb,NOW())
           ON CONFLICT (store_key) DO UPDATE SET todos_json=$3::jsonb, updated_at=NOW()`,
          [planKey, userId, JSON.stringify(plan)],
        );
        return { content: [{ type: "text" as const, text: `计划已创建，共 ${steps.length} 步。当前步骤：${steps[0]?.description ?? "无"}` }], details: {} };
      }

      if (params.action === "advance") {
        const idx = plan.steps.findIndex((s) => s.id === (params.step_id ?? ""));
        const current = idx >= 0 ? idx : plan.current_step_index;
        if (current >= 0 && current < plan.steps.length) {
          plan.steps[current].status = "completed";
          plan.current_step_index = current + 1;
          if (plan.current_step_index < plan.steps.length) {
            plan.steps[plan.current_step_index].status = "in_progress";
          }
        }
        await query(
          `INSERT INTO todos (store_key, user_id, todos_json, updated_at) VALUES ($1,$2,$3::jsonb,NOW())
           ON CONFLICT (store_key) DO UPDATE SET todos_json=$3::jsonb, updated_at=NOW()`,
          [planKey, userId, JSON.stringify(plan)],
        );
        return { content: [{ type: "text" as const, text: `步骤 ${params.step_id ?? current + 1} 已完成。` }], details: {} };
      }

      if (params.action === "progress") {
        const total = plan.steps.length;
        const completed = plan.steps.filter((s) => s.status === "completed").length;
        const current = plan.steps[plan.current_step_index]?.description ?? "收尾中";
        const lines = ["【执行计划】", ...plan.steps.map((s) => `  ${s.status === "completed" ? "✓" : s.status === "in_progress" ? ">" : "○"} ${s.id}. ${s.description}`), "", `当前步骤：${current}（${completed}/${total}）`];
        return { content: [{ type: "text" as const, text: lines.join("\n") }], details: {} };
      }

      return { content: [{ type: "text" as const, text: "未知操作，请使用 create / advance / progress。" }], details: {} };
    },
  };
}

import { config } from "../config.ts";

export interface BudgetConfig {
  maxIterations: number;
  maxDurationSeconds: number;
  maxTotalTokens: number;
}

export interface BudgetSummary {
  elapsedSeconds: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalTokens: number;
  maxIterations: number;
  maxDurationSeconds: number;
  maxTotalTokens: number;
  stoppedReason: string | null;
}

function estimateTextTokens(text: string): number {
  if (!text) return 0;
  return Math.floor(text.length * 0.6);
}

function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((item) => (typeof item === "object" && item !== null ? (item as any).text ?? "" : String(item)))
      .join(" ");
  }
  return "";
}

function estimateMessagesTokens(messages: Array<{ role: string; content?: unknown; tool_calls?: unknown[] }>): number {
  let total = 0;
  for (const msg of messages) {
    total += estimateTextTokens(extractText(msg.content));
    if (Array.isArray(msg.tool_calls)) {
      for (const tc of msg.tool_calls) {
        const args = (tc as any)?.function?.arguments ?? "";
        total += estimateTextTokens(typeof args === "string" ? args : JSON.stringify(args));
      }
    }
  }
  return total;
}

export class IterationBudget {
  readonly maxIterations: number;
  readonly maxDurationSeconds: number;
  readonly maxTotalTokens: number;
  private startTime: number;
  private totalInputTokens = 0;
  private totalOutputTokens = 0;
  private stoppedReason: string | null = null;

  constructor(cfg?: Partial<BudgetConfig>) {
    this.maxIterations = cfg?.maxIterations ?? config.agent.maxIterations;
    this.maxDurationSeconds = cfg?.maxDurationSeconds ?? config.agent.maxDurationSeconds;
    this.maxTotalTokens = cfg?.maxTotalTokens ?? config.agent.maxTotalTokens;
    this.startTime = Date.now();
  }

  get elapsedSeconds(): number {
    return (Date.now() - this.startTime) / 1000;
  }

  get totalTokens(): number {
    return this.totalInputTokens + this.totalOutputTokens;
  }

  check(iteration: number, messages: Array<{ role: string; content?: unknown; tool_calls?: unknown[] }>, responseContent = ""): string | null {
    this.totalInputTokens = estimateMessagesTokens(messages);
    this.totalOutputTokens += estimateTextTokens(responseContent);

    if (iteration >= this.maxIterations) {
      return `iteration_budget_exceeded:${iteration}`;
    }
    if (this.elapsedSeconds >= this.maxDurationSeconds) {
      return `time_budget_exceeded:${this.elapsedSeconds.toFixed(0)}s`;
    }
    if (this.totalTokens >= this.maxTotalTokens) {
      return `token_budget_exceeded:${this.totalTokens}`;
    }
    return null;
  }

  shouldCompress(thresholdRatio = 0.75): boolean {
    return this.totalTokens >= Math.floor(this.maxTotalTokens * thresholdRatio);
  }

  markStopped(reason: string): void {
    this.stoppedReason = reason;
  }

  summary(): BudgetSummary {
    return {
      elapsedSeconds: Math.round(this.elapsedSeconds * 100) / 100,
      totalInputTokens: this.totalInputTokens,
      totalOutputTokens: this.totalOutputTokens,
      totalTokens: this.totalTokens,
      maxIterations: this.maxIterations,
      maxDurationSeconds: this.maxDurationSeconds,
      maxTotalTokens: this.maxTotalTokens,
      stoppedReason: this.stoppedReason,
    };
  }
}

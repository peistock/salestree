export interface ModelPrice {
  inputUsdPer1M: number;
  outputUsdPer1M: number;
  inputCnyPer1M: number;
  outputCnyPer1M: number;
}

/**
 * 内部价目表：按 1M token 计价。
 * 全部走云端 API，按平台公开价填写；商业化前需统一校准。
 */
export const MODEL_PRICES: Record<string, ModelPrice> = {
  // Kimi k2.6（主力，api.kimi.com/coding/v1）
  // 价格沿用 Kimi K2 公开价（输入 ¥4 / 输出 ¥16 每 1M），k2.6 实际价以账单为准
  "k2.6": { inputUsdPer1M: 0.60, outputUsdPer1M: 2.40, inputCnyPer1M: 4.0, outputCnyPer1M: 16.0 },

  // Agnes（fallback，apihub.agnes-ai.com/v1）— 价格未知，待补，暂计 0
  "agnes-2.0-flash": { inputUsdPer1M: 0, outputUsdPer1M: 0, inputCnyPer1M: 0, outputCnyPer1M: 0 },

  // DeepSeek (v4 / chat 系列)
  "deepseek-chat": { inputUsdPer1M: 0.27, outputUsdPer1M: 1.10, inputCnyPer1M: 2.0, outputCnyPer1M: 8.0 },
  "deepseek-v4-flash": { inputUsdPer1M: 0.27, outputUsdPer1M: 1.10, inputCnyPer1M: 2.0, outputCnyPer1M: 8.0 },
  "deepseek-v4-pro": { inputUsdPer1M: 0.27, outputUsdPer1M: 1.10, inputCnyPer1M: 2.0, outputCnyPer1M: 8.0 },
  "deepseek-reasoner": { inputUsdPer1M: 0.55, outputUsdPer1M: 2.19, inputCnyPer1M: 4.0, outputCnyPer1M: 16.0 },

  // Moonshot / Kimi
  "moonshot-v1-8k": { inputUsdPer1M: 0.60, outputUsdPer1M: 0.60, inputCnyPer1M: 4.2, outputCnyPer1M: 4.2 },
  "moonshot-v1-32k": { inputUsdPer1M: 1.20, outputUsdPer1M: 1.20, inputCnyPer1M: 8.4, outputCnyPer1M: 8.4 },

  // 百炼 qwen
  "qwen-turbo": { inputUsdPer1M: 0.30, outputUsdPer1M: 0.60, inputCnyPer1M: 2.0, outputCnyPer1M: 6.0 },
  "qwen-plus": { inputUsdPer1M: 0.80, outputUsdPer1M: 2.00, inputCnyPer1M: 6.0, outputCnyPer1M: 16.0 },
  "qwen-max": { inputUsdPer1M: 2.00, outputUsdPer1M: 6.00, inputCnyPer1M: 20.0, outputCnyPer1M: 60.0 },

  // 历史本地模型（LM Studio，已停用）：不计费
  "qwen/qwen3.6-27b": { inputUsdPer1M: 0, outputUsdPer1M: 0, inputCnyPer1M: 0, outputCnyPer1M: 0 },
  "qwen/qwen3.6-35b-a3b": { inputUsdPer1M: 0, outputUsdPer1M: 0, inputCnyPer1M: 0, outputCnyPer1M: 0 },
};

export interface UsageCost {
  costUsd: number;
  costCny: number;
}

function round8(value: number): number {
  return Math.round(value * 1e8) / 1e8;
}

export function calculateCost(
  model: string,
  provider: string,
  inputTokens: number,
  outputTokens: number,
): UsageCost {
  const price = MODEL_PRICES[`${provider}/${model}`] ?? MODEL_PRICES[model];
  if (!price) {
    return { costUsd: 0, costCny: 0 };
  }

  const costUsd =
    (inputTokens * price.inputUsdPer1M + outputTokens * price.outputUsdPer1M) / 1_000_000;
  const costCny =
    (inputTokens * price.inputCnyPer1M + outputTokens * price.outputCnyPer1M) / 1_000_000;

  return { costUsd: round8(costUsd), costCny: round8(costCny) };
}

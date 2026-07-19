export interface ModelPrice {
  inputUsdPer1M: number;
  outputUsdPer1M: number;
  inputCnyPer1M: number;
  outputCnyPer1M: number;
}

/**
 * 内部价目表：按 1M token 计价。
 * 本地模型（LM Studio）默认 0，云端模型按当前平台公开价填写。
 * 商业化前需统一校准。
 */
export const MODEL_PRICES: Record<string, ModelPrice> = {
  // DeepSeek (v4 / chat 系列)
  "deepseek-chat": { inputUsdPer1M: 0.27, outputUsdPer1M: 1.10, inputCnyPer1M: 2.0, outputCnyPer1M: 8.0 },
  "deepseek-reasoner": { inputUsdPer1M: 0.55, outputUsdPer1M: 2.19, inputCnyPer1M: 4.0, outputCnyPer1M: 16.0 },

  // Moonshot / Kimi
  "moonshot-v1-8k": { inputUsdPer1M: 0.60, outputUsdPer1M: 0.60, inputCnyPer1M: 4.2, outputCnyPer1M: 4.2 },
  "moonshot-v1-32k": { inputUsdPer1M: 1.20, outputUsdPer1M: 1.20, inputCnyPer1M: 8.4, outputCnyPer1M: 8.4 },

  // 百炼 qwen
  "qwen-turbo": { inputUsdPer1M: 0.30, outputUsdPer1M: 0.60, inputCnyPer1M: 2.0, outputCnyPer1M: 6.0 },
  "qwen-plus": { inputUsdPer1M: 0.80, outputUsdPer1M: 2.00, inputCnyPer1M: 6.0, outputCnyPer1M: 16.0 },
  "qwen-max": { inputUsdPer1M: 2.00, outputUsdPer1M: 6.00, inputCnyPer1M: 20.0, outputCnyPer1M: 60.0 },

  // 本地 LM Studio：不计费
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

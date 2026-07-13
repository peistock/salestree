import "dotenv/config";

function splitEnv(name: string): string[] {
  const raw = process.env[name];
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export const config = {
  port: Number(process.env.PORT ?? "8001"),
  llm: {
    baseUrl: process.env.LLM_BASE_URL?.replace(/\/$/, ""),
    apiKey: process.env.LLM_API_KEY ?? "lm-studio",
    modelDaily: process.env.MODEL_DAILY ?? "qwen/qwen3.6-27b",
    modelComplex: process.env.MODEL_COMPLEX ?? "qwen/qwen3.6-27b",
    modelSummary: process.env.MODEL_SUMMARY ?? "qwen/qwen3.6-27b",
    fallbackUrls: splitEnv("LLM_FALLBACK_URLS"),
    fallbackKeys: splitEnv("LLM_FALLBACK_KEYS"),
    fallbackNames: splitEnv("LLM_FALLBACK_NAMES"),
    fallbackModels: splitEnv("LLM_FALLBACK_MODELS"),
  },
  db: {
    host: process.env.DB_HOST ?? "localhost",
    port: Number(process.env.DB_PORT ?? "5433"),
    user: process.env.DB_USER ?? "family",
    password: process.env.DB_PASSWORD ?? "salesmind2026",
    name: process.env.DB_NAME ?? "salesmind",
  },
  searxngUrl: process.env.SEARXNG_URL ?? "http://127.0.0.1:8080",
  pythonFallbackUrl: process.env.PYTHON_FALLBACK_URL ?? "http://localhost:8000",
  dataDir: process.env.DATA_DIR ?? "../data",
  wechatKbOutputDir:
    process.env.WECHAT_KB_OUTPUT_DIR ?? "../third_party/wechat-digest-skill/output",
  salesPolicyFilePath: process.env.SALES_POLICY_FILE_PATH ?? "../data/sales_policies.json",
  agent: {
    maxIterations: Number(process.env.AGENT_MAX_ITERATIONS ?? "50"),
    maxDurationSeconds: Number(process.env.AGENT_MAX_DURATION_SECONDS ?? "180"),
    maxTotalTokens: Number(process.env.AGENT_MAX_TOTAL_TOKENS ?? "32000"),
  },
};

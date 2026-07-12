import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const fetchWebpageSchema = Type.Object({
  url: Type.String({ description: "要获取的网页 URL" }),
  use_jina: Type.Optional(Type.Boolean({ description: "是否使用 jina.ai 摘要服务", default: true })),
});

export const fetchWebpageTool: AgentTool<typeof fetchWebpageSchema> = {
  name: "fetch_webpage",
  label: "Fetch Webpage",
  description: "获取指定网页内容并转成 Markdown 文本，便于阅读和分析",
  parameters: fetchWebpageSchema,
  async execute(_toolCallId, params: Static<typeof fetchWebpageSchema>) {
    const url = params.url.trim();
    if (!url) {
      return { content: [{ type: "text" as const, text: "URL 不能为空" }], details: {} };
    }
    const targetUrl = params.use_jina !== false ? `https://r.jina.ai/http://${url.replace(/^https?:\/\//, "")}` : url;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      const resp = await fetch(targetUrl, { signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) {
        return { content: [{ type: "text" as const, text: `网页获取失败：HTTP ${resp.status}` }], details: {} };
      }
      const text = await resp.text();
      return { content: [{ type: "text" as const, text: text.slice(0, 4000) }], details: {} };
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      return { content: [{ type: "text" as const, text: `网页获取异常：${message}` }], details: {} };
    }
  },
};

import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";
import { fetchWebpageTool } from "./fetchWebpage.ts";

const urlSchema = Type.Object({
  url: Type.String({ description: "网页 URL" }),
});

export const browseOpenTool: AgentTool<typeof urlSchema> = {
  name: "browse_open",
  label: "Browse Open",
  description: "打开并获取网页内容（兼容旧 skill 名称）",
  parameters: urlSchema,
  async execute(toolCallId, params: Static<typeof urlSchema>) {
    return fetchWebpageTool.execute(toolCallId, { url: params.url, use_jina: false });
  },
};

export const jinaReaderTool: AgentTool<typeof urlSchema> = {
  name: "jina_reader",
  label: "Jina Reader",
  description: "用 jina.ai 读取网页并转成 Markdown（兼容旧 skill 名称）",
  parameters: urlSchema,
  async execute(toolCallId, params: Static<typeof urlSchema>) {
    return fetchWebpageTool.execute(toolCallId, { url: params.url, use_jina: true });
  },
};

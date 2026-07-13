import type { AgentTool } from "@earendil-works/pi-agent-core";
import { browseOpenTool, jinaReaderTool } from "./aliases.ts";
import { createDbTools } from "./dbTools.ts";
import { fetchWebpageTool } from "./fetchWebpage.ts";
import { getTimeTool } from "./getTime.ts";
import { getNewsDigestTool } from "./getNewsDigest.ts";
import { searchIndustryNewsTool } from "./searchIndustryNews.ts";
import { searchWebTool } from "./searchWeb.ts";

import { createTodoTool, createPlanTool } from "./todoTools.ts";

import { readFeishuMessagesTool } from "./readFeishuMessages.ts";

export function createTools(userId: string): AgentTool[] {
  return [
    getTimeTool,
    getNewsDigestTool,
    readFeishuMessagesTool,
    searchWebTool,
    fetchWebpageTool,
    browseOpenTool,
    jinaReaderTool,
    searchIndustryNewsTool,
    createTodoTool(userId),
    createPlanTool(userId),
    ...createDbTools(userId),
  ];
}

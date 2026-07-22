import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { Type } from "@earendil-works/pi-ai";
import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { Static } from "@earendil-works/pi-ai";

const runProjectLifecycleReviewSchema = Type.Object({
  account: Type.String({ description: "客户/项目名称，例如'抖音商城内广'" }),
  full_pull: Type.Optional(
    Type.Boolean({
      description: "是否先全量拉取飞书消息再分析。默认 true。",
      default: true,
    }),
  ),
});

function projectRoot(): string {
  return path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../..");
}

// 与 index.ts 的 loadProjectAccounts 同源：判断项目属于哪个消息来源，
// 拉取脚本必须按来源选择（钉钉项目走 feishu/pull_account.py 会报"找不到账户"）
function detectAccountSource(account: string): string {
  const root = projectRoot();
  const sources = ["feishu", "dingtalk", "wechat", "hybrid"];
  for (const source of sources) {
    try {
      const raw = fs.readFileSync(path.join(root, "data/projects", `${source}_accounts.json`), "utf-8");
      const config = JSON.parse(raw);
      if (config.accounts && Object.keys(config.accounts).includes(account)) {
        return source;
      }
    } catch {
      // 配置文件缺失时尝试下一个来源
    }
  }
  return "feishu";
}

function loadEnv(): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = { ...process.env };
  const envFiles = [".env", "server/.env"];
  for (const file of envFiles) {
    const p = path.join(projectRoot(), file);
    if (!fs.existsSync(p)) continue;
    const text = fs.readFileSync(p, "utf-8");
    for (const line of text.split("\n")) {
      const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
      if (!m) continue;
      const [, key, value] = m;
      if (env[key] === undefined) {
        env[key] = value;
      }
    }
  }
  return env;
}

async function runCommand(
  cmd: string,
  args: string[],
  cwd: string,
  env: Record<string, string | undefined>,
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, args, { cwd, env });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (data: Buffer) => {
      stdout += data.toString("utf-8");
    });
    proc.stderr.on("data", (data: Buffer) => {
      stderr += data.toString("utf-8");
    });
    proc.on("close", (exitCode) => {
      resolve({ stdout, stderr, exitCode: exitCode ?? 0 });
    });
  });
}

export const runProjectLifecycleReviewTool: AgentTool<typeof runProjectLifecycleReviewSchema> = {
  name: "run_project_lifecycle_review",
  label: "Run Project Lifecycle Review",
  description:
    "对客户/项目进行全周期复盘：按时间阶段分析群聊记录，提炼阶段目标、核心策略、关键成果与当前挑战。适用于用户问'项目阶段目标'、'全周期复盘'、'项目策略和成果'等。",
  parameters: runProjectLifecycleReviewSchema,
  async execute(_toolCallId, params: Static<typeof runProjectLifecycleReviewSchema>) {
    const account = params.account?.trim();
    if (!account) {
      return {
        content: [{ type: "text" as const, text: "请提供客户/项目名称。" }],
        details: {},
      };
    }

    const cwd = projectRoot();
    const env = loadEnv();
    const source = detectAccountSource(account);

    // 1. 按需全量拉取（按项目来源选择脚本）
    if (params.full_pull !== false) {
      const pullResult = await runCommand(
        "python3",
        [`scripts/${source}/pull_account.py`, "--account", account, "--full"],
        cwd,
        env,
      );
      if (pullResult.exitCode !== 0) {
        return {
          content: [
            {
              type: "text" as const,
              text: `全量拉取消息失败：\n${pullResult.stderr || pullResult.stdout}`,
            },
          ],
          details: { exitCode: pullResult.exitCode },
        };
      }
    }

    // 2. 运行全周期复盘脚本
    const reviewResult = await runCommand(
      "python3",
      ["scripts/feishu/project_lifecycle_review.py", "--account", account],
      cwd,
      env,
    );
    if (reviewResult.exitCode !== 0) {
      return {
        content: [
          {
            type: "text" as const,
            text: `全周期复盘脚本执行失败：\n${reviewResult.stderr || reviewResult.stdout}`,
          },
        ],
        details: { exitCode: reviewResult.exitCode },
      };
    }

    // 3. 读取生成的 Markdown 报告
    const safeName = account.replace(/[^a-zA-Z0-9一-龥]/g, "_");
    const mdPath = path.join(cwd, "data/projects", `${safeName}_lifecycle.md`);
    let reportText = "";
    if (fs.existsSync(mdPath)) {
      reportText = fs.readFileSync(mdPath, "utf-8");
    } else {
      reportText = reviewResult.stdout;
    }

    return {
      content: [
        {
          type: "text" as const,
          text: reportText || "复盘报告为空。",
        },
      ],
      details: { reportPath: mdPath },
    };
  },
};

import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import { config } from "../config.ts";

export interface Skill {
  name: string;
  description: string;
  triggers: string[];
  content: string;
  filePath: string;
}

export class SkillLoader {
  private skills: Skill[] = [];
  private loaded = false;

  private loadAll(): void {
    if (this.loaded) return;
    const dir = path.resolve(config.dataDir, "skills");
    if (!fs.existsSync(dir)) {
      this.loaded = true;
      return;
    }
    const files = this.listMdFiles(dir);
    for (const file of files) {
      const raw = fs.readFileSync(file, "utf-8");
      const parsed = matter(raw);
      const name = String(parsed.data.name ?? "");
      if (!name) continue;
      const triggers = this.extractTriggers(parsed.data, parsed.content);
      this.skills.push({
        name,
        description: String(parsed.data.description ?? ""),
        triggers,
        content: parsed.content.trim(),
        filePath: file,
      });
    }
    this.loaded = true;
  }

  private extractTriggers(frontmatter: Record<string, unknown>, content: string): string[] {
    // 1. explicit triggers frontmatter (array or comma-separated string)
    const rawTriggers = frontmatter.triggers ?? frontmatter["触发词"];
    if (rawTriggers) {
      if (Array.isArray(rawTriggers)) {
        return rawTriggers.map((s) => String(s).trim()).filter(Boolean);
      }
      return String(rawTriggers)
        .split(/[,，、]/)
        .map((s) => s.trim())
        .filter(Boolean);
    }

    // 2. fallback: parse triggers from description / content body
    const description = String(frontmatter.description ?? "");
    const triggers = this.parseTriggersText(description);
    if (triggers.length > 0) return triggers;
    return this.parseTriggersText(content);
  }

  private parseTriggersText(text: string): string[] {
    const lines = text.split(/\r?\n/);
    const result: string[] = [];
    for (const line of lines) {
      // match "触发词：..." or "触发词包括但不限于：..."
      const idx = line.indexOf("触发词");
      if (idx === -1) continue;
      const afterTrigger = line.slice(idx + 3);
      const colonMatch = afterTrigger.match(/[：:]\s*(.*)/);
      if (!colonMatch) continue;
      const part = colonMatch[1];
      // strip "包括但不限于" prefix if present
      const cleaned = part.replace(/(?:包括)?但不限于[：:]\s*/, "");
      const items = cleaned
        .split(/[,，、]/)
        .map((s) => s.trim())
        .filter(Boolean);
      result.push(...items);
    }
    return result;
  }

  private listMdFiles(dir: string): string[] {
    const result: string[] = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true, recursive: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith(".md")) {
        result.push(path.join(entry.parentPath ?? dir, entry.name));
      }
    }
    return result;
  }

  match(query: string): Skill | undefined {
    this.loadAll();
    const q = query.toLowerCase();
    const qNormalized = q.replace(/[-_]/g, " ");
    for (const skill of this.skills) {
      for (const trigger of skill.triggers) {
        if (q.includes(trigger.toLowerCase())) return skill;
      }
      if (skill.name) {
        const name = skill.name.toLowerCase();
        if (q.includes(name)) return skill;
        // 兼容 "hv analysis" 与 "hv-analysis"
        if (qNormalized.includes(name.replace(/[-_]/g, " "))) return skill;
      }
    }
    return undefined;
  }

  get(name: string): Skill | undefined {
    this.loadAll();
    return this.skills.find((s) => s.name === name);
  }

  all(): Skill[] {
    this.loadAll();
    return this.skills;
  }
}

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
      const triggersText = String(parsed.data["触发词"] ?? "");
      const triggers = triggersText
        .split(/[,，、]/)
        .map((s) => s.trim())
        .filter(Boolean);
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

  private listMdFiles(dir: string): string[] {
    const result: string[] = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true, recursive: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith(".md")) {
        result.push(path.join(dir, entry.parentPath ?? "", entry.name));
      }
    }
    return result;
  }

  match(query: string): Skill | undefined {
    this.loadAll();
    const q = query.toLowerCase();
    for (const skill of this.skills) {
      for (const trigger of skill.triggers) {
        if (q.includes(trigger.toLowerCase())) return skill;
      }
      if (skill.name && q.includes(skill.name.toLowerCase())) return skill;
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

import "dotenv/config";
import { Memory } from "../src/memory/Memory.ts";
import { AssociationEngine } from "../src/association/AssociationEngine.ts";
import { SkillLoader } from "../src/skills/SkillLoader.ts";

const userId = process.argv[2] ?? "sales_001";
const query = process.argv[3] ?? "快手那边最近怎么样";

const memory = new Memory(userId);
const ctx = await memory.load();
console.log("=== CORE ===\n", ctx.core);
console.log("\n=== PROFILE ===\n", ctx.profile);
console.log("\n=== BUSINESS ===\n", ctx.business);
console.log("\n=== BRIEFING ===\n", ctx.briefing);
console.log("\n=== OVERRIDE ===\n", ctx.override);
console.log("\n=== EPISODES ===\n", ctx.episodes);

const assoc = new AssociationEngine();
const assocCtx = await assoc.buildContext(userId, query);
console.log("\n=== ASSOCIATION ===\n", assocCtx || "(none)");

const skills = new SkillLoader();
const skill = skills.match(query);
console.log("\n=== SKILL ===\n", skill ? `${skill.name}: ${skill.description}` : "(none)");

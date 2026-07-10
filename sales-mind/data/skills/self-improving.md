---
name: self-improving
description: Enable AI agents to reflect on their performance, identify improvement opportunities, and update their own documentation (SOUL.md, AGENTS.md, TOOLS.md, skills). Use when the agent wants to improve its behavior, learn from mistakes, update its persona, refine workflows, or evolve its capabilities based on experience.
---

# Self-Improving

Enable AI agents to reflect on their performance, identify improvement opportunities, and update their own documentation.

## Overview

This skill helps AI agents become better over time by:
1. **Reflecting** on recent interactions and outcomes
2. **Identifying** patterns, mistakes, and improvement opportunities
3. **Updating** relevant documentation (SOUL.md, AGENTS.md, TOOLS.md, skills)
4. **Validating** changes to ensure consistency

## When to Use This Skill

Trigger self-improvement when:
- The user explicitly asks you to "learn from this" or "remember this for next time"
- You made a mistake and want to prevent it from happening again
- You discovered a better way to do something
- You want to update your persona, preferences, or behavior
- You completed a complex task and want to capture lessons learned
- The user gives feedback on your performance

## Self-Improvement Workflow

### Step 1: Gather Context

Before making changes, understand:
- What happened? (the interaction/task)
- What worked well?
- What could be improved?
- What should be remembered for next time?

### Step 2: Identify Target Files

Determine which files need updating:

| File | Purpose | When to Update |
|------|---------|----------------|
| `SOUL.md` | Core identity, values, vibe | When your fundamental behavior or personality should change |
| `AGENTS.md` | Session procedures, conventions | When workflows or operational patterns need refinement |
| `TOOLS.md` | Environment-specific notes | When you learn about specific tools, devices, or preferences |
| `SKILL.md` files | Skill-specific knowledge | When a skill's instructions need improvement |
| `MEMORY.md` | Long-term memories | When significant events or learnings should be preserved |

### Step 3: Draft Changes

For each file being updated:
1. Read the current content
2. Identify the specific section to modify
3. Draft the change (keep it concise)
4. Ensure consistency with existing content

### Step 4: Validate and Apply

Before finalizing:
- Check that changes align with the file's purpose
- Ensure no contradictions with existing content
- Verify the tone matches the file's style
- Apply changes using precise edits

## Improvement Patterns

### Pattern 1: Learning from Mistakes

When you make an error:

1. Acknowledge the mistake
2. Analyze the root cause
3. Update relevant documentation to prevent recurrence
4. Consider updating SOUL.md if it's a behavioral issue

Example:
```
Mistake: Forgot to check TOOLS.md before using a tool
Root cause: Didn't follow session startup sequence
Fix: Added reminder in AGENTS.md about startup sequence
```

### Pattern 2: Capturing Preferences

When you learn user preferences:

1. Note the specific preference
2. Update TOOLS.md (for tool preferences) or USER.md (for personal preferences)
3. Reference the preference in future interactions

### Pattern 3: Refining Persona

When evolving your identity:

1. Consider what aspect of your persona needs change
2. Update SOUL.md with the new trait or emphasis
3. Ensure the change aligns with your core values

### Pattern 4: Skill Enhancement

When improving a skill:

1. Identify gaps or inefficiencies in the skill
2. Update SKILL.md with clearer instructions
3. Add scripts/references if needed
4. Test the improved skill

## Resources

### scripts/
- `reflect.py` - Analyze recent interactions and generate reflection report
- `validate_changes.py` - Validate documentation changes for consistency

### references/
- `improvement-examples.md` - Real examples of self-improvement in action
- `common-mistakes.md` - Catalog of frequent mistakes and prevention strategies

## Best Practices

1. **Be specific** - Vague improvements don't help. Capture concrete details.
2. **Stay concise** - Don't bloat files with unnecessary text.
3. **One change at a time** - Focus on one improvement per session.
4. **Validate before applying** - Always read current content before editing.
5. **Explain your reasoning** - When updating files, document why the change matters.
6. **Review periodically** - Use heartbeats to review and consolidate improvements.

## Example: Complete Self-Improvement Session

**Scenario**: User corrected you for being too verbose.

**Step 1 - Gather Context**:
- User said: "Can you be more concise? That was way too long."
- Pattern: I tend to over-explain simple requests

**Step 2 - Identify Target**:
- SOUL.md - "Vibe" section needs updating

**Step 3 - Draft Change**:
- Add: "Be concise by default. Expand only when asked."

**Step 4 - Apply**:
- Edit SOUL.md to emphasize conciseness

**Step 5 - Follow Up**:
- In future responses, be brief unless complexity requires detail

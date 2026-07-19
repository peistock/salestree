// ─────────────────────────────────────────────────
//  export.js  ·  build the prompt that bundles
//  edited HTML + collected comments for AI handoff.
//
//  Comment schema (current):
//    { id, text, author, createdAt,
//      general: boolean,
//      refs: [{ id, tag, snippet }] }
//
//  Also handles legacy comments that only have
//  `blockId/tag/snippet` (single-anchor).
// ─────────────────────────────────────────────────

function normalizeComment(c) {
  if (c.refs || c.general) return c;
  // Legacy single-anchor form → coerce into refs[]
  return {
    ...c,
    general: false,
    refs: [{ id: c.blockId, tag: c.tag, snippet: c.snippet }],
  };
}

export function buildExportPrompt(html, comments) {
  let out = '';
  out += 'Please revise the following HTML based on the comments below. ';
  out += 'Keep the overall structure and styling intact; only adjust the parts the comments mention. ';
  out += 'Output the complete revised HTML.\n\n';

  out += '## CURRENT HTML\n\n```html\n' + html + '\n```\n\n';

  const list = (comments || []).map(normalizeComment);
  if (list.length === 0) {
    out += '## COMMENTS\n\n(none — exporting HTML only)\n';
    return out;
  }

  const general = list.filter(c => c.general || !c.refs?.length);
  const anchored = list.filter(c => !c.general && c.refs?.length);

  if (general.length) {
    out += '## GENERAL NOTES (whole document)\n\n';
    general.forEach((c, i) => {
      const author = c.author?.name ? `${c.author.name} · ` : '';
      out += `- ${author}${c.text}\n`;
    });
    out += '\n';
  }

  if (anchored.length) {
    out += `## ANCHORED COMMENTS (${anchored.length})\n\n`;
    anchored.forEach((c, i) => {
      const author = c.author?.name ? `${c.author.name} · ` : '';
      const targets = c.refs.map(r => `<${r.tag}> "${r.snippet}"`).join(' + ');
      out += `${i + 1}. ${targets}\n`;
      out += `   ${author}${c.text}\n\n`;
    });
  }

  return out;
}

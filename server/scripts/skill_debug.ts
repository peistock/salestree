import matter from 'gray-matter';
import fs from 'node:fs';

const raw = fs.readFileSync('/Users/cpp/salestree/data/skills/hv-analysis.md', 'utf-8');
const parsed = matter(raw);
console.log('description type:', typeof parsed.data.description);
console.log('description first 200:', String(parsed.data.description).slice(0, 200));
console.log('---');

const lines = String(parsed.data.description).split(/\r?\n/);
for (const line of lines) {
  if (line.includes('触发词')) {
    console.log('FOUND:', JSON.stringify(line));
    const match = line.match(/触发词[：:]\s*(.*)/);
    console.log('match:', match);
  }
}

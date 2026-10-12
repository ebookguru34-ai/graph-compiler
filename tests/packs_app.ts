// Runs the APP's knowledge-pack code (lore scan + RAG retrieve with packs) on a job; compared with the Python port by test_packs.py.
import { readFileSync } from 'node:fs';
import { setMemStorage } from '/workspace/prompt-graph/chat/src/graph/memory';
import { scanLore } from '/workspace/prompt-graph/chat/src/graph/lore';
import { retrieve } from '/workspace/prompt-graph/chat/src/graph/rag';
import { registerPack, unregisterPack, validatePack, parseSel, setPackVecStore, LAZY_EMBED_MAX } from '/workspace/prompt-graph/chat/src/graph/packs';
const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out: unknown[] = [];
const vec = async (t: string[]) => t.map((x) => { const v = new Array(8).fill(0); for (const w of x.toLowerCase().split(/\s+/)) { let h = 0; for (const ch of w) h = (h * 31 + ch.charCodeAt(0)) % 8; v[h] += 1; } return v; });
for (const c of job.cases) {
  const m = new Map<string, string>(Object.entries(c.seed || {}));
  setMemStorage({ getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, keys: () => [...m.keys()] } as never);
  for (const p of job.packs) unregisterPack(p.id);
  setPackVecStore(null); // fresh vector cache for the swapped storage
  for (const p of c.installed ?? []) registerPack(validatePack(job.packs.find((x: { id: string }) => x.id === p)));
  const sel = parseSel(c.packs, c.packIds);
  const r: Record<string, unknown> = {};
  r.scan = scanLore(c.text, c.ns, { scope: c.scope ?? 'character + global', depth: 0, budget: c.budget ?? 0, header: '[WI]', joiner: '\n', packs: sel, level: c.level });
  r.rag = await retrieve(c.text, c.ns, { scope: c.scope ?? 'character + global', method: c.method ?? 'keyword', topK: c.topK ?? 3, minScore: 0, budget: c.budget ?? 0, header: '[Ref]', joiner: '\n', packs: sel, level: c.level },
    c.method && c.method !== 'keyword' ? vec : null);
  r.kv = Object.fromEntries([...m].filter(([k]) => k.startsWith('pgc.packemb.')).map(([k, v]) => [k, JSON.parse(v)]));
  out.push(r);
}
console.log(JSON.stringify({ out, LAZY_EMBED_MAX }));

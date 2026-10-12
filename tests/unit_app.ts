// Runs the APP's lore/rag helpers on a job and prints JSON (compared with the Python port by test_knowledge.py).
import { readFileSync } from 'node:fs';
import { setMemStorage } from '/workspace/prompt-graph/chat/src/graph/memory';
import { chunkText } from '/workspace/prompt-graph/chat/src/graph/rag';
import { parseLorebook, keyMatches, scanLore, saveLore, exportLorebook } from '/workspace/prompt-graph/chat/src/graph/lore';
import { characterFromJson } from '/workspace/prompt-graph/chat/src/charData';
const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const m = new Map<string, string>(); setMemStorage({ getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, keys: () => [...m.keys()] });
const out: Record<string, unknown> = {};
out.chunks = job.chunks.map((c: { text: string; size: number; overlap: number }) => chunkText(c.text, c.size, c.overlap));
out.books = job.books.map((b: unknown) => parseLorebook(b));
out.roundtrip = parseLorebook(JSON.parse(exportLorebook(out.books[0] as never, 'x')));
out.cardBook = characterFromJson(job.card).lorebook;
out.keys = job.keys.map((k: [string, string, boolean, boolean]) => keyMatches(k[0], k[1], k[2], k[3]));
saveLore(out.books[0] as never); saveLore(out.cardBook as never, 'char-mira');
out.scans = job.scans.map((s: [string, string, number]) => scanLore(s[0], 'char-mira', { scope: s[1], depth: 0, budget: s[2], header: '[WI]', joiner: '\n' }));
console.log(JSON.stringify(out));

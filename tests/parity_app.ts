// Runs a scenario through the APP's runGraph (bundled with esbuild, run in node) against the fake Kobold.
// usage: node parity_app.mjs <job.json>   job = {graph, scenario, port, seed, character, template, system}
import { readFileSync } from 'node:fs';
import { runGraph } from '/workspace/prompt-graph/chat/src/graph/runner';
import { setMemStorage } from '/workspace/prompt-graph/chat/src/graph/memory';
import { rollbackTurns } from '/workspace/prompt-graph/chat/src/graph/profile';
import { TEMPLATE_PRESETS, DEFAULT_SAMPLERS } from '/workspace/prompt-graph/chat/src/templates';
import { characterSystemPrompt, substChar } from '/workspace/prompt-graph/chat/src/charData';

const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const m = new Map<string, string>(Object.entries(job.seed || {}));
setMemStorage({ getItem: (k) => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, keys: () => [...m.keys()] } as any);
const realFetch = globalThis.fetch;
(globalThis as any).fetch = (u: string, init: any) => realFetch(`http://127.0.0.1:${job.port}` + String(u).replace(/^\/kobold/, ''), init);

const ch = job.character;
const userName = 'User';
const base = {
  systemPrompt: ch ? characterSystemPrompt(ch, userName) : job.system, template: TEMPLATE_PRESETS.find((t) => t.name === job.template)!,
  samplers: DEFAULT_SAMPLERS, settings: { koboldUrl: 'http://fake', useTokenCountApi: false, streaming: true, userName },
  charName: ch?.name, greeting: ch ? substChar(ch.greeting, ch.name, userName) : undefined, memNs: ch?.id,
};
let history: any[] = ch && base.greeting ? [{ id: 'greeting', role: 'assistant', content: base.greeting }] : [];
const replies: any[] = []; let k = 0;
async function run(armed: Record<string, boolean>) {
  const turnId = 'a' + (++k);
  const { ctx } = await runGraph(job.graph, { ...base, history, turnId, armed } as any);
  const reached = ctx.output !== '' || job.graph.nodes.some((n: any) => n.type === 'chat_output') && false;
  replies.push({ turnId, reply: ctx.output });
  if (ctx.output) history = [...history, { id: turnId, role: 'assistant', content: ctx.output }];
  void reached;
}
for (const a of job.scenario) {
  if (a.say != null) { history = [...history, { id: 'u' + (k + 1), role: 'user', content: a.say }]; await run(a.arm ? { [a.arm]: true } : {}); }
  else if (a.regen || a.undo) {
    const last = history[history.length - 1];
    if (!last || last.role !== 'assistant' || last.id === 'greeting') continue;
    history = history.slice(0, -1); rollbackTurns([last.id]);
    if (a.undo) { if (history.length && history[history.length - 1].role === 'user') history = history.slice(0, -1); }
    else await run({});
  }
}
console.log(JSON.stringify({ replies, kv: Object.fromEntries(m) }));

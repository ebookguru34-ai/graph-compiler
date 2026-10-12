// Dump the app's example graphs + templates/samplers/example schema as JSON (run with esbuild + node).
import { EXAMPLE_GRAPHS } from '/workspace/prompt-graph/chat/src/graph/defaults';
import { TEMPLATE_PRESETS, DEFAULT_SAMPLERS } from '/workspace/prompt-graph/chat/src/templates';
import { EXAMPLE_SCHEMA } from '/workspace/prompt-graph/chat/src/graph/profile';
import { NODE_DEFS } from '/workspace/prompt-graph/chat/src/graph/nodes';
console.log(JSON.stringify({ graphs: EXAMPLE_GRAPHS, templates: TEMPLATE_PRESETS, samplers: DEFAULT_SAMPLERS, schema: EXAMPLE_SCHEMA,
  nodeDefs: NODE_DEFS.map((d) => ({ type: d.type, inputs: d.inputs, outputs: d.outputs, defaults: d.defaults, callsLLM: !!d.callsLLM })) }));

# graphc: Prompt Graph compiler

Compiles a **Prompt Graph Chat** graph (the JSON you export from the app's graph editor) into
plain, readable **Python** that runs on its own against KoboldCPP. You can use it in a Discord bot,
a game or a script without the browser app. It needs only the Python 3.8+ standard library.

```
python graphc.py compile my-graph.json --target python -o out/
cd out && python chat_cli.py --template ChatML [--character card.png]
```

On Windows you can drag the graph `.json` onto **compile.bat**. You can also drop a character
card as a second argument: `compile.bat graph.json card.png`.

Other commands: `python graphc.py check graph.json` validates a graph and prints its run order.
`python graphc.py nodes` lists the supported node types.

## What you get (`out/`)

| file | what |
|---|---|
| `<graph_name>.py` | `run_turn(history, user_msg, ctx) -> TurnResult(reply, trace, turn_id, history, ...)`. One commented block per node in run order, with each wire as a variable. Condition, Compare, Every N and Manual Trigger become `if` blocks, and SKIP spreads the same way it does in the app. It also holds `GRAPH_JSON`, the original graph, so you can import it back into the app. |
| `pgc_runtime/` | The runtime. It covers KoboldCPP generate, SSE streaming, tokencount and embeddings; the app's instruct templates; prompt safety (assistant cue, turn stops, echo strip); trimming to the token budget; memory stores with turn-tagged rollback; profiles (commits, validation, clamp, `+N` deltas, decay and drift rules, revert); BM25 and embedding search; JSON extract; and summarize. Data goes in a JSON folder, one file per key, using the app's keys. |
| `chat_cli.py` | Terminal chat. `/regen` and `/undo` roll back that turn's memory and profile writes. `/trigger <id>` arms a Manual Trigger, and `/trace` prints the node trace. |
| `README.md` | Embedding guide covering the API, regenerate and rollback, a Discord bot and a game NPC. |
| `character.json/png` | Added only with `--character`. It is the default character for `chat_cli`. |

Characters can be the app's own export (`pgc_character_v1`), Tavern V1, or V2/V3 cards, as JSON or as PNG
(`chara` / `ccv3` chunks). The character id namespaces memory and profiles, just as it does in the app.

## Supported nodes

All 24 node types are supported: chat_input, system_prompt, chat_prompt, template, instruct_wrap,
generate, persona, memory_read, memory_write, profile, profile_update, stats, compare, every_n,
manual_trigger, json_extract, summarize, memory_search, **lorebook**, **rag_retrieve**, merge, regex,
condition and chat_output.
The semantics, defaults and skip rules are ported from `src/graph/nodes.ts` and `runner.ts`.
An unknown node type, a cycle or a malformed file fails at compile time with a clear message.

JavaScript regexes (Regex and Condition nodes) are translated to Python `re`. This covers flags
`gimsu`, named groups, and `$1`, `$&`, `$<name>` and `$$` in replacements. Lookbehind and other
features shared by JS and Python work. Exotic JS-only syntax (for example the `v` flag) does not.

## Lorebooks and documents (Lorebook / RAG Retrieve nodes)

Lorebooks and document collections live in the data dir (`pgc.lore.<charId|_global>.json` and `pgc.rag.<charId|_global>.json`).
They use the same format as the app, so you can copy them over. You can load them at compile time:

```
python graphc.py compile graph.json -o out/ --lore world.json --docs handbook.md --docs notes.txt
python graphc.py compile graph.json -o out/ --character card.png --char-lore extra.json --char-docs bio.md
```

| flag | what it does |
|---|---|
| `--lore` | imports SillyTavern world info, a V2 `character_book` or our export into the global book |
| `--docs` | chunks `.txt` / `.md` files into the global collection |
| `--chunk-size` / `--overlap` | control chunking (defaults 800 / 100 chars) |
| `--char-lore` / `--char-docs` | target the `--character`'s own namespace |

A card's `character_book` is imported into that character's lorebook automatically the first time it runs.

Later, you can manage the data dir with:

```
python -m pgc_runtime.manage --data out/pgc_data [--ns char-mira] import-lore world.json
python -m pgc_runtime.manage --data out/pgc_data import-doc rules.md --size 600 --overlap 80
python -m pgc_runtime.manage --data out/pgc_data list
python -m pgc_runtime.manage --data out/pgc_data embed --url http://localhost:5001 [--fresh]
python -m pgc_runtime.manage --data out/pgc_data export-lore world.json
```

RAG uses KoboldCPP `/v1/embeddings`, which needs an embeddings model. Vectors are cached per chunk hash in
`pgc.emb.*`. If embeddings fail, retrieval falls back to BM25 keyword search, as it does in the app.

## Knowledge packs (`--pack`)

Knowledge packs are read-only lore sets, such as the Skyrim Oghma Infinium catalog. They feed the Lorebook and RAG Retrieve nodes the same way they do in the app's 📚 Knowledge → Packs. The node options are `packs` (`all enabled` / `none` / `selected` + `packIds`) and `knowledge` (`basic` / `advanced`). A pack that is off contributes nothing.

Build the Oghma pack from the CHIM / HerikaServer catalog:

```
python tools/oghma_to_pack.py resources/oghma/skyrim-official/catalogs/<version>/articles.json -o skyrim-oghma-v2.pack.json
python tools/oghma_to_pack.py articles.json --license LICENSE -o skyrim-oghma-v2.pack.zip     # zip = pack.json + LICENSE + NOTICE
```

The catalog is **GPL-3.0** (the HerikaServer license). The pack carries the license text and attribution.
This repo ships **only the converter**, not the catalog data. Generate the pack yourself and keep the license with it if you share it.

Use it:

```
python graphc.py compile graph.json -o out/ --pack skyrim-oghma-v2.pack.zip [--pack-off skyrim-oghma-v2]
python -m pgc_runtime.manage --data out/pgc_data packs                                # list, categories, counts
python -m pgc_runtime.manage --data out/pgc_data pack skyrim-oghma-v2 off             # global toggle (on|off)
python -m pgc_runtime.manage --data out/pgc_data pack skyrim-oghma-v2 cat esoteric off   # category ("cat all on")
python -m pgc_runtime.manage --data out/pgc_data --ns char-mira pack skyrim-oghma-v2 on  # character override (inherit|on|off)
python -m pgc_runtime.manage --data out/pgc_data embed-pack skyrim-oghma-v2 --url http://localhost:5001
python -m pgc_runtime.manage --data out/pgc_data install-pack other.pack.json / uninstall-pack <id>
```

Keys match the app's: `pgc.pack.<id>` (pack data), `pgc.packs.settings`, `pgc.packs.char.<charId>` and `pgc.packemb.<id>` (vectors by article checksum).

## Tests

```
python tests/run_tests.py        # compile all examples, run them, compare with the app's runner
python tests/test_character.py   # character cards + chat_cli end to end
python tests/test_packs.py       # knowledge packs: converter, on/off rules, lazy embeddings, manage CLI, app parity
python tests/test_knowledge.py   # lorebook + RAG units, and chunking/parsing/matching parity with the app's TS
```

`tests/fake_kobold.py` is a deterministic fake KoboldCPP. Its replies depend only on the prompt,
and it includes leaked `User:` lines, `<think>` blocks and JSON with trailing commas. `run_tests.py`
does three things. It compiles every graph in `examples/`: the app's six built-in examples (including Lorebook + RAG), an
emotion-assessor graph, and `all-nodes.json`, which uses every node type. Both runners start from the same seeded
lorebooks and document collections (`tests/fixtures`). It runs a 10-step scenario
(chat, regenerate, manual trigger, undo) with and without a character. Then it runs the same scenario
through the **app's own `runGraph`**, bundled with esbuild and run in node with fetch pointed at the
fake server. For each graph it compares the replies, every request body sent to KoboldCPP and the
final storage contents (ids and timestamps normalized). Set `APP_DIR` to the app checkout, which
needs `node_modules`. Without node, the parity step is skipped.

## TypeScript target

TODO: `--target ts` is not implemented yet and exits with an error. The plan is to emit the same
block structure against the app's own `src/graph` modules.

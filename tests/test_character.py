"""Character loading (app export, Tavern V1/V2 JSON, PNG card) + chat_cli smoke test with on-disk storage."""
import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from pgc_runtime import character as ch  # noqa: E402


def png_with(key, text):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", key.encode() + b"\0" + text.encode()) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def main():
    fails = 0
    v2 = {"spec": "chara_card_v2", "data": {"name": "Seraphina", "description": "A guardian of {{user}}.", "personality": "kind", "scenario": "forest",
                                            "first_mes": "Hello <USER>!", "mes_example": "<START>\n{{char}}: hi", "system_prompt": ""}}
    c = ch.from_json(v2)
    exp = "A guardian of {{user}}.\n\nPersonality: kind\n\nScenario: forest"
    ok = c["name"] == "Seraphina" and c["system"] == exp and c["id"] == "char-seraphina"
    print(f"[{'ok' if ok else 'FAIL'}] Tavern V2 JSON"); fails += not ok
    v1 = ch.from_json({"name": "Old", "char_persona": "grumpy", "char_greeting": "Hm.", "example_dialogue": ""})
    ok = v1["system"] == "grumpy" and v1["greeting"] == "Hm."
    print(f"[{'ok' if ok else 'FAIL'}] Tavern V1 JSON"); fails += not ok
    app = ch.from_json({"spec": "pgc_character_v1", "id": "char-abc", "name": "Mira", "system": "You are {{char}}.", "greeting": "", "examples": ""})
    ok = app["id"] == "char-abc" and ch.system_prompt(app, "Bob") == "You are Mira."
    print(f"[{'ok' if ok else 'FAIL'}] app export JSON"); fails += not ok
    d = tempfile.mkdtemp()
    png = os.path.join(d, "card.png")
    open(png, "wb").write(png_with("chara", base64.b64encode(json.dumps(v2).encode()).decode()))
    c2 = ch.load(png)
    ok = c2 == c and ch.system_prompt(c2, "Bob").startswith("A guardian of Bob.") and "[Example dialogue]\n<START>\nSeraphina: hi" in ch.system_prompt(c2, "Bob")
    print(f"[{'ok' if ok else 'FAIL'}] PNG card (tEXt chara)"); fails += not ok

    # chat_cli: compile with a bundled PNG card, chat through the fake server, /regen + /undo, data on disk
    import fake_kobold, graphc
    port = 5198
    srv = fake_kobold.serve(port)
    out = os.path.join(d, "out")
    graphc.main(["compile", os.path.join(ROOT, "examples", "chat-with-memory-stream.json"), "-o", out, "--character", png])
    stdin = "remember I like tea\n/regen\nhi\n/undo\nnote that I hate rain\n/quit\n"
    r = subprocess.run([sys.executable, "chat_cli.py", "--url", f"http://127.0.0.1:{port}", "--data", "store"], cwd=out,
                       input=stdin, capture_output=True, text=True, timeout=60, encoding="utf-8")
    files = sorted(os.listdir(os.path.join(out, "store")))
    notes = json.load(open(os.path.join(out, "store", next(f for f in files if "notes" in f)), encoding="utf-8"))
    texts = [n["text"] for n in json.loads(notes)] if isinstance(notes, str) else [n["text"] for n in notes]
    ok = r.returncode == 0 and "Hello User!" in r.stdout and "leaked" not in r.stdout and texts == ["remember I like tea", "note that I hate rain"]
    print(f"[{'ok' if ok else 'FAIL'}] chat_cli (/regen /undo, PNG character, JSON dir storage) files={files} notes={texts}")
    if not ok:
        print(r.stdout[-1500:], r.stderr[-1500:])
    fails += not ok
    srv.shutdown()
    print("ALL PASSED" if not fails else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

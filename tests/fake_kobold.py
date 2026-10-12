"""Deterministic fake KoboldCPP for tests. Replies depend only on the prompt, so two runners that send
the same prompts get the same replies. Logs every request (GET /_log, POST /_reset).

    python tests/fake_kobold.py 5199
"""
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []
LOCK = threading.Lock()


def h(s, n=6):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def reply_for(prompt):
    k = h(prompt)
    if "character state profile" in prompt:
        n = int(k, 16)
        return '<think>weighing it</think>```json\n{"mood": "%s", "trust": {"player": "+%d"}, "inventory": "+item%d", "name": "Hacker", "bogus": 1,}\n```' % (
            ["happy", "wary", "calm"][n % 3], n % 7 + 1, n % 4)
    if "Summarize" in prompt or "summarize" in prompt:
        return "Summary %s: the user and the character talked." % k
    if "Emotion" in prompt or "emotion" in prompt:
        return '{"emotion": "curious", "intensity": %d, "note": "id %s"}' % (int(k, 16) % 10, k)
    # normal reply with a leaked next user turn the runtime must cut, and trailing whitespace
    return "Reply %s. Ünïcode ✓ ok.  \nUser: this leaked line must be cut" % k


def embed(text):
    v = [0.0] * 16
    for w in text.lower().split():
        v[int(h(w, 4), 16) % 16] += 1.0
    return v


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/_log":
            with LOCK:
                return self._json(200, LOG)
        if self.path == "/api/v1/model":
            return self._json(200, {"result": "fake/model"})
        self._json(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        p = self.path
        if p == "/_reset":
            with LOCK:
                LOG.clear()
            return self._json(200, {})
        if p in ("/api/v1/generate", "/api/extra/generate/stream"):
            with LOCK:
                LOG.append({"path": p, "body": body})
            text = reply_for(body.get("prompt", ""))
            if p == "/api/v1/generate":
                return self._json(200, {"results": [{"text": text}]})
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for i in range(0, len(text), 7):
                self.wfile.write(("event: message\ndata: " + json.dumps({"token": text[i:i + 7]}) + "\n\n").encode()); self.wfile.flush()
            return
        if p == "/api/extra/tokencount":
            return self._json(200, {"value": len(body.get("prompt", "")) // 4})
        if p == "/v1/embeddings":
            with LOCK:
                LOG.append({"path": p, "body": body})
            return self._json(200, {"data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(body["input"])]})
        self._json(404, {"error": "unknown " + p})


def serve(port):
    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 5199)
    threading.Event().wait()

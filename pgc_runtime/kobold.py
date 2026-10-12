"""Minimal KoboldCPP client (stdlib urllib): generate, SSE stream, tokencount, embeddings."""
import json
import urllib.error
import urllib.request
from .util import json_stringify


class KoboldError(Exception):
    pass


def _req(base, path, body=None, timeout=600):
    url = base.rstrip("/") + path
    data = None if body is None else json_stringify(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json", "accept": "*/*"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        msg = f"Kobold returned HTTP {e.code}"
        try:
            j = json.loads(e.read().decode("utf-8"))
            det = j.get("detail") if isinstance(j.get("detail"), dict) else {}
            msg = j.get("message") or det.get("msg") or j.get("error") or msg
        except Exception:
            pass
        raise KoboldError(msg or f"Kobold returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise KoboldError(f"Could not reach KoboldCPP at {base} ({e.reason}). Is it running?") from None


def get_model(base):
    j = json.load(_req(base, "/api/v1/model"))
    return j.get("result") or json.dumps(j)


def token_count(base, prompt):
    j = json.load(_req(base, "/api/extra/tokencount", {"prompt": prompt}))
    if not isinstance(j.get("value"), (int, float)):
        raise KoboldError("Bad tokencount response")
    return int(j["value"])


def generate(base, body):
    j = json.load(_req(base, "/api/v1/generate", body))
    return ((j.get("results") or [{}])[0] or {}).get("text", "")


def generate_stream(base, body, on_token=None):
    """SSE streaming; calls on_token per token and returns the full text."""
    r = _req(base, "/api/extra/generate/stream", body)
    import codecs
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    full, got_any, buf = "", False, ""
    for raw in r:
        buf += dec.decode(raw)
        while "\n\n" in buf or "\r\n\r\n" in buf:
            sep = "\r\n\r\n" if "\r\n\r\n" in buf and (buf.find("\r\n\r\n") < buf.find("\n\n") or "\n\n" not in buf) else "\n\n"
            ev, buf = buf.split(sep, 1)
            for line in ev.splitlines():
                if line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(d.get("token"), str):
                        got_any = True; full += d["token"]
                        if on_token:
                            on_token(d["token"])
    if not got_any and buf.strip():
        for line in buf.splitlines():
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                    if isinstance(d.get("token"), str):
                        full += d["token"]; got_any = True
                        if on_token:
                            on_token(d["token"])
                except ValueError:
                    pass
    return full


def embeddings(base, inputs):
    j = json.load(_req(base, "/v1/embeddings", {"model": "kcpp", "input": inputs}))
    data = j.get("data") or []
    if len(data) != len(inputs) or not all(isinstance(d.get("embedding"), list) for d in data):
        raise KoboldError("Embeddings response malformed")
    return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]

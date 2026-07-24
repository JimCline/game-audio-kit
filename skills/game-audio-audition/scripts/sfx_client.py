#!/usr/bin/env python3
"""Batch client for the local sfx-gen MCP daemon (Stable Audio Open).

Usage: python3 sfx_client.py jobs.json
jobs.json: [{"name": "laser", "prompt": "...", "duration": 0.6,
             "output_dir": "/abs/path", "seed": 123 (optional)}, ...]

Drives http://127.0.0.1:8756/mcp directly (works even when the session's MCP
connection failed at startup). Prints one line per job with the saved path and
seed; exits non-zero if any job failed. Normalizes the server's double-.wav
filename quirk so the file always lands at <output_dir>/<name>.wav.
"""
import json, os, sys, urllib.request

BASE = os.environ.get("SFX_GEN_URL", "http://127.0.0.1:8756/mcp")
session = None


def rpc(method, params, rid=None, timeout=900):
    global session
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if rid is not None:
        body["id"] = rid
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 **({"mcp-session-id": session} if session else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        sid = r.headers.get("mcp-session-id")
        if sid:
            session = sid
        if rid is None:
            return None
        payload = r.read().decode()
    result = None
    for line in payload.splitlines():
        if line.startswith("data: "):
            msg = json.loads(line[6:])
            if msg.get("id") == rid:
                result = msg
    return result


def main():
    jobs = json.load(open(sys.argv[1]))
    rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "sfx-audition", "version": "1.0"}}, rid=1)
    rpc("notifications/initialized", {})
    print(f"session: {session}", flush=True)

    fail = 0
    for i, job in enumerate(jobs):
        name, outdir = job["name"], job["output_dir"]
        os.makedirs(outdir, exist_ok=True)
        print(f"[{i + 1}/{len(jobs)}] {name} ...", flush=True)
        args = {"prompt": job["prompt"], "duration_seconds": job["duration"],
                "output_dir": outdir, "filename": name}
        if "seed" in job:
            args["seed"] = job["seed"]
        try:
            resp = rpc("tools/call", {"name": "generate_sfx", "arguments": args},
                       rid=100 + i)
            res = resp.get("result", {})
            text = "".join(c.get("text", "") for c in res.get("content", []))
            if res.get("isError"):
                print(f"  ERROR: {text[:400]}", flush=True)
                fail += 1
                continue
            info = json.loads(text)
            target = os.path.join(outdir, name + ".wav")
            files = info.get("files", [])
            if files and os.path.abspath(files[0]) != target:
                os.replace(files[0], target)
            print(f"  ok: {target} seed={info.get('seed')}", flush=True)
        except Exception as e:
            print(f"  EXCEPTION: {e}", flush=True)
            fail += 1
    print(f"done ok={len(jobs) - fail} fail={fail}", flush=True)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

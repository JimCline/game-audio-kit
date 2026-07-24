# sfx-gen-mcp

An [MCP](https://modelcontextprotocol.io) server that lets LLM agents (Claude
Code, etc.) generate game sound effects **locally** with Stability AI's
[Stable Audio Open](https://huggingface.co/stabilityai/stable-audio-open-1.0).
No API keys, no per-generation cost — your agent asks for "a coin pickup
sound in assets/sounds/" and gets a .wav file.

## Tools

- **`generate_sfx`** — text prompt → .wav file(s). Parameters: `prompt`,
  `duration_seconds` (0.5–47), `steps`, `cfg_scale`, `seed`,
  `negative_prompt`, `variations` (1–4 takes per call), `output_dir`,
  `filename`. Returns JSON with saved file paths and the seed used (so a
  liked sound can be reproduced or varied).
- **`sfx_server_status`** — model/device/load state.

## Requirements

- Python 3.10+
- ~10 GB disk for model weights, ~12 GB RAM while generating
- GPU strongly recommended: CUDA or Apple Silicon (MPS is supported and
  patched at runtime — upstream stable-audio-tools uses float64 which MPS
  lacks)
- Hugging Face access to the **gated** model: accept the license at
  [stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
  then `hf auth login`

## Install

```sh
pip install sfx-gen-mcp        # or: uv tool install sfx-gen-mcp
```

## Run

Two modes:

**Shared daemon (recommended)** — one resident model serves every client;
sessions connect over streamable HTTP and skip the per-session model load:

```sh
PYTORCH_ENABLE_MPS_FALLBACK=1 sfx-gen-mcp --transport http --port 8756
```

```sh
claude mcp add --transport http --scope user sfx-gen http://127.0.0.1:8756/mcp
```

**Per-session (stdio)** — simplest, but each client process loads its own
copy of the model:

```sh
claude mcp add --scope user --env PYTORCH_ENABLE_MPS_FALLBACK=1 -- sfx-gen sfx-gen-mcp
```

Concurrent requests to the daemon are serialized with a lock — a second
client queues instead of contending for the GPU.

The model lazy-loads on the first `generate_sfx` call (~20–40s), then stays
resident. On an Apple M-series GPU a 50-step clip takes roughly 15–30s.

### Env vars

| Variable           | Meaning                                              |
| ------------------ | ---------------------------------------------------- |
| `SFX_MODEL`        | HF model name (default `stabilityai/stable-audio-open-1.0`) |
| `SFX_OUTPUT_DIR`   | Default output directory (default `<cwd>/sfx-output`) |
| `SFX_IDLE_TIMEOUT` | Seconds of inactivity before the server exits to free model memory (default 1800; 0 disables) |

### Idle shutdown

Once the model has been loaded, the server exits after `SFX_IDLE_TIMEOUT`
seconds (default 30 min) without a generation, releasing the ~10 GB of model
memory. Run it under a supervisor that restarts it (launchd `KeepAlive`,
systemd `Restart=always`, docker `--restart`) and it respawns instantly as a
small model-free listener; the next `generate_sfx` call reloads the model.
A server that has never loaded the model never exits.

### macOS: run at login

See [`launchd/com.sfx-gen-mcp.plist`](launchd/com.sfx-gen-mcp.plist) for a
LaunchAgent template: copy to `~/Library/LaunchAgents/`, adjust paths, then
`launchctl load ~/Library/LaunchAgents/com.sfx-gen-mcp.plist`. Idle memory
is small — the model only loads when the first generation is requested.

## Prompting tips

Concrete, physical descriptions work best:

- `"sword clashing against metal shield, sharp ring"` not `"battle sound"`
- `"footsteps on gravel, slow walking pace"` not `"walking"`
- Use `negative_prompt: "music, voices"` to keep ambiences clean
- Impacts: 1–2s. UI blips: ~1s. Ambient loops: 10–30s.

## License

MIT for this server. Model weights are governed by the
[Stability AI Community License](https://huggingface.co/stabilityai/stable-audio-open-1.0);
outputs are usable in commercial projects for organizations under $1M
annual revenue — review the license for your situation.

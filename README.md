# game-audio-kit

A complete, self-hosted audio pipeline for LLM-assisted game development:
**sound effects generated locally on your own GPU, music and voices from the
Gemini API, and an agent skill that turns it all into an iterative
listening-booth workflow** inside [Claude Code](https://claude.com/claude-code).

You say *"my tower-defense game needs a coin pickup sound, a build-phase music
bed, and an announcer"* — and the agent generates candidates, publishes an A/B
audition page where you click through variants, re-rolls the losers, and wires
the winners into your project.

> **Status: experimental proof of concept.** This grew out of one developer's
> setup and is shared so others can study and adapt it — expect rough edges,
> read the scripts before running them, and treat it as a starting point, not
> a product.

## Why this exists

This was born out of needing a comprehensive way for an LLM coding agent to
provide **sound effects, music, and voices** for games. Asset store searching
breaks flow; commissioning audio is overkill for prototypes; and generation
APIs alone don't solve the real problem, which is *iteration* — you don't know
if a coin sound is right until you hear it next to nine alternatives, in
context, and then want "the third one but shorter."

The answer turned out to be three cooperating pieces:

1. a **local, free, unlimited SFX generator** (short sounds are where you
   iterate hardest, so per-generation API cost hurts most there),
2. a **hosted music + voice service** (long-form music and natural TTS are
   beyond what fits comfortably on a laptop GPU), and
3. an **agent skill** that orchestrates both into a human-in-the-loop
   audition workflow, because the bottleneck was never generation — it was
   *choosing*.

It has been used to score and sound-design Godot 4.x games (developed against
Godot 4.7), but nothing in it is Godot-specific except the final wiring step —
it adapts easily to Unity, Bevy, web games, film pre-viz, or anything else
that consumes audio files.

## What's in the box

```
                        ┌────────────────────────────┐
                        │   Claude Code (the agent)  │
                        │  + game-audio-audition     │
                        │    skill (the workflow)    │
                        └─────┬───────────────┬──────┘
                     MCP over │               │ MCP over
                     HTTP     │               │ stdio
                ┌─────────────▼───┐     ┌─────▼──────────────┐
                │   sfx-gen-mcp   │     │  gemini-media-mcp  │
                │  local daemon   │     │    (Go binary)     │
                │  port 8756      │     │                    │
                │                 │     │  Gemini API ──────►│ music (Lyria)
                │  Stable Audio   │     │  (paid, needs key) │ voices (TTS)
                │  Open 1.0 on    │     └────────────────────┘
                │  your GPU       │
                └─────────────────┘
```

| Piece | What | Cost | Where it runs |
|---|---|---|---|
| [sfx-gen-mcp](https://github.com/JimCline/sfx-gen-mcp) | Sound effects & foley | Free, unlimited | Your GPU (Apple Silicon or CUDA) |
| [gemini-media-mcp](https://github.com/mordor-forge/gemini-media-mcp) | Music & voice lines | Paid Gemini API | Google's servers, local Go binary as the bridge |
| [`skills/game-audio-audition/`](skills/game-audio-audition/) | The audition workflow | — | Inside Claude Code |

## How each piece works

### 1. [sfx-gen-mcp](https://github.com/JimCline/sfx-gen-mcp) — local sound effect generation

A small (~300 line) Python [MCP](https://modelcontextprotocol.io) server
(its own repo — usable standalone with any MCP client) that
wraps Stability AI's [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
a latent-diffusion model trained for exactly this niche: short sounds, foley,
and ambiences up to 47 seconds. The agent calls one tool:

```
generate_sfx(prompt, duration_seconds, steps, cfg_scale, seed,
             negative_prompt, variations, output_dir, filename)
```

and gets back .wav file paths plus the seed used — so a sound you liked can be
reproduced exactly or varied (same prompt, new seed = a "sibling take").

Design decisions worth studying:

- **Shared daemon over HTTP, not per-session stdio.** The model is ~10 GB in
  memory and takes 20–40 s to load. Running one resident daemon
  (`--transport http`, port 8756) means every Claude session, terminal, and
  script shares one loaded model. A lock serializes generations so concurrent
  clients queue instead of fighting over the GPU.
- **Lazy load + idle suicide.** The server starts as a tiny model-free
  listener; the model loads on the first `generate_sfx` call. After 30 min
  idle (configurable via `SFX_IDLE_TIMEOUT`) the process *exits* to free the
  ~10 GB, and the supervisor (launchd `KeepAlive` / systemd `Restart=always`)
  instantly respawns the small listener. You get a permanently-available
  service that costs ~nothing when you're not making sounds.
- **MPS patch for Apple Silicon.** Upstream `stable-audio-tools` uses float64
  inside its APG projection, which Apple's MPS backend lacks. The server
  monkey-patches that one method with a float32 fallback on MPS
  ([`server.py`](https://github.com/JimCline/sfx-gen-mcp/blob/main/src/sfx_gen_mcp/server.py), `_patch_mps_float64`)
  — identical math elsewhere. On an M-series GPU a 50-step clip takes
  ~15–30 s.
- **stdout hygiene.** On stdio transport, stdout carries the MCP protocol —
  the model libraries' prints are redirected to stderr so they can't corrupt
  it.

Prompting the model: concrete physical descriptions win. *"sword clashing
against metal shield, sharp ring"* beats *"battle sound"*; impacts want 1–2 s,
UI blips ~1 s, ambient loops 10–30 s; `negative_prompt: "music, voices"`
keeps ambiences clean.

### 2. gemini-media-mcp — music and voices

Where the local model tops out (long-form structured music, natural speech),
the kit hands off to
[gemini-media-mcp](https://github.com/mordor-forge/gemini-media-mcp) — a
single-binary Go MCP server (Apache-2.0, installed from its GitHub releases)
that bridges the agent to Google's Gemini media APIs:

- **`generate_music`** — Google's **Lyria** model. Prompts can specify genre,
  instrumentation, BPM, key, mood, and structure tags; `model: "clip"` gives
  a 30 s piece (right for audition loops), `"full"` up to ~3 minutes.
- **`generate_audio`** — **Gemini TTS** for voice lines. The prompt carries
  delivery direction ("gravelly, urgent, like a sports announcer"), and
  `voiceName` selects among prebuilt voices (Aoede, Kore, Puck, …) — casting
  an announcer means generating the same line across candidate voices and
  listening.

It talks stdio MCP: Claude Code spawns the binary per session, the binary
calls Google, files land in `MEDIA_OUTPUT_DIR`. This is the one **paid** part
of the kit — it needs a `GEMINI_API_KEY`
([get one here](https://aistudio.google.com/apikey)); music/TTS calls are
billed by Google. The complementary split is deliberate: iterate freely and
endlessly on SFX locally at zero cost, spend API money only on the fewer,
longer music/voice generations.

(The server exposes image/video tools too — the kit only wires up audio, but
they come along for free.)

### 3. game-audio-audition — the workflow skill

A [Claude Code skill](skills/game-audio-audition/SKILL.md) (a markdown
playbook plus helper scripts — no code runs until the agent follows it) that
turns the two generators into an iterative audition loop:

1. **Inventory** — the agent reads your game for audio keys and play-sites:
   existing files, procedural synths (dumped to .wav via headless Godot),
   music slots, and the announcer line list. Missing audio is still a row.
2. **Generate candidates** — SFX via sfx-gen (seeds logged for
   reproducibility), music via Lyria, voices via TTS.
3. **Publish the listening booth** — [`build_audition.py`](skills/game-audio-audition/scripts/build_audition.py)
   compiles a manifest into a single self-contained HTML page (all audio
   embedded as data URIs, long tracks compressed) published as a private
   artifact. Each sound key is a row of playable cards: the **current**
   sound, the **pick**, and **challengers**, color-coded. You play, click
   your picks, hit *Copy selections*, and paste the result back to the agent.
4. **Iterate** — losers get re-rolled *with a genuinely different angle* (a
   new source metaphor for an SFX, a different genre for a track, a different
   voice for a line — not a reworded prompt); "I like it but shorter" runs
   [`tighten.py`](skills/game-audio-audition/scripts/tighten.py); picked audio
   is immutable and the page republishes to the same URL each round.
5. **Finalize** — winners are converted (SFX → mono 16-bit WAV, music →
   Ogg Vorbis with loop flags, voices optionally radio-processed), copied
   into the project, playback code is rewired, and the import is smoke-tested.

The skill also includes [`sfx_client.py`](skills/game-audio-audition/scripts/sfx_client.py),
a raw-HTTP batch client for the sfx daemon — for big batches the agent runs
generation as a background job instead of blocking on tool calls, and it
works even when the MCP connection isn't up. [`REFERENCE.md`](skills/game-audio-audition/REFERENCE.md)
carries the prompt-craft notes and daemon ops runbook.

The finalize step is written for **Godot 4.x** (developed on 4.7): `res://`
paths, `.import` flags, headless import smoke-tests. That's one section of
one markdown file — retargeting the skill to Unity or anything else is an
edit, not a rewrite.

## Requirements

- **macOS on Apple Silicon** (primary target; launchd daemon, MPS) or
  **Linux with CUDA** (systemd user service). CPU-only works but is slow.
- ~10 GB disk for model weights, ~12 GB RAM while generating
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/)
- [Claude Code](https://claude.com/claude-code) (the CLI registers the MCP
  servers; other MCP clients work with manual config)
- `ffmpeg` (audition page embedding, tightening, format conversion)
- A [Hugging Face account](https://huggingface.co) — the SFX model weights
  are **gated**: accept the license on the
  [model page](https://huggingface.co/stabilityai/stable-audio-open-1.0),
  then `hf auth login`
- Optional: a [Gemini API key](https://aistudio.google.com/apikey) for music
  and voices (paid)

## Install

```sh
git clone https://github.com/JimCline/game-audio-kit
cd game-audio-kit
./install.sh              # or: ./install.sh --skip-gemini  for SFX-only
```

The installer:

1. installs [`sfx-gen-mcp`](https://github.com/JimCline/sfx-gen-mcp) as a
   `uv` tool from its own repo and sets up the supervised daemon
   (launchd on macOS, systemd user service on Linux) on port **8756**;
2. downloads the prebuilt `gemini-media-mcp` binary for your platform from
   its GitHub releases and prompts for your `GEMINI_API_KEY` (Enter to skip)
   — on macOS the key goes into the **Keychain** and a small wrapper injects
   it at server launch, so it never sits in a plaintext config file;
3. copies the `game-audio-audition` skill into `~/.claude/skills/`;
4. registers both MCP servers with Claude Code at user scope.

Then restart any Claude Code session and try:

> "Generate a retro coin pickup sound, 3 variations"

The first generation downloads the model weights (~10 GB, one time) and takes
a few minutes; after that clips take ~15–30 s. In a game project, try:

> "Run a sound audition for this game"

## Uninstall

```sh
./uninstall.sh            # or --yes for no prompts
```

Removes the daemon, the tools, the skill, and the MCP registrations. It
deliberately leaves the model weights (`~/.cache/huggingface/…`, ~10 GB) and
your generated audio in place, and prints where they are.

## Adapting it

- **Different engine** — edit the *Finalize* section of
  `skills/game-audio-audition/SKILL.md`; everything upstream of it is
  engine-agnostic.
- **Different SFX model** — `SFX_MODEL` env var takes any
  `stable-audio-tools`-compatible checkpoint.
- **Different MCP client** — both servers are plain MCP (one streamable
  HTTP, one stdio); point any client at them.
- **No Claude Code at all** — `sfx_client.py` shows the daemon's raw HTTP
  protocol; the audition page builder is a standalone Python script.

## Licenses

- This kit: **MIT** ([LICENSE](LICENSE))
- [`sfx-gen-mcp`](https://github.com/JimCline/sfx-gen-mcp): **MIT** (own repo)
- `gemini-media-mcp`: **Apache-2.0** (upstream repo; installed as a binary,
  not vendored)
- **Stable Audio Open 1.0 weights**: [Stability AI Community License](https://huggingface.co/stabilityai/stable-audio-open-1.0)
  — free including commercial use for organizations under $1M annual
  revenue; review it for your situation. Generated outputs are yours per
  that license.
- Gemini API outputs: governed by [Google's terms](https://ai.google.dev/gemini-api/terms).

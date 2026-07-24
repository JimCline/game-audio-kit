---
name: game-audio-audition
description: Run an iterative audio audition for a Godot game covering sound effects, music, and voice lines — generate candidates (SFX via the local sfx-gen MCP, music/voice via the gemini-media MCP), publish an A/B listening-booth artifact where the user plays variants and copies their picks, iterate rounds, then wire the winners into the game. Use when the user wants to replace/audition/compare game sounds, generate SFX/music/voice candidates, add a soundtrack or announcer voice, or asks for a "sound audition page".
---

# Game Audio Audition

Iterative loop: extract the game's current audio → generate candidates →
publish an audition artifact → user pastes picks back → re-roll losers /
tighten winners → wire final picks into the game. Same booth for all three
audio types; they differ only in generator and prompts.

## Tools

- **SFX — sfx-gen MCP** (local Stable Audio daemon, `http://127.0.0.1:8756/mcp`):
  `mcp__sfx-gen__generate_sfx` (prompt, duration_seconds, seed, variations,
  output_dir, filename), `mcp__sfx-gen__sfx_server_status`. Load via ToolSearch
  if deferred. If the tools are absent (MCP connect failed at session start) or
  the daemon is wedged, see [REFERENCE.md](REFERENCE.md) "Daemon ops", then
  batch over raw HTTP with `scripts/sfx_client.py`. For batches (>3 sounds)
  prefer the script in a background Bash task; each generation blocks ~30–60s.
- **Music — `mcp__gemini-media__generate_music`** (Lyria): prompt supports
  genre, instruments, BPM, key, mood, structure tags; `model: "clip"` = 30s
  (right for audition loops), `"full"` = up to 3min.
- **Voice — `mcp__gemini-media__generate_audio`** (Gemini TTS): `prompt`
  (include delivery directions), `voiceName` (e.g. Aoede, Kore, Puck),
  `languageCode`. Compare voices by generating the same line per candidate voice.
- **Publishing — the Artifact tool** (load the `artifact-design` skill before
  first publish).
- **Godot — `mcp__godot__*`** if connected, or `godot --headless` via Bash.
- `ffmpeg` required (page embedding, tightening, VO processing, ogg encoding).
- Work in the session scratchpad; keep a `picks.txt` state file there.

## Round 1

1. **Inventory the game's audio.** SFX keys + play-sites and current files
   (WAV assets → copy; procedural synths → dump via a temp headless-Godot
   script, see REFERENCE.md). Music: existing tracks and the slots the game
   needs (build-phase bed, combat layer, stingers). Voice: the line list
   (announcer/UI callouts) with exact text. Missing audio is still a row —
   baseline card is just absent.
2. **Generate candidates** per the Tools section. Keep logs (SFX seeds make
   candidates reproducible). Prompt guidance: REFERENCE.md "Prompt craft".
3. **Build + publish the page.** Write a `manifest.json` (schema in
   `scripts/build_audition.py` docstring): per key a variant list —
   `channel: proc` (amber, current/baseline), `gen` (cyan, pick), `alt`
   (violet, challenger) — plus `defaults` and a `storage_key`. Then
   `python3 scripts/build_audition.py manifest.json page.html` and publish via
   the Artifact tool (same file path every round → stable URL). Long audio
   (music/VO > 8s) embeds compressed automatically; the play button
   pause-toggles for long tracks.
4. Tell the user: play ▶, click a card to pick (click again to clear), ✎ on a
   card to attach optional details to that specific option, then
   **Copy selections** and paste the result back.

## Later rounds

On pasted picks: record them in `picks.txt`, then per key. Indented
`  variantId: text` lines are the user's notes on *that specific variant* —
treat them as targeted direction (e.g. a note on a loser says how to re-roll
it; a note on the pick says what to preserve or tweak):

- **Picked + happy** → lock it; show only baseline + pick next round.
- **Picked "but shorter"** → `python3 scripts/tighten.py file.wav 0.6`
  (backs up `.orig.wav`; label the variant "· short").
- **UNDECIDED / direction note** → re-roll with a genuinely different angle
  (SFX: new source metaphor; music: different genre/instrumentation; voice:
  different voiceName or delivery direction), not a reworded prompt. Stubborn
  keys get 2 candidates at once.
- **Likes current-but-wants-variations** → synths: variant synth functions in
  a temp Godot script; generated SFX: same prompt, new seed (sibling take);
  music: same prompt with one axis changed (BPM, lead instrument).

Every round: **bump `storage_key`** (stale localStorage picks must never point
at replaced audio), bake the user's pasted picks into `defaults`, keep picked
audio immutable (promote files rather than overwrite), republish to the same
artifact URL.

## Finalize (only when the user says final)

1. Copy winners into the project: SFX → `res://assets/sfx/<key>.wav` (mono
   16-bit; ship the page's converted audio so shipped == auditioned). Music →
   `res://assets/music/<key>.ogg` (ffmpeg `-c:a libvorbis -q:a 5`; set the
   Godot import loop flag for beds). Voice → `res://assets/voice/<key>.wav`
   (optionally radio-process first, see REFERENCE.md).
2. Rewire playback: sound bank `load()`s WAV winners, keeps synth code for
   procedural winners; music through a dedicated looping AudioStreamPlayer /
   crossfade layer; voice lines through a queue so callouts don't overlap.
3. `godot --headless --import`, then smoke-test every key loads.
4. Flag loudness: generated audio is normalized hot; mixed sources need a
   listen in-game (per-key gain table beats re-editing files).

Details, prompt craft, daemon ops: [REFERENCE.md](REFERENCE.md)

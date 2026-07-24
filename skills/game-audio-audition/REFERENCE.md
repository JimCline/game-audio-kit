# Game Audio Audition — Reference

## Daemon ops (sfx-gen)

Local Stable Audio Open MCP server, repo `~/git/repos/audio-gen/sfx-gen-mcp`,
launchd agent `com.sfx-gen-mcp`, logs `~/Library/Logs/sfx-gen-mcp.log`.

- Health check: POST an MCP `initialize` to `http://127.0.0.1:8756/mcp` with
  `Accept: application/json, text/event-stream`. A wedged daemon accepts TCP
  but never responds.
- Restart: `launchctl kickstart -k gui/$UID/com.sfx-gen-mcp`, allow ~20s for
  uvicorn to come up. Model lazy-loads on the first `generate_sfx` (~30s),
  then generations run ~30–60s each, serialized by a lock (safe to queue a
  second batch while one runs).
- If the session's MCP connection failed at startup, the `mcp__sfx-gen__*`
  tools won't exist — `scripts/sfx_client.py` drives the daemon over raw HTTP
  and doesn't care.
- Quirk: the server appends `.wav` to `filename` even when present; the
  client script normalizes this.
- `generate_sfx` params worth knowing: `seed` (reproduce/vary a liked sound),
  `variations` (1–4 takes per call), `negative_prompt`, `steps`, `cfg_scale`.

## Music & voice (gemini-media MCP)

Music — `mcp__gemini-media__generate_music` (Lyria):

- Audition with `model: "clip"` (30s); generate `"full"` (up to 3min, supports
  [Verse]/[Chorus]/[Bridge] structure tags) only for locked winners.
- For a reactive game soundtrack, generate the layers as separate prompts
  sharing tempo and key ("ambient synth bed, 90 BPM, D minor, sparse, calm" /
  "driving percussion and bass layer, 90 BPM, D minor, tense") so the game can
  crossfade between them; add short stingers (wave cleared, defeat) the same way.
- Loop check before shipping: trim to a bar boundary with ffmpeg and listen to
  the seam; Godot's ogg import has a loop flag but the audio must actually meet.

Voice — `mcp__gemini-media__generate_audio` (Gemini TTS):

- Put delivery directions in the prompt ("calm clipped military announcer:
  'Wave inbound'"), pick voices via `voiceName` (Aoede, Kore, Puck, ...).
- Voice casting = one row per candidate voice speaking the same line; line
  audition = one row per line in the chosen voice.
- Batch all lines for a chosen voice in one round; keep exact line text in the
  manifest `role` field so the user reviews copy and read together.
- Radio/comms feel for sci-fi announcers, applied after picking:
  `ffmpeg -i in.wav -af "highpass=f=300,lowpass=f=3400,acompressor=ratio=4:threshold=-18dB,volume=4dB" out.wav`

## Prompt craft (SFX)

Anchor every prompt in three things: the physical source, the envelope, and
the game register.

- Name concrete sources and textures: "supersonic crack", "hydraulic hiss",
  "glassy resonant ping", "sub-bass thump", "crackling debris tail".
- State the envelope: "short and snappy", "rising sweep into release",
  "slow heavy collapse". Include "video game" / "sci-fi weapon" to steer away
  from field-recording ambience.
- Duration: request roughly the gameplay-appropriate length (0.5s minimum).
  Layered descriptions ("crack, then deep body, then whip tail") work well.
- Re-rolls for a rejected key need a **different angle** (new source
  metaphor), not adjectives bolted onto the failed prompt. Two candidates per
  stubborn key converges faster than one.
- Sibling take of a liked sound = same prompt, no seed (random). Exact
  reproduction = same prompt + logged seed.

## Page/manifest conventions

- Variant ids are the vocabulary of the copy-paste round trip (`proc`,
  `proc2`, `gen`, `gen2`, `alt1`…). Keep an id's meaning stable across rounds;
  explain new ids to the user when introduced.
- Show per row: baseline (`proc`, amber) + current pick + new challengers.
  Drop defeated variants to keep rows scannable; keep max 4 cards per row
  (that's the grid).
- `storage_key` MUST change every round. The page merges
  `localStorage[storage_key]` over `defaults`; an explicit deselect is stored
  as `null` so it doesn't resurrect a default.
- Trimmed/edited variants: same id, label suffix ("· short"); always keep the
  `.orig.wav` backup beside the edited file.
- Artifact: same html file path every round → same URL. Keep the favicon
  stable. Version labels like "round-3-challengers" help the artifact history.

## Godot extraction & wiring

Dump procedural synths (temp script, run from the project dir, delete after):

```gdscript
extends SceneTree
func _init() -> void:
    var bank = load("res://scripts/autoload/sound_bank.gd").new()
    var out: String = OS.get_environment("SFX_OUT")
    DirAccess.make_dir_recursive_absolute(out)
    for k in ["laser", "boom"]:  # the bank's keys
        var s: AudioStreamWAV = bank.call("_synth_" + k)
        print(k, " -> ", s.save_to_wav(out.path_join(k + ".wav")))
    bank.free()
    quit()
```

`SFX_OUT=<scratchpad>/current godot --headless --script res://dump_tmp.gd`

Wiring pattern for the final sound bank: a `WAV_KEYS` const loaded from
`res://assets/sfx/%s.wav` in `_ready()`, synth functions kept only for
procedural winners. Then `godot --headless --import` and a smoke-test script
asserting every key's stream is a non-null AudioStream.

Loudness: generated files are normalized near 0dBFS; procedural synths often
peak at 0.5–0.9. If the mix feels uneven in-game, add a per-key gain table
consulted in `play()` rather than editing files.

## State to keep in the scratchpad

- `picks.txt` — per-key pick + provenance (take numbers, prompt angle, trims)
  updated every round; this is what survives context compaction.
- `gen*.log` — client logs with seeds.
- Update project memory at round boundaries (verdicts, open questions, final
  outcome).

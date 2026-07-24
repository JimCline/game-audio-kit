#!/usr/bin/env python3
"""Build a self-contained audio audition HTML page from a manifest.

Usage: python3 build_audition.py manifest.json out.html

manifest.json:
{
  "title": "MYGAME",                       # header: "MYGAME / <subtitle>"
  "subtitle": "Audio Audition · Round 1",  # optional
  "storage_key": "mygame-audio-r1",        # BUMP EVERY ROUND (stale saved picks
                                           #  must not point at replaced audio)
  "defaults": {"laser": "gen"},            # pre-selected pick per key (optional)
  "sounds": [
    {"key": "laser", "title": "Laser shot", "role": "main fire sound",
     "variants": [
       {"id": "proc", "label": "A · current", "channel": "proc", "path": "/abs/laser_current.wav"},
       {"id": "gen",  "label": "B · gen",     "channel": "gen",  "path": "/abs/laser_gen.wav"},
       {"id": "gen2", "label": "C · gen alt", "channel": "alt",  "path": "/abs/laser_alt.wav"}
     ]}
  ]
}

Works for SFX, music tracks, and voice lines: any input format ffmpeg can read.
Short clips (<= 8s) embed as mono 16-bit WAV data URIs; longer audio (music,
long VO) embeds as mono 128k MP3 to keep the page publishable. channel picks
the card color: proc=amber (current/baseline), gen=cyan (leading candidate),
alt=violet (challenger). Selections persist in localStorage under storage_key
(per-variant notes under storage_key + "-notes"). Each card has a ✎ button that
opens an optional details box for that variant. The Copy button emits
"key: variantId" lines, with any variant notes as indented "  variantId: text"
lines beneath their key, to paste back to the agent.
"""
import base64, hashlib, json, os, shutil, subprocess, sys, tempfile

MP3_THRESHOLD_S = 8.0


def probe_dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip())


def load(path, workdir):
    """Return (dataURI, duration). Normalizes to mono; long audio becomes MP3."""
    dur = probe_dur(path)
    tag = hashlib.sha1(path.encode()).hexdigest()[:8]
    if dur > MP3_THRESHOLD_S:
        out = os.path.join(workdir, tag + ".mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ac", "1", "-ar", "44100", "-b:a", "128k", out], check=True)
        mime = "audio/mpeg"
    else:
        out = os.path.join(workdir, tag + ".wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ac", "1", "-ar", "44100", "-sample_fmt", "s16", out],
                       check=True)
        mime = "audio/wav"
    with open(out, "rb") as f:
        uri = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    return uri, round(dur, 2)


def main():
    manifest = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    tmpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "audition_template.html")).read()
    workdir = tempfile.mkdtemp(prefix="audio-audition-")
    data = []
    for s in manifest["sounds"]:
        variants = []
        for v in s["variants"]:
            if not os.path.exists(v["path"]):
                print(f"MISSING: {s['key']}/{v['id']} -> {v['path']}")
                continue
            uri, d = load(v["path"], workdir)
            variants.append({"id": v["id"], "label": v["label"],
                             "channel": v.get("channel", "gen"),
                             "src": uri, "dur": d})
        data.append({"key": s["key"], "title": s.get("title", s["key"]),
                     "role": s.get("role", ""), "variants": variants})
    html = (tmpl.replace("__TITLE__", manifest.get("title", "GAME"))
                .replace("__SUBTITLE__", manifest.get("subtitle", "Audio Audition"))
                .replace("__STORAGE__", manifest["storage_key"])
                .replace("__DATA__", json.dumps(data))
                .replace("__DEFAULTS__", json.dumps(manifest.get("defaults", {}))))
    with open(out, "w") as f:
        f.write(html)
    shutil.rmtree(workdir)
    nvar = sum(len(s["variants"]) for s in data)
    print(f"wrote {out}: {len(data)} rows, {nvar} variants, {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()

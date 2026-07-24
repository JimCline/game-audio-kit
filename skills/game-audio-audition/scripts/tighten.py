#!/usr/bin/env python3
"""Silence-trim + pitch-preserving time compression for a sound the user wants shorter.

Usage: python3 tighten.py file.wav target_seconds
Backs up the untouched original to file.orig.wav (first run only), then rewrites
file.wav: leading/trailing silence below -45dB removed, ffmpeg atempo applied to
hit the target duration (never slower than 1.0x), 40ms fade-out to kill clicks.
"""
import os, shutil, subprocess, sys


def probe(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip())


def main():
    src, target = sys.argv[1], float(sys.argv[2])
    bak = os.path.splitext(src)[0] + ".orig.wav"
    if not os.path.exists(bak):
        shutil.copy(src, bak)
    tmp = src + ".tighten_tmp.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", bak, "-af",
        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB,areverse", tmp], check=True)
    trimmed = probe(tmp)
    tempo = max(1.0, round(trimmed / target, 3))
    fade_start = round((target if tempo > 1.0 else trimmed) - 0.04, 3)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-af",
        f"atempo={tempo},afade=t=out:st={fade_start}:d=0.04", src], check=True)
    os.remove(tmp)
    print(f"{os.path.basename(src)}: orig={probe(bak):.2f}s trimmed={trimmed:.2f}s "
          f"tempo={tempo} final={probe(src):.2f}s (backup: {bak})")


if __name__ == "__main__":
    main()

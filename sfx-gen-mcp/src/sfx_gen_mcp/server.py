#!/usr/bin/env python
"""sfx-gen-mcp: MCP server for local game sound-effect generation.

Wraps Stability AI's Stable Audio Open (via stable-audio-tools) in an MCP
server so LLM agents (Claude Code, etc.) can generate SFX with a tool call.
Runs fully locally: no API keys, no per-generation cost.

Transports:
    stdio (default)  one server per client session; model loads per process
    http             shared daemon; one resident model serves all clients
                     (streamable HTTP at http://<host>:<port>/mcp)

The model is lazy-loaded on the first generate_sfx call and stays resident
for the life of the process. Generation is serialized with a lock so
concurrent clients queue rather than fight over the GPU.

To keep idle memory small, the process exits after SFX_IDLE_TIMEOUT seconds
(default 1800) without a generation once the model has been loaded. Under a
supervisor with restart (launchd KeepAlive / systemd Restart=always) this
means the daemon respawns model-free and reloads on the next call.

Env vars:
    SFX_MODEL         HF pretrained name (default stabilityai/stable-audio-open-1.0)
    SFX_OUTPUT_DIR    default directory for generated files (default <cwd>/sfx-output)
    SFX_IDLE_TIMEOUT  seconds of inactivity before exiting to free the model
                      (default 1800; 0 disables)

Note: the model weights are gated on Hugging Face — accept the license at
https://huggingface.co/stabilityai/stable-audio-open-1.0 and log in with
`hf auth login` before first use.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import threading
import time

from mcp.server.fastmcp import FastMCP

MODEL_NAME = os.environ.get("SFX_MODEL", "stabilityai/stable-audio-open-1.0")

mcp = FastMCP("sfx-gen")

_model = None
_model_config = None
_device = None
_load_lock = threading.Lock()
_gen_lock = threading.Lock()

IDLE_TIMEOUT = float(os.environ.get("SFX_IDLE_TIMEOUT", "1800"))
_last_used = time.monotonic()
_watchdog_started = False


def _log(msg: str) -> None:
    # on stdio transport, stdout carries the MCP protocol; log to stderr
    print(f"[sfx-gen] {msg}", file=sys.stderr, flush=True)


def _patch_mps_float64() -> None:
    """Upstream stable-audio-tools uses float64 inside APG projection, which
    MPS (Apple GPU) does not support. Rebind the method with a float32
    fallback on MPS. Idempotent; identical math otherwise."""
    import torch
    from stable_audio_tools.models import dit

    def apg_project(self, v0, v1, padding_mask=None):
        dtype = v0.dtype
        hi = torch.float32 if v0.device.type == "mps" else torch.float64
        v0, v1 = v0.to(hi), v1.to(hi)

        if padding_mask is not None:
            mask = padding_mask.unsqueeze(1).to(hi)
            v0_masked = v0 * mask
            v1_masked = v1 * mask
            v1_norm = v1_masked.norm(dim=[-1, -2], keepdim=True).clamp(min=1e-8)
            v1_normalized = v1_masked / v1_norm
            v0_parallel = (v0_masked * v1_normalized).sum(dim=[-1, -2], keepdim=True) * v1_normalized
            v0_orthogonal = (v0 - (v0 * v1_normalized).sum(dim=[-1, -2], keepdim=True) * v1_normalized) * mask
        else:
            v1 = torch.nn.functional.normalize(v1, dim=[-1, -2])
            v0_parallel = (v0 * v1).sum(dim=[-1, -2], keepdim=True) * v1
            v0_orthogonal = v0 - v0_parallel

        return v0_parallel.to(dtype), v0_orthogonal.to(dtype)

    dit.DiffusionTransformer.apg_project = apg_project


def _idle_watchdog() -> None:
    """Exit once the model has sat unused for IDLE_TIMEOUT seconds, freeing
    its memory. The supervisor (launchd KeepAlive) respawns a fresh,
    model-free process; the next call reloads the model."""
    while True:
        time.sleep(60)
        idle = time.monotonic() - _last_used
        if idle >= IDLE_TIMEOUT and not _gen_lock.locked():
            _log(f"Idle for {idle / 60:.0f}min — exiting to free model memory")
            os._exit(0)


def _get_model():
    global _model, _model_config, _device, _watchdog_started
    with _load_lock:
        if _model is not None:
            return _model, _model_config, _device

        _log(f"Loading {MODEL_NAME} (first call only, ~20-40s)...")
        t0 = time.time()
        # stable_audio_tools prints to stdout, which would corrupt stdio MCP
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from stable_audio_tools.models.pretrained import get_pretrained_model

            if torch.backends.mps.is_available():
                device = torch.device("mps")
                _patch_mps_float64()
            elif torch.cuda.is_available():
                device = torch.device("cuda")
            else:
                device = torch.device("cpu")

            model, model_config = get_pretrained_model(MODEL_NAME)
            model.to(device).eval().requires_grad_(False)

        _model, _model_config, _device = model, model_config, device
        _log(f"Model loaded on {device} in {time.time() - t0:.1f}s")
        if IDLE_TIMEOUT > 0 and not _watchdog_started:
            threading.Thread(target=_idle_watchdog, daemon=True).start()
            _watchdog_started = True
        return _model, _model_config, _device


def _slug(prompt: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", prompt.lower()).strip("-")
    return s[:60] or "sfx"


@mcp.tool()
def generate_sfx(
    prompt: str,
    duration_seconds: float = 4.0,
    steps: int = 50,
    cfg_scale: float = 7.0,
    seed: int = -1,
    negative_prompt: str = "",
    variations: int = 1,
    output_dir: str = "",
    filename: str = "",
) -> str:
    """Generate a game sound effect from a text prompt using a local
    Stable Audio Open model. Returns JSON with the saved .wav file path(s).

    Runs fully locally (no API cost). Typical generation time: well under a
    second per step on GPU, plus a one-time model load on the first call.

    Args:
        prompt: Description of the sound. Concrete, physical descriptions work
            best, e.g. "sword clashing against metal shield, sharp ring" or
            "footsteps on gravel, slow walking pace". Style tags like
            "high quality", "stereo" can help.
        duration_seconds: Length of the output audio (0.5 to 47 seconds).
            Keep short for one-shot SFX (impacts: 1-2s, ambiences: 10-30s).
        steps: Diffusion steps. 25-50 = fast draft, 100 = high quality.
        cfg_scale: Prompt adherence (1-15). 6-8 is a good range; higher forces
            the prompt harder at some cost to naturalness.
        seed: Random seed for reproducibility. -1 picks a random seed
            (returned in the result so you can re-generate variants of a
            sound you liked).
        negative_prompt: What the sound should NOT contain (e.g. "music,
            voices, reverb"). Empty disables it. Note: doubles generation time.
        variations: Number of takes to generate in one batch (1-4), each saved
            to its own file. Same seed, different noise per batch item.
        output_dir: Directory to save into. Defaults to $SFX_OUTPUT_DIR or
            <server cwd>/sfx-output. Pass an absolute path inside your game
            project (e.g. its sounds/ folder) to save directly there.
        filename: Base filename without extension. Defaults to a slug of the
            prompt plus the seed. Variations get a -2, -3... suffix.
    """
    import numpy as np

    global _last_used
    _last_used = time.monotonic()
    model, model_config, device = _get_model()

    with _gen_lock, contextlib.redirect_stdout(sys.stderr):
        import torch
        import torchaudio
        from stable_audio_tools.inference.generation import generate_diffusion_cond

        sample_rate = model_config["sample_rate"]
        sample_size = model_config["sample_size"]
        max_seconds = sample_size / sample_rate

        duration_seconds = max(0.5, min(float(duration_seconds), max_seconds))
        steps = max(8, min(int(steps), 250))
        variations = max(1, min(int(variations), 4))
        seed = int(seed)
        if seed == -1:
            seed = int(np.random.randint(0, 2**31 - 1))

        cond = {"prompt": prompt, "seconds_start": 0, "seconds_total": duration_seconds}
        neg = (
            [{"prompt": negative_prompt, "seconds_start": 0, "seconds_total": duration_seconds}] * variations
            if negative_prompt.strip()
            else None
        )

        t0 = time.time()
        audio = generate_diffusion_cond(
            model,
            conditioning=[cond] * variations,
            negative_conditioning=neg,
            steps=steps,
            cfg_scale=cfg_scale,
            batch_size=variations,
            sample_size=sample_size,
            seed=seed,
            device=device,
            sampler_type="dpmpp-3m-sde",
            sigma_min=0.03,
            sigma_max=500,
        )
        gen_time = time.time() - t0

        # (batch, channels, samples) -> trim padding silence to requested length
        audio = audio[:, :, : int(duration_seconds * sample_rate)]

        out_dir = output_dir or os.environ.get("SFX_OUTPUT_DIR") or os.path.join(os.getcwd(), "sfx-output")
        os.makedirs(out_dir, exist_ok=True)
        base = filename.strip() or f"{_slug(prompt)}-{seed}"

        files = []
        for i in range(variations):
            clip = audio[i].to(torch.float32)
            peak = torch.max(torch.abs(clip))
            if peak > 0:
                clip = clip / peak
            clip = clip.clamp(-1, 1).mul(32767).to(torch.int16).cpu()
            name = base if i == 0 else f"{base}-{i + 1}"
            path = os.path.join(out_dir, f"{name}.wav")
            torchaudio.save(path, clip, sample_rate)
            files.append(os.path.abspath(path))

    _last_used = time.monotonic()
    _log(f"Generated {len(files)} clip(s) in {gen_time:.1f}s: {files[0]}")
    return json.dumps(
        {
            "files": files,
            "seed": seed,
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "generation_time_seconds": round(gen_time, 1),
        }
    )


@mcp.tool()
def sfx_server_status() -> str:
    """Report sfx-gen server status: model name, whether the model is loaded
    (first generation loads it, ~20-40s), compute device, and whether a
    generation is currently running."""
    return json.dumps(
        {
            "model": MODEL_NAME,
            "model_loaded": _model is not None,
            "device": str(_device) if _device is not None else None,
            "generating": _gen_lock.locked(),
            "idle_timeout_seconds": IDLE_TIMEOUT,
            "idle_seconds": round(time.monotonic() - _last_used),
            "default_output_dir": os.environ.get("SFX_OUTPUT_DIR") or os.path.join(os.getcwd(), "sfx-output"),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for local SFX generation (Stable Audio Open)")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio: per-client server. http: shared daemon (streamable HTTP at /mcp)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8756)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        _log(f"Serving on http://{args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

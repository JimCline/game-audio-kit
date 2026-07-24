#!/usr/bin/env bash
#
# game-audio-kit installer
#
# Installs three pieces:
#   1. sfx-gen-mcp        — local SFX generation (Stable Audio Open) as an MCP
#                           daemon, supervised by launchd (macOS) or systemd
#                           (Linux) on port 8756
#   2. gemini-media-mcp   — music (Lyria) + voice (Gemini TTS) MCP server,
#                           prebuilt binary from GitHub releases (needs a
#                           GEMINI_API_KEY — paid Google API)
#   3. game-audio-audition — a Claude Code skill that runs the whole
#                           audition workflow on top of the two servers
#
# Usage:
#   ./install.sh                  install everything (prompts for API key)
#   ./install.sh --skip-gemini    skip the gemini-media music/voice server
#   GEMINI_API_KEY=... ./install.sh   non-interactive gemini setup
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SFX_PORT=8756
GEMINI_MEDIA_VERSION="${GEMINI_MEDIA_VERSION:-0.1.1}"
GEMINI_MEDIA_REPO="mordor-forge/gemini-media-mcp"
SFX_GEN_REPO="JimCline/sfx-gen-mcp"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/game-audio-audition"
SFX_OUTPUT_DIR="${SFX_OUTPUT_DIR:-$HOME/game-audio-kit-output/sfx}"
MEDIA_OUTPUT_DIR="${MEDIA_OUTPUT_DIR:-$HOME/game-audio-kit-output/media}"

SKIP_GEMINI=0
for arg in "$@"; do
  case "$arg" in
    --skip-gemini) SKIP_GEMINI=1 ;;
    -h|--help) awk 'NR>1 && !/^#/{exit} NR>1{print}' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
have()  { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
ARCH="$(uname -m)"

# ---------------------------------------------------------------- prereqs
bold "Checking prerequisites"

have uv || die "uv is required (installs sfx-gen-mcp). Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
info "uv: $(uv --version)"

HAVE_CLAUDE=0
if have claude; then
  HAVE_CLAUDE=1
  info "claude CLI found — MCP servers will be registered automatically"
else
  warn "claude CLI not found — MCP registration commands will be printed for you to run later"
fi

if have ffmpeg; then
  info "ffmpeg found"
else
  warn "ffmpeg not found — the audition skill needs it (brew install ffmpeg / apt install ffmpeg)"
fi

# ------------------------------------------------- 1. sfx-gen-mcp (local SFX)
bold "Installing sfx-gen-mcp (local Stable Audio Open MCP server)"

uv tool install --force "git+https://github.com/$SFX_GEN_REPO" >/dev/null
SFX_BIN="$(uv tool dir)/sfx-gen-mcp/bin/sfx-gen-mcp"
[ -x "$SFX_BIN" ] || SFX_BIN="$BIN_DIR/sfx-gen-mcp"
[ -x "$SFX_BIN" ] || die "sfx-gen-mcp installed but binary not found on expected paths"
info "installed: $SFX_BIN"
mkdir -p "$SFX_OUTPUT_DIR"

# Model weights are gated on Hugging Face.
if [ -f "$HOME/.cache/huggingface/token" ] || [ -n "${HF_TOKEN:-}" ]; then
  info "Hugging Face credentials detected"
else
  warn "No Hugging Face login detected. The model is GATED — before first generation:"
  warn "  1. Accept the license at https://huggingface.co/stabilityai/stable-audio-open-1.0"
  warn "  2. uv tool install huggingface_hub && hf auth login"
fi

# ------------------------------------------ daemon supervision (launchd/systemd)
if [ "$OS" = "Darwin" ]; then
  bold "Setting up launchd daemon (com.sfx-gen-mcp, port $SFX_PORT)"
  PLIST="$HOME/Library/LaunchAgents/com.sfx-gen-mcp.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.sfx-gen-mcp</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SFX_BIN</string>
        <string>--transport</string><string>http</string>
        <string>--port</string><string>$SFX_PORT</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTORCH_ENABLE_MPS_FALLBACK</key><string>1</string>
        <key>SFX_OUTPUT_DIR</key><string>$SFX_OUTPUT_DIR</string>
        <key>HOME</key><string>$HOME</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/sfx-gen-mcp.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/sfx-gen-mcp.log</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/com.sfx-gen-mcp" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  info "daemon loaded (logs: ~/Library/Logs/sfx-gen-mcp.log)"
elif [ "$OS" = "Linux" ] && have systemctl; then
  bold "Setting up systemd user service (sfx-gen-mcp, port $SFX_PORT)"
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/sfx-gen-mcp.service" <<UNIT
[Unit]
Description=sfx-gen-mcp local SFX generation daemon

[Service]
ExecStart=$SFX_BIN --transport http --port $SFX_PORT
Environment=SFX_OUTPUT_DIR=$SFX_OUTPUT_DIR
Restart=always

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now sfx-gen-mcp.service
  info "service enabled (journalctl --user -u sfx-gen-mcp)"
else
  warn "No supervisor configured for this platform. Run the daemon manually:"
  warn "  $SFX_BIN --transport http --port $SFX_PORT"
fi

if [ "$HAVE_CLAUDE" = 1 ]; then
  claude mcp remove --scope user sfx-gen >/dev/null 2>&1 || true
  claude mcp add --transport http --scope user sfx-gen "http://127.0.0.1:$SFX_PORT/mcp" >/dev/null
  info "registered MCP server 'sfx-gen' (user scope)"
fi

# --------------------------------------- 2. gemini-media-mcp (music + voice)
if [ "$SKIP_GEMINI" = 0 ]; then
  bold "Installing gemini-media-mcp (Lyria music + Gemini TTS voices)"

  case "$OS" in Darwin) GOOS=darwin ;; Linux) GOOS=linux ;; *) GOOS="" ;; esac
  case "$ARCH" in arm64|aarch64) GOARCH=arm64 ;; x86_64|amd64) GOARCH=amd64 ;; *) GOARCH="" ;; esac

  if [ -n "$GOOS" ] && [ -n "$GOARCH" ]; then
    ASSET="gemini-media-mcp_${GEMINI_MEDIA_VERSION}_${GOOS}_${GOARCH}.tar.gz"
    URL="https://github.com/$GEMINI_MEDIA_REPO/releases/download/v$GEMINI_MEDIA_VERSION/$ASSET"
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    curl -fsSL "$URL" -o "$TMP/$ASSET" || die "Download failed: $URL"
    tar -xzf "$TMP/$ASSET" -C "$TMP"
    mkdir -p "$BIN_DIR" "$MEDIA_OUTPUT_DIR"
    GM_BIN_SRC="$(find "$TMP" -name gemini-media-mcp -type f | head -1)"
    [ -n "$GM_BIN_SRC" ] || die "gemini-media-mcp binary not found in release archive"
    install -m 755 "$GM_BIN_SRC" "$BIN_DIR/gemini-media-mcp"
    info "installed: $BIN_DIR/gemini-media-mcp (v$GEMINI_MEDIA_VERSION)"
  else
    die "Unsupported platform $OS/$ARCH — build from source: https://github.com/$GEMINI_MEDIA_REPO"
  fi

  if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo
    echo "  gemini-media needs a Google Gemini API key (PAID API for music/TTS)."
    echo "  Get one at https://aistudio.google.com/apikey — or press Enter to skip"
    echo "  registration (you can register later, see README)."
    printf "  GEMINI_API_KEY: "
    read -r GEMINI_API_KEY || GEMINI_API_KEY=""
  fi

  if [ -n "${GEMINI_API_KEY:-}" ] && [ "$HAVE_CLAUDE" = 1 ]; then
    claude mcp remove --scope user gemini-media >/dev/null 2>&1 || true
    if [ "$OS" = "Darwin" ]; then
      # Keep the key out of plaintext config: store it in the macOS Keychain
      # and register a wrapper that injects it at server launch.
      security add-generic-password -a "$USER" -s gemini-api-key -w "$GEMINI_API_KEY" -U
      cat > "$BIN_DIR/with-gemini-key" <<'WRAP'
#!/bin/bash
# Fetch GEMINI_API_KEY from the macOS Keychain (service "gemini-api-key") and
# exec the given command with it in the environment.
export GEMINI_API_KEY="$(security find-generic-password -s gemini-api-key -w)"
[ -n "$GEMINI_API_KEY" ] || { echo "with-gemini-key: no 'gemini-api-key' item in Keychain" >&2; exit 1; }
exec "$@"
WRAP
      chmod 755 "$BIN_DIR/with-gemini-key"
      claude mcp add --scope user gemini-media "$BIN_DIR/with-gemini-key" "$BIN_DIR/gemini-media-mcp" \
        --env "MEDIA_OUTPUT_DIR=$MEDIA_OUTPUT_DIR" >/dev/null
      info "API key stored in the macOS Keychain (service 'gemini-api-key')"
      info "registered MCP server 'gemini-media' via keychain wrapper (user scope)"
    else
      claude mcp add --scope user gemini-media "$BIN_DIR/gemini-media-mcp" \
        --env "GEMINI_API_KEY=$GEMINI_API_KEY" \
        --env "MEDIA_OUTPUT_DIR=$MEDIA_OUTPUT_DIR" >/dev/null
      info "registered MCP server 'gemini-media' (user scope)"
      warn "note: the key is stored in plaintext in ~/.claude.json on this platform"
    fi
  else
    warn "gemini-media not registered. When ready:"
    warn "  claude mcp add --scope user gemini-media $BIN_DIR/gemini-media-mcp \\"
    warn "    --env GEMINI_API_KEY=<your-key> --env MEDIA_OUTPUT_DIR=$MEDIA_OUTPUT_DIR"
  fi
else
  warn "Skipping gemini-media (music/voice) — SFX-only install"
fi

# ------------------------------------------- 3. game-audio-audition skill
bold "Installing the game-audio-audition Claude Code skill"
mkdir -p "$(dirname "$SKILL_DIR")"
rm -rf "$SKILL_DIR"
cp -R "$REPO_DIR/skills/game-audio-audition" "$SKILL_DIR"
info "installed: $SKILL_DIR"

# ----------------------------------------------------------------- summary
echo
bold "Done. Next steps:"
echo "  1. Accept the model license + 'hf auth login' if you haven't (see above)."
echo "  2. Restart any running Claude Code sessions so they pick up the MCP servers."
echo "  3. Try it: ask Claude Code to 'generate a coin pickup sound' — the first"
echo "     generation downloads ~10 GB of model weights and takes a few minutes;"
echo "     after that the model stays resident and clips take ~15-30 s."
echo "  4. In a game project, say 'run a sound audition for this game' to invoke"
echo "     the game-audio-audition skill."
if [ "$HAVE_CLAUDE" = 0 ]; then
  echo
  warn "claude CLI was not found. Register the servers once it's installed:"
  warn "  claude mcp add --transport http --scope user sfx-gen http://127.0.0.1:$SFX_PORT/mcp"
  warn "  claude mcp add --scope user gemini-media $BIN_DIR/gemini-media-mcp --env GEMINI_API_KEY=<key> --env MEDIA_OUTPUT_DIR=$MEDIA_OUTPUT_DIR"
fi

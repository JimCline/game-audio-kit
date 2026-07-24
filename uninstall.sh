#!/usr/bin/env bash
#
# game-audio-kit uninstaller — reverses everything install.sh set up.
#
# Usage:
#   ./uninstall.sh          prompts before removing the gemini-media binary
#   ./uninstall.sh --yes    no prompts
#
# NOT removed (listed at the end): downloaded model weights (~10 GB in the
# Hugging Face cache) and any audio you generated.
#
set -uo pipefail

BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.claude/skills/game-audio-audition"

YES=0
[ "${1:-}" = "--yes" ] && YES=1

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[2m-\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  [ "$YES" = 1 ] && return 0
  printf '  %s [y/N] ' "$1"
  read -r a; [ "$a" = y ] || [ "$a" = Y ]
}

bold "Stopping and removing the sfx-gen daemon"
if [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.sfx-gen-mcp.plist"
  if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)/com.sfx-gen-mcp" 2>/dev/null
    rm -f "$PLIST"
    info "launchd agent removed"
  else
    skip "no launchd agent found"
  fi
elif have systemctl; then
  UNIT="$HOME/.config/systemd/user/sfx-gen-mcp.service"
  if [ -f "$UNIT" ]; then
    systemctl --user disable --now sfx-gen-mcp.service 2>/dev/null
    rm -f "$UNIT"
    systemctl --user daemon-reload
    info "systemd user service removed"
  else
    skip "no systemd service found"
  fi
fi

bold "Uninstalling sfx-gen-mcp"
if uv tool uninstall sfx-gen-mcp >/dev/null 2>&1; then
  info "uv tool removed"
else
  skip "not installed via uv tool"
fi

bold "Removing gemini-media-mcp"
if [ -f "$BIN_DIR/with-gemini-key" ]; then
  rm -f "$BIN_DIR/with-gemini-key"
  info "keychain wrapper removed"
fi
if [ "$(uname -s)" = "Darwin" ] && security find-generic-password -s gemini-api-key >/dev/null 2>&1; then
  if confirm "Delete the 'gemini-api-key' item from the macOS Keychain?"; then
    security delete-generic-password -s gemini-api-key >/dev/null
    info "keychain item removed"
  else
    skip "keychain item kept"
  fi
fi
if [ -f "$BIN_DIR/gemini-media-mcp" ]; then
  if confirm "Delete $BIN_DIR/gemini-media-mcp?"; then
    rm -f "$BIN_DIR/gemini-media-mcp"
    info "binary removed"
  else
    skip "kept"
  fi
else
  skip "no binary found"
fi

bold "Unregistering MCP servers from Claude Code"
if have claude; then
  claude mcp remove --scope user sfx-gen >/dev/null 2>&1 && info "sfx-gen unregistered" || skip "sfx-gen not registered"
  claude mcp remove --scope user gemini-media >/dev/null 2>&1 && info "gemini-media unregistered" || skip "gemini-media not registered"
else
  skip "claude CLI not found — nothing to unregister"
fi

bold "Removing the game-audio-audition skill"
if [ -d "$SKILL_DIR" ]; then
  rm -rf "$SKILL_DIR"
  info "skill removed"
else
  skip "not installed"
fi

echo
bold "Left in place (remove manually if you want the space back):"
echo "  - Model weights: ~/.cache/huggingface/hub/models--stabilityai--stable-audio-open-1.0 (~10 GB)"
echo "  - Generated audio: ~/game-audio-kit-output/ (or wherever you pointed the output dirs)"

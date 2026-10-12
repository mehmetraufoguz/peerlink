#!/usr/bin/env bash
# agent-relay CLI'yi kur (pipx veya pip --user) ve skill'i ~/.claude/skills altına bağla.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if command -v pipx >/dev/null 2>&1; then
  pipx install --force "$HERE" >/dev/null && echo "agent-relay: pipx ile kuruldu"
else
  python3 -m pip install --user --quiet "$HERE" && echo "agent-relay: pip --user ile kuruldu (~/.local/bin PATH'te olmalı)"
fi

SKILL_DST="$HOME/.claude/skills/agent-relay-peer"
mkdir -p "$HOME/.claude/skills"
if [ -L "$SKILL_DST" ] || [ ! -e "$SKILL_DST" ]; then
  ln -sfn "$HERE/skills/agent-relay-peer" "$SKILL_DST"
  echo "skill: $SKILL_DST -> $HERE/skills/agent-relay-peer"
else
  echo "skill: $SKILL_DST zaten var ve symlink değil; elle birleştir" >&2
fi

CFG="$HOME/.config/agent-relay-peer/config.json"
if [ ! -f "$CFG" ]; then
  mkdir -p "$(dirname "$CFG")"
  cp "$HERE/config.example.json" "$CFG"
  chmod 600 "$CFG"
  echo "config: $CFG oluşturuldu; secret ve peers alanlarını doldur"
fi
echo "bitti. deneme: agent-relay status"

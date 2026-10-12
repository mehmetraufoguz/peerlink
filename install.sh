#!/usr/bin/env bash
# peerlink kurulumu: CLI (~/.local/bin/peerlink), Claude Code skill symlink'i ve config.
#
#   ./install.sh                                   # yalnızca CLI + skill (config varsa dokunmaz)
#   ./install.sh --secret S --peer 192.168.1.20 --label macA   # config'i de yazar
#
# Bağımlılık yok: pipx varsa onunla, yoksa bu klasöre işaret eden bir sarmalayıcı betikle kurar.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
CFG_DIR="$HOME/.config/peerlink"
CFG="$CFG_DIR/config.json"
SKILL_DST="$HOME/.claude/skills/peerlink"

SECRET="" LABEL="" PEERS=() PORT=7437
while [ $# -gt 0 ]; do
  case "$1" in
    --secret) SECRET="$2"; shift 2 ;;
    --peer)   PEERS+=("$2"); shift 2 ;;
    --label)  LABEL="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "bilinmeyen argüman: $1" >&2; exit 2 ;;
  esac
done

# 1) CLI
mkdir -p "$BIN"
if command -v pipx >/dev/null 2>&1; then
  pipx install --force "$HERE" >/dev/null 2>&1 && echo "cli:    pipx ile kuruldu ($(command -v peerlink || echo "$BIN/peerlink"))"
else
  cat > "$BIN/peerlink" <<EOF
#!/usr/bin/env bash
exec python3 "$HERE/peerlink/relay.py" "\$@"
EOF
  chmod +x "$BIN/peerlink"
  echo "cli:    $BIN/peerlink -> $HERE (sarmalayıcı)"
fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    # ~/.local/bin PATH'te değil: kabuk rc dosyasına bir kez ekle (zsh ve bash)
    LINE='export PATH="$HOME/.local/bin:$PATH"  # peerlink'
    ADDED=""
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
      if ! grep -qs '\.local/bin' "$rc"; then printf '\n%s\n' "$LINE" >> "$rc"; ADDED="$ADDED $rc"; fi
    done
    [ -n "$ADDED" ] && echo "path:   $BIN PATH'e eklendi ($ADDED); bu terminalde etkinleştir:  source ~/.zshrc" \
                    || echo "path:   $BIN PATH'te değil; yeni bir terminal aç ya da:  source ~/.zshrc"
    export PATH="$BIN:$PATH"
    ;;
esac

# 2) Skill
mkdir -p "$HOME/.claude/skills"
if [ -d "$SKILL_DST" ] && [ ! -L "$SKILL_DST" ] && grep -q '^name: peerlink' "$SKILL_DST/SKILL.md" 2>/dev/null; then
  # npx skills add ile kopyalanmış eski sürüm: repo'daki canlı sürüme bağla
  rm -rf "$SKILL_DST"
fi
if [ -L "$SKILL_DST" ] || [ ! -e "$SKILL_DST" ]; then
  ln -sfn "$HERE/skills/peerlink" "$SKILL_DST"
  echo "skill:  $SKILL_DST -> $HERE/skills/peerlink"
else
  echo "skill:  $SKILL_DST zaten var ve peerlink değil; elle birleştir" >&2
fi

# 3) Config
mkdir -p "$CFG_DIR"
if [ -n "$SECRET" ] || [ ${#PEERS[@]} -gt 0 ] || [ -n "$LABEL" ]; then
  [ -n "$SECRET" ] || { echo "config: --peer/--label verildi ama --secret yok" >&2; exit 2; }
  [ -n "$LABEL" ] || LABEL="$(hostname -s)"
  PEERS_JSON="$(printf '"%s",' "${PEERS[@]-}" | sed 's/,$//; s/^""$//')"
  cat > "$CFG" <<EOF
{
  "secret": "$SECRET",
  "peers": [$PEERS_JSON],
  "label": "$LABEL",
  "port": $PORT,
  "bind": "0.0.0.0",
  "advertise": null,
  "interval": 5
}
EOF
  chmod 600 "$CFG"
  echo "config: $CFG yazıldı (label=$LABEL, peers=${PEERS[*]-})"
elif [ ! -f "$CFG" ]; then
  cp "$HERE/config.example.json" "$CFG"; chmod 600 "$CFG"
  echo "config: $CFG taslak olarak oluşturuldu; secret ve peers alanlarını doldur"
else
  echo "config: $CFG mevcut, dokunulmadı"
fi

echo
echo "bitti. sonraki adım:  peerlink up        (arka plan: nohup peerlink up > ~/.peerlink.log 2>&1 &)"
echo "kontrol:              peerlink who"

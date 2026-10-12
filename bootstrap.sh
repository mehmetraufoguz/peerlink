#!/usr/bin/env bash
# Tek komutla kurulum: klonla (veya güncelle) + install.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/mehmetraufoguz/peerlink/main/bootstrap.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/mehmetraufoguz/peerlink/main/bootstrap.sh \
#     | bash -s -- --secret S --peer 192.168.1.20 --label macB
#
# Hedef klasör: $PEERLINK_DIR (varsayılan ~/.peerlink). Tüm argümanlar install.sh'e aktarılır.
set -euo pipefail
REPO="${PEERLINK_REPO:-https://github.com/mehmetraufoguz/peerlink.git}"
DIR="${PEERLINK_DIR:-$HOME/.peerlink}"

command -v git >/dev/null 2>&1 || { echo "git gerekli" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 (3.9+) gerekli" >&2; exit 1; }

if [ -d "$DIR/.git" ]; then
  echo "güncelleniyor: $DIR"; git -C "$DIR" pull -q --ff-only
else
  echo "klonlanıyor: $REPO -> $DIR"; git clone -q "$REPO" "$DIR"
fi
exec bash "$DIR/install.sh" "$@"

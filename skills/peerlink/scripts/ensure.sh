#!/usr/bin/env bash
# peerlink kurulu mu? Değilse kur; kuruluysa doktoru çalıştır.
#
#   ensure.sh --check                         # yalnızca durum: exit 0 kurulu+hazır, 2 kurulu ama sorunlu, 3 kurulu değil
#   ensure.sh                                 # kurulu değilse CLI + skill kur (config'e dokunmaz), sonra doctor
#   ensure.sh --secret S --peer IP --label L  # kurulu değilse kur ve config'i yaz; kuruluysa sadece config'i yaz
#
# Skill tek başına (npx skills add ile) kurulmuş olabilir; kod yoksa bootstrap.sh ile ~/.peerlink altına klonlar.
set -uo pipefail
BOOTSTRAP="${PEERLINK_BOOTSTRAP:-https://raw.githubusercontent.com/mehmetraufoguz/peerlink/main/bootstrap.sh}"
DIR="${PEERLINK_DIR:-$HOME/.peerlink}"

CHECK=0; ARGS=()
for x in "$@"; do [ "$x" = "--check" ] && CHECK=1 || ARGS+=("$x"); done

find_cli() {
  if command -v peerlink >/dev/null 2>&1; then echo "peerlink"; return; fi
  if [ -x "$HOME/.local/bin/peerlink" ]; then echo "$HOME/.local/bin/peerlink"; return; fi
  for d in "$DIR" "$HOME/Documents/GitHub/peerlink"; do
    [ -f "$d/peerlink/relay.py" ] && { echo "python3 $d/peerlink/relay.py"; return; }
  done
  return 1
}
repo_dir() {  # CLI'nin ait olduğu repo klasörü (install.sh için)
  local c="$1"
  case "$c" in
    python3\ *) dirname "$(dirname "${c#python3 }")" ;;
    *) local t; t="$(command -v "$c" 2>/dev/null || echo "$c")"
       grep -o '"/[^"]*/peerlink/relay.py"' "$t" 2>/dev/null | head -1 | sed 's/^"//; s|/peerlink/relay.py"$||' || true ;;
  esac
}

CLI="$(find_cli)" || CLI=""
if [ -z "$CLI" ]; then
  echo "peerlink: KURULU DEĞİL (komut yok, $DIR yok)"
  [ $CHECK -eq 1 ] && exit 3
  command -v git >/dev/null && command -v python3 >/dev/null && command -v curl >/dev/null \
    || { echo "git, python3 (3.9+) ve curl gerekli" >&2; exit 1; }
  echo "kuruluyor: $BOOTSTRAP -> $DIR"
  curl -fsSL "$BOOTSTRAP" | bash -s -- "${ARGS[@]}" || exit 1
  CLI="$(find_cli)" || { echo "kurulum sonrası komut bulunamadı" >&2; exit 1; }
else
  echo "peerlink: kurulu ($CLI)"
  if [ ${#ARGS[@]} -gt 0 ] && [ $CHECK -eq 0 ]; then
    R="$(repo_dir "$CLI")"
    [ -n "$R" ] && [ -x "$R/install.sh" ] && bash "$R/install.sh" "${ARGS[@]}" || echo "install.sh bulunamadı; config'i elle yaz: ~/.config/peerlink/config.json" >&2
  fi
fi

$CLI doctor; rc=$?
[ $CHECK -eq 1 ] && { [ $rc -eq 0 ] && exit 0 || exit 2; }
exit $rc

#!/usr/bin/env bash
# NowPlayingJellyfin uninstaller. undoes everything install.sh did.
#   sudo bash /opt/nowplaying/uninstall.sh
set -euo pipefail

DESTDIR="${DESTDIR:-}"
SKIP_SYSTEM="${SKIP_SYSTEM:-0}"
MARK_BEGIN="# >>> nowplaying lcd >>>"
MARK_END="# <<< nowplaying lcd <<<"

TTY=/dev/tty
if ! { : < "$TTY"; } 2>/dev/null; then TTY=""; fi
yes_no() {
  local a=""
  [ -z "$TTY" ] && { [ "$2" = "Y" ]; return; }
  printf '  %s (y/n) [%s]: ' "$1" "$2" > "$TTY"
  IFS= read -r a < "$TTY" || true
  case "${a:-$2}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

if [ "$SKIP_SYSTEM" != "1" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "run it as root: sudo bash $0"; exit 1; }
fi

echo "This removes NowPlayingJellyfin (app, settings, service, the 'nowplaying' user)."
yes_no "Continue?" "N" || { echo "ok, nothing touched."; exit 0; }

if [ "$SKIP_SYSTEM" != "1" ]; then
  systemctl disable --now nowplaying >/dev/null 2>&1 || true
fi
rm -f "$DESTDIR/etc/systemd/system/nowplaying.service" "$DESTDIR/usr/local/bin/display" \
      "$DESTDIR/usr/share/bash-completion/completions/display"
rm -rf "$DESTDIR/etc/nowplaying" "$DESTDIR/var/lib/nowplaying"
if [ "$SKIP_SYSTEM" != "1" ]; then
  systemctl daemon-reload
  id nowplaying >/dev/null 2>&1 && userdel nowplaying || true
fi
echo "  ✓ service, settings and user removed"

for c in "$DESTDIR/boot/firmware/config.txt" "$DESTDIR/boot/config.txt"; do
  if [ -f "$c" ] && grep -qF "$MARK_BEGIN" "$c"; then
    if yes_no "Also remove the LCD driver lines from $c?" "N"; then
      # delete our marked block, leave everything else alone
      sed -i "/^$MARK_BEGIN\$/,/^$MARK_END\$/d" "$c"
      echo "  ✓ driver lines removed (reboot to apply)"
    fi
  fi
done

# last, since this script lives in there
rm -rf "$DESTDIR/opt/nowplaying"
echo "  ✓ /opt/nowplaying removed. bye monster :("

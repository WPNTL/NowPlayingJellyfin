#!/usr/bin/env bash
# NowPlayingJellyfin installer
#
#   curl -fsSL https://raw.githubusercontent.com/WPNTL/NowPlayingJellyfin/main/install.sh | sudo bash
#
# or, if u cloned the repo:   sudo ./install.sh
#
# what it does (asks before anything important):
#   1. installs python3 + pillow + numpy from apt
#   2. copies the app to /opt/nowplaying, adds the `display` command (w/ TAB completion)
#   3. sets up the 3.5" lcd driver in config.txt (backs it up first)
#   4. asks for ur jellyfin url + api key, tests them, saves em root-only
#   5. creates an unprivileged `nowplaying` user + a hardened systemd service
#
# safe to run again to update, it keeps ur settings.
#
# skip the questions w/ env vars (handy for scripting):
#   JELLYFIN_URL=... JELLYFIN_API_KEY=... LCD_OVERLAY=piscreen|waveshare35a|skip NP_LANG=en ASSUME_YES=1

set -euo pipefail

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/WPNTL/NowPlayingJellyfin/main}"
FILES=(nowplaying.py themes.py display display-completion.bash nowplaying.service uninstall.sh)

# DESTDIR + SKIP_SYSTEM=1 let me test this whole thing on a laptop w/o wrecking it.
# u don't need these
DESTDIR="${DESTDIR:-}"
SKIP_SYSTEM="${SKIP_SYSTEM:-0}"
APP_DIR="$DESTDIR/opt/nowplaying"
CONF_DIR="$DESTDIR/etc/nowplaying"
ENV_FILE="$CONF_DIR/nowplaying.env"
UNIT_FILE="$DESTDIR/etc/systemd/system/nowplaying.service"
BIN_LINK="$DESTDIR/usr/local/bin/display"
COMPLETION="$DESTDIR/usr/share/bash-completion/completions/display"
MARK_BEGIN="# >>> nowplaying lcd >>>"
MARK_END="# <<< nowplaying lcd <<<"

# ------------------------------------------------------------------ helpers --
if [ -t 1 ]; then
  B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
else
  B=""; G=""; Y=""; R=""; C=""; N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$C" "$N" "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sx %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# when piped from curl, stdin IS the script, so questions have to read from the terminal
TTY=/dev/tty
if ! { : < "$TTY"; } 2>/dev/null; then TTY=""; fi

ask() {   # ask "question" "default" -> echoes answer
  local q="$1" def="${2:-}" ans=""
  if [ "${ASSUME_YES:-0}" = "1" ] || [ -z "$TTY" ]; then echo "$def"; return; fi
  if [ -n "$def" ]; then printf '  %s [%s]: ' "$q" "$def" > "$TTY"; else printf '  %s: ' "$q" > "$TTY"; fi
  IFS= read -r ans < "$TTY" || true
  echo "${ans:-$def}"
}
ask_secret() {
  local q="$1" ans=""
  [ -z "$TTY" ] && { echo ""; return; }
  printf '  %s: ' "$q" > "$TTY"
  IFS= read -rs ans < "$TTY" || true
  printf '\n' > "$TTY"
  echo "$ans"
}
yes_no() {   # yes_no "question" Y|N -> exit code
  local a
  a="$(ask "$1 (y/n)" "$2")"
  case "$a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

lcd_found() {
  grep -qiE 'ili9486|fbtft|piscreen|waveshare' "$DESTDIR"/sys/class/graphics/fb*/name 2>/dev/null
}

# tests url + key w/ python (already a dependency). the key goes thru an env var,
# NOT the command line, so it never shows up in `ps` for other users
jf_check() {   # jf_check url key -> prints server name, exit 0 ok / 2 bad key / 1 unreachable
  NPK="$2" python3 - "$1" <<'PY'
import json, os, sys, urllib.request, urllib.error
url, key = sys.argv[1].rstrip("/"), os.environ.get("NPK", "")
try:
    with urllib.request.urlopen(url + "/System/Info/Public", timeout=6) as r:
        name = json.loads(r.read(65536)).get("ServerName", "Jellyfin")
except Exception:
    sys.exit(1)
if key:
    req = urllib.request.Request(url + "/Sessions", headers={
        "Authorization": f'MediaBrowser Client="NowPlayingInstaller", Device="installer", '
                         f'DeviceId="nowplaying-installer", Version="1.0", Token="{key}"'})
    try:
        urllib.request.urlopen(req, timeout=6).read(65536)
    except urllib.error.HTTPError as e:
        sys.exit(2 if e.code in (401, 403) else 1)
    except Exception:
        sys.exit(1)
print(name)
PY
}

# ------------------------------------------------------------------- checks --
say ""
say "${B}  ┌──────────────────────────────────────┐${N}"
say "${B}  │   NowPlayingJellyfin  ·  installer   │${N}"
say "${B}  └──────────────────────────────────────┘${N}"

if [ "$SKIP_SYSTEM" != "1" ]; then
  [ "$(id -u)" -eq 0 ] || die "run it as root:  sudo bash install.sh"
  command -v apt-get >/dev/null || die "this needs a Debian-based OS (Raspberry Pi OS, DietPi, Ubuntu...)"
  command -v systemctl >/dev/null || die "systemd not found"
fi

# ------------------------------------------------------------- 1. packages --
step "1/5  Installing dependencies"
if [ "$SKIP_SYSTEM" != "1" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  # bash-completion = TAB for the display command. tiny, and dietpi doesn't ship it
  apt-get install -y -qq python3 python3-pil python3-numpy curl bash-completion >/dev/null
fi
ok "python3, pillow, numpy, bash-completion"

# ------------------------------------------------------------------ 2. app --
step "2/5  Installing the app to /opt/nowplaying"
mkdir -p "$APP_DIR"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/nowplaying.py" ] && [ -f "$SRC_DIR/themes.py" ]; then
  for f in "${FILES[@]}"; do install -m 0644 "$SRC_DIR/$f" "$APP_DIR/$f"; done
  ok "copied from $SRC_DIR"
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  for f in "${FILES[@]}"; do
    curl -fsSL "$REPO_RAW/$f" -o "$tmp/$f" || die "couldn't download $f (no internet?)"
  done
  for f in "${FILES[@]}"; do install -m 0644 "$tmp/$f" "$APP_DIR/$f"; done
  ok "downloaded from GitHub"
fi
# windows editors love sneaking \r in, which breaks the shebang. strip just in case
sed -i 's/\r$//' "$APP_DIR"/*.py "$APP_DIR"/*.bash "$APP_DIR/display" "$APP_DIR/uninstall.sh"
chmod 0755 "$APP_DIR/display" "$APP_DIR/uninstall.sh"
mkdir -p "$(dirname "$BIN_LINK")"
ln -sf /opt/nowplaying/display "$BIN_LINK"
mkdir -p "$(dirname "$COMPLETION")"
install -m 0644 "$APP_DIR/display-completion.bash" "$COMPLETION"
ok "\`display\` command ready (TAB completes it in new terminals)"

# ------------------------------------------------------------------ 3. lcd --
step "3/5  Screen driver (3.5\" ILI9486 SPI LCD)"
CONFIG=""
for c in "$DESTDIR/boot/firmware/config.txt" "$DESTDIR/boot/config.txt"; do
  [ -f "$c" ] && { CONFIG="$c"; break; }
done
REBOOT=0
if lcd_found; then
  ok "LCD driver already running, nothing to do"
elif [ -z "$CONFIG" ]; then
  warn "no config.txt found (not a Raspberry Pi?). skipping, see the README to set it up by hand"
elif grep -qF "$MARK_BEGIN" "$CONFIG"; then
  ok "driver already configured in $CONFIG (reboot to load it if u haven't)"
  REBOOT=1
else
  overlay="${LCD_OVERLAY:-}"
  if [ -z "$overlay" ]; then
    say "  Most cheap 3.5\" ILI9486 boards work with ${B}piscreen${N}. If yours stays white,"
    say "  re-run the installer and pick ${B}waveshare35a${N} instead."
    say "    1) piscreen      (recommended)"
    say "    2) waveshare35a"
    say "    3) skip          (I'll set it up myself)"
    case "$(ask "Pick one" "1")" in
      2) overlay="waveshare35a" ;;
      3) overlay="skip" ;;
      *) overlay="piscreen" ;;
    esac
  fi
  if [ "$overlay" != "skip" ]; then
    ovdir="$(dirname "$CONFIG")/overlays"
    if [ -d "$ovdir" ] && [ ! -f "$ovdir/$overlay.dtbo" ]; then
      if [ "$overlay" != "piscreen" ] && [ -f "$ovdir/piscreen.dtbo" ]; then
        warn "$overlay.dtbo isn't on this system, using piscreen instead"
        overlay="piscreen"
      else
        warn "$overlay.dtbo not found. adding it anyway, but u might need a firmware update (sudo apt full-upgrade)"
      fi
    fi
    cp "$CONFIG" "$CONFIG.nowplaying.bak"
    {
      printf '\n%s\n' "$MARK_BEGIN"
      printf 'dtparam=spi=on\n'
      printf 'dtoverlay=%s,speed=16000000,rotate=90\n' "$overlay"
      printf '%s\n' "$MARK_END"
    } >> "$CONFIG"
    ok "added $overlay to $CONFIG (backup: $(basename "$CONFIG").nowplaying.bak)"
    REBOOT=1
  else
    warn "skipped"
  fi
fi

# ------------------------------------------------------------- 4. jellyfin --
step "4/5  Connecting to Jellyfin"
KEEP=0
URL="${JELLYFIN_URL:-}"; KEY="${JELLYFIN_API_KEY:-}"; LANG_="${NP_LANG:-}"
if [ -f "$ENV_FILE" ] && [ -z "$KEY" ]; then
  if yes_no "Found ur existing settings, keep them?" "Y"; then KEEP=1; ok "keeping $ENV_FILE"; fi
fi
# migrating from the old pre-1.0 setup where the key sat in the unit file itself
if [ "$KEEP" = "0" ] && [ -z "$KEY" ] && [ -f "$UNIT_FILE" ]; then
  KEY="$(sed -n 's/^Environment=JELLYFIN_API_KEY=//p' "$UNIT_FILE" | head -n1)"
  URL="${URL:-$(sed -n 's/^Environment=JELLYFIN_URL=//p' "$UNIT_FILE" | head -n1)}"
  [ -n "$KEY" ] && ok "picked up the API key from ur old service file (moving it somewhere safer)"
fi

if [ "$KEEP" = "0" ]; then
  if [ -z "$URL" ]; then
    URL="http://127.0.0.1:8096"
    # jellyfin on the same pi is the usual setup, try the default port quietly first
    if ! jf_check "$URL" "" >/dev/null 2>&1; then
      say "  Couldn't find Jellyfin on this Pi at port 8096."
      URL="$(ask "Jellyfin address (e.g. http://192.168.0.10:8096)" "$URL")"
    fi
  fi
  URL="${URL%/}"
  if name="$(jf_check "$URL" "")"; then ok "found server \"$name\" at $URL"; else warn "can't reach $URL right now, saving it anyway"; fi

  if [ -z "$KEY" ]; then
    say ""
    say "  Now an API key. In Jellyfin (web browser):"
    say "    ${B}Dashboard${N} → ${B}API Keys${N} → ${B}+${N} → name it \"nowplaying\" → copy the key"
    say "  (typing is hidden, just paste and hit Enter)"
  fi
  for try in 1 2 3; do
    [ -z "$KEY" ] && KEY="$(ask_secret "API key")"
    [ -z "$KEY" ] && { warn "no key given. the screen will show the monster but no movies until u add one"; break; }
    rc=0; jf_check "$URL" "$KEY" >/dev/null 2>&1 || rc=$?
    if [ "$rc" = "0" ]; then ok "API key works"; break; fi
    if [ "$rc" = "2" ]; then warn "Jellyfin said that key is wrong (try $try/3)"; else warn "couldn't verify the key (server unreachable?), saving it anyway"; break; fi
    KEY=""
    [ "$try" = "3" ] && warn "giving up on the key, fix it later in $ENV_FILE"
  done

  if [ -z "$LANG_" ]; then
    case "$(ask "On-screen language: en (English) / pt (Português)" "en")" in
      pt*|PT*) LANG_="pt" ;;
      *) LANG_="en" ;;
    esac
  fi

  mkdir -p "$CONF_DIR"
  chmod 0755 "$CONF_DIR"
  ( umask 077
    {
      echo "# NowPlayingJellyfin settings. root-only on purpose, the API key is in here."
      echo "# after editing:  sudo display restart"
      echo "JELLYFIN_URL=$URL"
      echo "JELLYFIN_API_KEY=$KEY"
      echo "NP_LANG=$LANG_"
      echo "# theme, lang, sleep timer and rotation are easier w/ the display command:"
      echo "#   sudo display sleep 4h   /   sudo display rotate 180   /   display help"
      echo "# NP_FPS=6             # only raise this if u also raise the SPI speed"
      echo "# NP_PIXEL_POSTER=1    # crunchy 8-bit poster instead of the full-res one"
    } > "$ENV_FILE"
  )
  chmod 0600 "$ENV_FILE"
  ok "settings saved to $ENV_FILE (readable by root only)"
fi
unset KEY

# --------------------------------------------------------------- 5. service --
step "5/5  Service"
mkdir -p "$(dirname "$UNIT_FILE")"
install -m 0644 "$APP_DIR/nowplaying.service" "$UNIT_FILE"
if [ "$SKIP_SYSTEM" != "1" ]; then
  if ! id nowplaying >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin nowplaying
  fi
  usermod -aG video nowplaying
  ok "user 'nowplaying' (no login, no root)"
  systemctl daemon-reload
  systemctl enable nowplaying >/dev/null 2>&1
  if [ "$REBOOT" = "0" ]; then
    systemctl restart nowplaying
    sleep 2
    if systemctl is-active --quiet nowplaying; then ok "running!"; else warn "didn't start, check:  sudo display logs"; fi
  else
    ok "enabled, it'll start on its own after the reboot"
  fi
fi

say ""
say "${G}${B}  All done!${N}"
say ""
say "  Try these:"
say "    display list            ${C}# see all the themes${N}"
say "    sudo display miami      ${C}# switch theme${N}"
say "    sudo display sleep 4h   ${C}# how long before the monster gets drowsy${N}"
say "    sudo display logs       ${C}# if something looks off${N}"
say "  (TAB completion kicks in once u open a new terminal)"
say ""
if [ "$REBOOT" = "1" ] && [ "$SKIP_SYSTEM" != "1" ]; then
  # no terminal (some automation) and nobody said ASSUME_YES -> never reboot on our own
  if [ -z "$TTY" ] && [ "${ASSUME_YES:-0}" != "1" ]; then
    say "  reboot to load the screen driver:  sudo reboot"
  elif yes_no "The screen driver needs a reboot to load. Reboot now?" "Y"; then
    say "  rebooting... the monster will say hi in a minute"
    reboot
  else
    say "  ok, reboot whenever (sudo reboot)"
  fi
fi

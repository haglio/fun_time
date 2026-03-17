#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# main.sh (Git Bash on Windows) -> AutoHotkey v2 controller
###############################################################################

### ===================== CONFIG =====================

VLC_EXE="/c/Program Files/VideoLAN/VLC/vlc.exe"
MFP_EXE="/c/Program Files/MultiFunPlayer-1.33.9-patreon/MultiFunPlayer.exe"
AHK_EXE="/c/Program Files/AutoHotkey/v2/AutoHotkey64.exe"   # AutoHotkey v2

WINSTON_DIR="/c/path/to/suite-root/videos/videos/2D/larkin/3_good_to_go"
PORTRAIT_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/portrait"
LANDSCAPE_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/landscape"

WEIRD_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/kinda_weird"

ROBOT_HAND_PY="/c/Users/Alex/miniconda3/pythonw.exe"
ROBOT_HAND_SCRIPT="/c/path/to/suite-root/projects/fun_time/robot_hand_listener.py"
BROKER_SCRIPT="/c/path/to/suite-root/projects/fun_time/broker.py"
ROBOT_HAND_CLIPS="/c/path/to/suite-root/projects/fun_time/clips"
ROBOT_HAND_AUDIO_SCRIPT="/c/path/to/suite-root/projects/fun_time/robot_hand_audio_companion.py"
ROBOT_HAND_AUDIO="/c/path/to/suite-root/projects/fun_time/audio"
ROBOT_HAND_MODE_FILE="/c/path/to/suite-root/projects/fun_time/state/robot_hand_mode.txt"
ROBOT_HAND_CMD_FILE="/c/path/to/suite-root/projects/fun_time/state/robot_hand_cmd.txt"

# Favorites CSV (2 columns only, both clickable in LibreOffice Calc)
FAVS_FILE="/c/path/to/suite-root/projects/fun_time/favs.csv"

VLC2_HTTP_PORT="8091"          # pid2 / portrait
VLC3_HTTP_PORT="8092"          # pid3 / landscape

### ===================== HELPERS =====================

need_file() { [[ -f "$1" ]] || { echo "Missing file: $1" >&2; exit 1; }; }

to_win_path() {
  local p="$1"
  if [[ "$p" =~ ^[A-Za-z]:\\ ]]; then
    printf '%s' "$p"
  else
    cygpath -w "$p"
  fi
}

need_file "$VLC_EXE"
need_file "$MFP_EXE"
need_file "$AHK_EXE"
need_file "$ROBOT_HAND_PY"
need_file "$ROBOT_HAND_SCRIPT"
need_file "$BROKER_SCRIPT"
need_file "$ROBOT_HAND_AUDIO_SCRIPT"

mkdir -p "$WEIRD_DIR"
touch "$FAVS_FILE"

mkdir -p "/c/path/to/suite-root/projects/fun_time/state"

VLC_WIN="$(to_win_path "$VLC_EXE")"
MFP_WIN="$(to_win_path "$MFP_EXE")"
AHK_WIN="$(to_win_path "$AHK_EXE")"

ROBOT_HAND_PY_WIN="$(to_win_path "$ROBOT_HAND_PY")"
ROBOT_HAND_SCRIPT_WIN="$(to_win_path "$ROBOT_HAND_SCRIPT")"
BROKER_SCRIPT_WIN="$(to_win_path "$BROKER_SCRIPT")"
ROBOT_HAND_CLIPS_WIN="$(to_win_path "$ROBOT_HAND_CLIPS")"
ROBOT_HAND_AUDIO_SCRIPT_WIN="$(to_win_path "$ROBOT_HAND_AUDIO_SCRIPT")"
ROBOT_HAND_AUDIO_WIN="$(to_win_path "$ROBOT_HAND_AUDIO")"
ROBOT_HAND_MODE_FILE_WIN="$(to_win_path "$ROBOT_HAND_MODE_FILE")"
ROBOT_HAND_CMD_FILE_WIN="$(to_win_path "$ROBOT_HAND_CMD_FILE")"

WINSTON_WIN="$(to_win_path "$WINSTON_DIR")"
PORTRAIT_WIN="$(to_win_path "$PORTRAIT_DIR")"
LANDSCAPE_WIN="$(to_win_path "$LANDSCAPE_DIR")"
WEIRD_WIN="$(to_win_path "$WEIRD_DIR")"
FAVS_WIN="$(to_win_path "$FAVS_FILE")"

# Password for VLC HTTP interface (printed so you can test in browser if needed)
VLC_HTTP_PASS="fun_time_$(date +%s)"

AHK_SCRIPT="./controller.ahk"
need_file "$AHK_SCRIPT"
AHK_SCRIPT_WIN="$(cygpath -aw "$AHK_SCRIPT")"

echo "Starting controller (AutoHotkey v2). Press Esc to close everything."
echo "VLC HTTP password (blank username): $VLC_HTTP_PASS"
echo "AHK script: $AHK_SCRIPT"
echo "Favorites CSV: $FAVS_FILE"

MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1 "$AHK_WIN" "$AHK_SCRIPT_WIN" \
  "$VLC_WIN" "$MFP_WIN" \
  "$WINSTON_WIN" "$PORTRAIT_WIN" "$LANDSCAPE_WIN" \
  "$WEIRD_WIN" "$FAVS_WIN" \
  "$VLC2_HTTP_PORT" "$VLC3_HTTP_PORT" \
  "$VLC_HTTP_PASS" \
  "$ROBOT_HAND_PY_WIN" "$ROBOT_HAND_SCRIPT_WIN" "$BROKER_SCRIPT_WIN" \
  "$ROBOT_HAND_CLIPS_WIN" "$ROBOT_HAND_AUDIO_SCRIPT_WIN" "$ROBOT_HAND_AUDIO_WIN" \
  "$ROBOT_HAND_MODE_FILE_WIN" "$ROBOT_HAND_CMD_FILE_WIN"
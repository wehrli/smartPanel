#!/usr/bin/env bash
# Screenshot suite (SP-2701): every dashboard page, in every demo scenario, light and
# dark, rendered offscreen through `smartpanel --snapshot` (a private gtk4-broadwayd,
# nothing appears on screen, never touches a running smartPanel).
#
#   ./scripts/screenshots.sh                         all of it into docs/screenshots/all/
#   ./scripts/screenshots.sh --out /tmp/shots        somewhere else
#   ./scripts/screenshots.sh --scenarios "busy alert" --pages "overview sessions:board" \
#                            --styles dark --size 1100x760 --jobs 3
#   ./scripts/screenshots.sh --readme                the handful README uses, into docs/screenshots/
#                                                    (plus bar.png, the top-bar item, via bar_preview.py)
#   ./scripts/screenshots.sh --list                  print what would be rendered
#
# Files are named <scenario>-<page>-<style>.png, ':' in a page written as '-'
# (busy-sessions-board-dark.png). Pages: overview sessions sessions:board spend system
# journal journal:ok journal:failed settings. 'journal:ok' / 'journal:failed' stand for
# the scenario's first successful / failed receipt (their ids are clock-relative, so
# they are looked up right before each shot; a scenario without one is skipped).
# Review by eye; don't commit the full set (it is ~160 PNGs).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${SMARTPANEL_PYTHON:-python3}"

OUT=""
SCENARIOS="busy calm alert empty many cold first notmux noswap"
PAGES="overview sessions sessions:board spend system journal journal:ok journal:failed settings"
STYLES="light dark"
SIZE="900x680"
JOBS=1
LIST=0
README=0

while (( $# )); do
  case "$1" in
    --out) OUT="$2"; shift ;;
    --scenarios) SCENARIOS="$2"; shift ;;
    --pages) PAGES="$2"; shift ;;
    --styles) STYLES="$2"; shift ;;
    --size) SIZE="$2"; shift ;;
    --jobs|-j) JOBS="$2"; shift ;;
    --list) LIST=1 ;;
    --readme) README=1 ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

# The README set: one line per shot, "scenario page style size name".
README_SHOTS=(
  "busy overview dark 1100x760 overview-dark"
  "busy overview light 1100x760 overview-light"
  "busy sessions dark 1100x760 sessions-cockpit-dark"
  "many sessions:board light 1100x760 sessions-board-light"
  "busy spend dark 1100x760 spend-dark"
  "alert system light 1100x760 system-alert-light"
)

jobs_list() {
  if (( README )); then
    printf '%s\n' "${README_SHOTS[@]}"
    return
  fi
  local s p st
  for s in $SCENARIOS; do
    for p in $PAGES; do
      for st in $STYLES; do
        echo "$s $p $st $SIZE $s-${p//:/-}-$st"
      done
    done
  done
}

if (( README )); then
  OUT="${OUT:-$ROOT/docs/screenshots}"
else
  OUT="${OUT:-$ROOT/docs/screenshots/all}"
fi

if (( LIST )); then
  jobs_list | awk -v out="$OUT" '{printf "%s/%s.png  (%s %s %s %s)\n", out, $5, $1, $2, $3, $4}'
  exit 0
fi

mkdir -p "$OUT"
# The full set is for review, not for git: keep it out of `git status`.
if [[ "$OUT" == "$ROOT/docs/screenshots/all" && ! -e "$OUT/.gitignore" ]]; then
  echo '*' > "$OUT/.gitignore"
fi
cd "$ROOT"

# One shot. Prints "ok <file>", "skip <file> <why>" or "FAIL <file>".
shoot() {
  local scenario="$1" page="$2" style="$3" size="$4" name="$5"
  local file="$OUT/$name.png" target="$page"
  case "$page" in
    journal:ok|journal:failed)
      local want="${page#journal:}"
      local id
      id="$("$PY" -c "
import sys
from smartpanel import demo
ok = sys.argv[2] == 'ok'
ids = [e.id for e in demo.build_state(sys.argv[1]).journal.entries
       if (e.outcome == 'journaled') == ok]
print(ids[0] if ids else '')" "$scenario" "$want")"
      if [[ -z "$id" ]]; then
        echo "skip $file (no $want receipt in '$scenario')"
        return 0
      fi
      target="journal:$id" ;;
  esac
  # Parallel jobs can race for the same private Broadway display number; the loser
  # fails to initialise GTK. Retrying (after a small random pause) settles it.
  local attempt
  for attempt in 1 2 3; do
    if "$PY" -m smartpanel --demo "$scenario" --snapshot "$target" --out "$file" \
          --style "$style" --size "$size" >/dev/null 2>"$file.log"; then
      rm -f "$file.log"
      echo "ok   $file"
      return 0
    fi
    sleep "0.$(( RANDOM % 9 + 1 ))"
  done
  echo "FAIL $file (log: $file.log)"
}
export -f shoot
export PY OUT

start=$SECONDS
if (( README )); then
  "$PY" "$ROOT/scripts/bar_preview.py" --out "$OUT/bar.png" >/dev/null && echo "ok   $OUT/bar.png"
fi
results="$(jobs_list | xargs -P "$JOBS" -L 1 bash -c 'shoot "$@"' _)"
echo "$results"
ok=$(grep -c '^ok' <<<"$results" || true)
skipped=$(grep -c '^skip' <<<"$results" || true)
failed=$(grep -c '^FAIL' <<<"$results" || true)
echo "$ok rendered, $skipped skipped, $failed failed in $((SECONDS - start)) s → $OUT"
(( failed == 0 ))

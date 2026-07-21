#!/usr/bin/env bash
# Quit every visible macOS app except the ones in KEEP_APPS, to free up
# unified memory (RAM/MPS) before running Chatterbox Local TTS or other
# heavy MPS workloads. Uses a graceful `quit` (System Events), so apps get
# the chance to prompt "save before closing?" instead of losing work.
set -euo pipefail

# Names as they appear in `System Events` (usually the .app name without
# ".app"). Add your terminal app here too if you don't want it closed.
KEEP_APPS=(
  "Docker Desktop"
  "Docker"
  "Visual Studio Code"
  "Code"
  "Finder"
  "Terminal"
  "iTerm2"
)

is_kept() {
  local app="$1"
  for keep in "${KEEP_APPS[@]}"; do
    [[ "$app" == "$keep" ]] && return 0
  done
  return 1
}

# "Available" = free + inactive pages (both are reclaimable by the OS on
# demand), which is what macOS actually treats as usable headroom — not just
# "Pages free", which alone chronically under-reports on macOS.
available_gb() {
  local page_size free_pages inactive_pages
  page_size=$(vm_stat | head -1 | grep -o '[0-9]*')
  free_pages=$(vm_stat | awk '/Pages free/ {gsub("\\.", "", $3); print $3}')
  inactive_pages=$(vm_stat | awk '/Pages inactive/ {gsub("\\.", "", $3); print $3}')
  echo "scale=2; ($free_pages + $inactive_pages) * $page_size / 1024 / 1024 / 1024" | bc
}

total_gb() {
  echo "scale=2; $(sysctl -n hw.memsize) / 1024 / 1024 / 1024" | bc
}

print_mem_report() {
  local label="$1"
  local total avail
  total=$(total_gb)
  avail=$(available_gb)
  echo "[$label] RAM total: ${total} GB | disponible (libre+inactiva): ${avail} GB"
}

TOTAL_GB=$(total_gb)
BEFORE_GB=$(available_gb)
echo "Memoria antes de cerrar apps:"
print_mem_report "ANTES"
echo

running_apps=$(osascript -e '
tell application "System Events"
  set procList to name of every process whose background only is false
end tell
' | tr ',' '\n' | sed 's/^ *//; s/ *$//')

closed=()
kept=()

while IFS= read -r app; do
  [[ -z "$app" ]] && continue
  if is_kept "$app"; then
    kept+=("$app")
    continue
  fi
  echo "Quitting: $app"
  osascript -e "tell application \"$app\" to quit" >/dev/null 2>&1 || true
  closed+=("$app")
done <<< "$running_apps"

echo
echo "Kept running: ${kept[*]}"
echo "Closed: ${closed[*]:-(none)}"

# Give macOS a moment to actually reclaim pages after the quits above —
# the numbers right at process exit are usually still mid-update.
sleep 3

echo
echo "Memoria despues de cerrar apps:"
print_mem_report "DESPUES"
AFTER_GB=$(available_gb)
DELTA_GB=$(echo "scale=2; $AFTER_GB - $BEFORE_GB" | bc)
echo
echo "RAM total del sistema: ${TOTAL_GB} GB"
echo "Disponible antes: ${BEFORE_GB} GB -> despues: ${AFTER_GB} GB (liberado: ${DELTA_GB} GB)"

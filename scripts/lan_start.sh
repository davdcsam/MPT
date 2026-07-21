#!/usr/bin/env bash
# Arranca el stack completo (API + WebUI) con stdout/stderr combinados en
# storage/logs/lan_start.out.log, y deja scripts/task_progress.sh siguiendo
# ese log en segundo plano. Sin esto, si el stack se arranca a mano (o desde
# otra herramienta) con el log redirigido a otro sitio, task_progress.sh no
# tiene nada que leer y storage/tasks/<task_id>/progress.log nunca aparece.
#
# Uso:
#   scripts/lan_start.sh          # arranca API + WebUI + task_progress.sh
#   scripts/lan_start.sh --stop   # mata los procesos que arranco este script
set -euo pipefail

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$CURRENT_DIR/.." && pwd)
LOG_FILE="$PROJECT_DIR/storage/logs/lan_start.out.log"
PID_FILE="$PROJECT_DIR/storage/logs/lan_start.pids"

if [ "${1:-}" = "--stop" ]; then
  if [ ! -f "$PID_FILE" ]; then
    echo "No hay $PID_FILE, nada que detener."
    exit 0
  fi
  while IFS= read -r pid; do
    [ -z "$pid" ] && continue
    # webui.sh no hace exec del streamlit final, asi que mata tambien sus
    # hijos directos (pkill -P) o el proceso de streamlit queda huerfano.
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  echo "Procesos detenidos."
  exit 0
fi

if [ -f "$PID_FILE" ]; then
  echo "Ya existe $PID_FILE - si el stack sigue corriendo, usa '$0 --stop' primero." >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR/storage/logs"
: > "$LOG_FILE"
> "$PID_FILE"

echo "[lan_start] arrancando API (main.py) -> $LOG_FILE"
(cd "$PROJECT_DIR" && exec "$PROJECT_DIR/.venv/bin/python" main.py) >> "$LOG_FILE" 2>&1 &
echo "$!" >> "$PID_FILE"

echo "[lan_start] arrancando WebUI (streamlit) -> $LOG_FILE"
(cd "$PROJECT_DIR" && MPT_WEBUI_HOST="${MPT_WEBUI_HOST:-0.0.0.0}" exec sh "$PROJECT_DIR/webui.sh") >> "$LOG_FILE" 2>&1 &
echo "$!" >> "$PID_FILE"

echo "[lan_start] arrancando scripts/task_progress.sh"
MPT_LOG_FILE="$LOG_FILE" "$CURRENT_DIR/task_progress.sh" >> "$PROJECT_DIR/storage/logs/task_progress.out.log" 2>&1 &
echo "$!" >> "$PID_FILE"

echo "[lan_start] listo. Progreso por tarea en storage/tasks/<task_id>/progress.log"
echo "[lan_start] para detener todo: $0 --stop"

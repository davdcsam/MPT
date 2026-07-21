#!/usr/bin/env bash
# Separa el log combinado de la API/WebUI (storage/logs/lan_start.out.log,
# que mezcla API + WebUI + salida cruda de librerias como Chatterbox/torch)
# en un log detallado por tarea: storage/tasks/<task_id>/task.log
#
# No modifica nada del proyecto principal: solo lee el log ya existente y
# copia cada linea al archivo de la tarea correspondiente, tal cual aparece
# (barras de progreso, warnings de librerias, todo incluido), quitando
# unicamente los codigos ANSI de color para que sea legible en un editor.
#
# Deteccion de limite de tarea: usa la linea que ya emite task.py al
# arrancar cada tarea ("... start task: <uuid>, stop_at: ..."). Todo lo que
# aparece despues de esa linea se atribuye a esa tarea, hasta que empiece
# la siguiente. Si hay tareas corriendo en paralelo (max_concurrent_tasks
# en config.toml > 1), la salida cruda de librerias (que no incluye
# task_id) puede quedar mal atribuida entre tareas simultaneas - esta
# limitacion es inherente a que ese log combinado no etiqueta cada linea
# con el task_id.
#
# Uso:
#   scripts/split_task_logs.sh            # sigue el log en vivo (como tail -f)
#   scripts/split_task_logs.sh --once      # procesa lo que ya existe y termina
#
# Para dejarlo corriendo en segundo plano junto al resto del stack:
#   nohup scripts/split_task_logs.sh > storage/logs/split_task_logs.out.log 2>&1 & disown

set -euo pipefail

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$CURRENT_DIR/.." && pwd)
LOG_FILE="${MPT_LOG_FILE:-$PROJECT_DIR/storage/logs/lan_start.out.log}"
TASKS_DIR="$PROJECT_DIR/storage/tasks"

MODE="follow"
if [ "${1:-}" = "--once" ]; then
  MODE="once"
fi

if [ ! -f "$LOG_FILE" ]; then
  echo "No existe $LOG_FILE" >&2
  exit 1
fi

mkdir -p "$TASKS_DIR"

process_stream() {
  local current_task=""
  local clean_line

  while IFS= read -r line; do
    # Quita codigos ANSI de color (loguru colorize=True los escribe crudos
    # en el log de archivo/consola redirigida).
    clean_line=$(printf '%s' "$line" | sed -E 's/\x1b\[[0-9;]*m//g')

    if [[ "$clean_line" == *"start task: "* ]]; then
      task_id=$(printf '%s' "$clean_line" | sed -n 's/.*start task: \([a-f0-9-]*\),.*/\1/p')
      if [ -n "$task_id" ]; then
        current_task="$task_id"
        mkdir -p "$TASKS_DIR/$current_task"
        echo "[split_task_logs] tarea detectada: $current_task -> storage/tasks/$current_task/task.log"
      fi
    fi

    if [ -n "$current_task" ]; then
      printf '%s\n' "$clean_line" >> "$TASKS_DIR/$current_task/task.log"
    fi
  done
}

if [ "$MODE" = "once" ]; then
  process_stream < "$LOG_FILE"
  echo "[split_task_logs] listo (--once)."
else
  echo "[split_task_logs] siguiendo $LOG_FILE ... (Ctrl+C para detener)"
  tail -n +1 -f "$LOG_FILE" | process_stream
fi

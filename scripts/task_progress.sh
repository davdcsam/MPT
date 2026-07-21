#!/usr/bin/env bash
# Version simplificada de split_task_logs.sh: en vez de guardar TODO el
# ruido del log (barras de progreso, warnings de libreria, etc.), esto
# guarda solo la etapa actual y el avance de cada tarea, una linea por
# cambio de estado, en storage/tasks/<task_id>/progress.log
#
# Ejemplo de salida:
#   [2026-07-21 00:31:28] tarea iniciada
#   [2026-07-21 00:31:28] etapa 1/6: generando guion
#   [2026-07-21 00:31:28] etapa 3/6: generando audio
#   [2026-07-21 00:40:51] audio: 0/166 segmentos (0%)
#   [2026-07-21 00:41:05] audio: 10/166 segmentos (6%)
#   ...
#   [2026-07-21 00:XX:XX] etapa 4/6: generando subtitulos
#   [2026-07-21 00:XX:XX] etapa 5/6: descargando videos (pexels)
#   [2026-07-21 00:XX:XX] descarga de videos: 12 candidato(s) encontrado(s) para cubrir la duracion necesaria
#   [2026-07-21 00:XX:XX] descarga de videos: 1/12 candidatos descargados (8%)
#   [2026-07-21 00:XX:XX] etapa 5/6: descarga completa, 8 video(s) descargado(s) (de los candidatos disponibles)
#   [2026-07-21 00:XX:XX] etapa 6/6: combinando video 1/2 (50%)
#   [2026-07-21 00:XX:XX] etapa 6/6: renderizando video 1/2 (50%)
#   [2026-07-21 00:XX:XX] tarea completada: 2 video(s) generado(s)
#
# Nota sobre "candidatos": la descarga se detiene por duracion cubierta,
# no por una cantidad fija de clips, asi que el total no es una meta
# exacta de cuantos SE VAN a descargar, sino cuantos hay disponibles como
# maximo (normalmente descarga menos que ese total).
#
# No modifica nada del proyecto principal: solo lee el log combinado ya
# existente (storage/logs/lan_start.out.log), igual que split_task_logs.sh.
#
# Uso:
#   scripts/task_progress.sh            # sigue el log en vivo
#   scripts/task_progress.sh --once      # procesa lo existente y termina
#
# Para dejarlo corriendo en segundo plano junto al resto del stack:
#   nohup scripts/task_progress.sh > storage/logs/task_progress.out.log 2>&1 & disown

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

emit() {
  local task_id="$1" msg="$2" ts="$3"
  mkdir -p "$TASKS_DIR/$task_id"
  echo "[$ts] $msg" >> "$TASKS_DIR/$task_id/progress.log"
}

# Lee params.video_count desde script.json (lo escribe task.py bastante
# antes de la etapa de combinar/renderizar), para poder mostrar
# "video X/N" en vez de solo "video X". Si jq no esta disponible o el
# campo no existe, devuelve vacio y el llamador cae al formato sin total.
get_video_count() {
  local script_json="$TASKS_DIR/$1/script.json"
  if [ -f "$script_json" ] && command -v jq >/dev/null 2>&1; then
    jq -r '.params.video_count // empty' "$script_json" 2>/dev/null
  fi
}

process_stream() {
  local current_task="" clean_line task_id n idx d t pct line_ts is_doc

  while IFS= read -r line; do
    clean_line=$(printf '%s' "$line" | sed -E 's/\x1b\[[0-9;]*m//g')
    # Usa el timestamp que ya trae la linea del log (formato loguru:
    # "YYYY-MM-DD HH:MM:SS | ..."). Si la linea no trae uno (p.ej. salida
    # cruda de Chatterbox/torch), reusa el ultimo timestamp visto.
    if [[ "$clean_line" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}) ]]; then
      line_ts="${BASH_REMATCH[1]}"
    fi
    line_ts="${line_ts:-$(date '+%Y-%m-%d %H:%M:%S')}"

    if [[ "$clean_line" == *"start task: "* ]]; then
      task_id=$(printf '%s' "$clean_line" | sed -n 's/.*start task: \([a-f0-9-]*\),.*/\1/p')
      if [ -n "$task_id" ]; then
        current_task="$task_id"
        mkdir -p "$TASKS_DIR/$current_task"
        : > "$TASKS_DIR/$current_task/progress.log"
        eval "total_${current_task//-/_}=0"
        eval "done_${current_task//-/_}=0"
        eval "doc_${current_task//-/_}=0"
        emit "$current_task" "tarea iniciada" "$line_ts"
      fi
      continue
    fi

    [ -z "$current_task" ] && continue

    case "$clean_line" in
      *"## generating video script"*)
        emit "$current_task" "etapa 1/6: generando guion" "$line_ts" ;;
      *"## generating video terms"*)
        emit "$current_task" "etapa 2/6: generando terminos de busqueda" "$line_ts" ;;
      *"## generating audio and materials per segment"*)
        # Modo documental: el audio y los clips de video se generan juntos,
        # segmento a segmento. Marca doc_ para que el bloque de chatterbox-local
        # (mas abajo) no pise este conteo con el suyo propio por-llamada.
        eval "doc_${current_task//-/_}=1"
        emit "$current_task" "etapa 3/6: generando audio y video por segmento (modo documental)" "$line_ts" ;;
      *"documentary sync: planned "*" segments from"*)
        n=$(printf '%s' "$clean_line" | sed -n 's/.*planned \([0-9]*\) segments from.*/\1/p')
        if [ -n "$n" ]; then
          eval "total_${current_task//-/_}=$n"
          eval "done_${current_task//-/_}=0"
          eval "dl_total_${current_task//-/_}=$n"
          eval "dl_${current_task//-/_}=0"
          emit "$current_task" "audio: 0/$n segmentos (0%)" "$line_ts"
        fi
        ;;
      *"documentary sync: audio segment "*)
        d=$(printf '%s' "$clean_line" | sed -n 's#.*audio segment \([0-9]*\)/\([0-9]*\) synthesized.*#\1#p')
        t=$(printf '%s' "$clean_line" | sed -n 's#.*audio segment \([0-9]*\)/\([0-9]*\) synthesized.*#\2#p')
        if [ -n "$d" ] && [ -n "$t" ] && [ "$t" -gt 0 ]; then
          eval "done_${current_task//-/_}=$d"
          pct=$(( d * 100 / t ))
          if [ $(( d % 5 )) -eq 0 ] || [ "$d" -eq "$t" ]; then
            emit "$current_task" "audio: $d/$t segmentos ($pct%)" "$line_ts"
          fi
        fi
        ;;
      *"documentary sync: video segment "*)
        d=$(printf '%s' "$clean_line" | sed -n 's#.*video segment \([0-9]*\)/\([0-9]*\) downloaded.*#\1#p')
        t=$(printf '%s' "$clean_line" | sed -n 's#.*video segment \([0-9]*\)/\([0-9]*\) downloaded.*#\2#p')
        if [ -n "$d" ] && [ -n "$t" ] && [ "$t" -gt 0 ]; then
          eval "dl_${current_task//-/_}=$d"
          pct=$(( d * 100 / t ))
          if [ $(( d % 5 )) -eq 0 ] || [ "$d" -eq "$t" ]; then
            emit "$current_task" "descarga de videos: $d/$t clips de segmento descargados ($pct%)" "$line_ts"
          fi
        fi
        ;;
      *"## generating audio"*)
        emit "$current_task" "etapa 3/6: generando audio" "$line_ts" ;;
      *"start chatterbox-local tts"*"segments:"*)
        eval "is_doc=\${doc_${current_task//-/_}:-0}"
        if [ "$is_doc" != "1" ]; then
          n=$(printf '%s' "$clean_line" | sed -n 's/.*segments: \([0-9]*\).*/\1/p')
          if [ -n "$n" ]; then
            eval "total_${current_task//-/_}=$n"
            eval "done_${current_task//-/_}=0"
            emit "$current_task" "audio: 0/$n segmentos (0%)" "$line_ts"
          fi
        fi
        ;;
      *"Reference mel length"*)
        eval "is_doc=\${doc_${current_task//-/_}:-0}"
        if [ "$is_doc" != "1" ]; then
          eval "t=\${total_${current_task//-/_}:-0}"
          if [ "$t" -gt 0 ]; then
            eval "done_${current_task//-/_}=\$(( \${done_${current_task//-/_}:-0} + 1 ))"
            eval "d=\${done_${current_task//-/_}}"
            pct=$(( d * 100 / t ))
            # Solo escribe cada 5 segmentos (o el ultimo) para no inundar el
            # archivo en tareas con muchos segmentos cortos.
            if [ $(( d % 5 )) -eq 0 ] || [ "$d" -eq "$t" ]; then
              emit "$current_task" "audio: $d/$t segmentos ($pct%)" "$line_ts"
            fi
          fi
        fi
        ;;
      *"## generating subtitle, provider"*)
        emit "$current_task" "etapa 4/6: generando subtitulos" "$line_ts" ;;
      *"## preprocess local materials"*)
        eval "dl_${current_task//-/_}=0"
        eval "dl_total_${current_task//-/_}="
        emit "$current_task" "etapa 5/6: usando materiales de video locales" "$line_ts" ;;
      *"## downloading videos from "*)
        src=$(printf '%s' "$clean_line" | sed -n 's/.*downloading videos from \(.*\)/\1/p')
        eval "dl_${current_task//-/_}=0"
        eval "dl_total_${current_task//-/_}="
        emit "$current_task" "etapa 5/6: descargando videos ($src)" "$line_ts" ;;
      *"found total"*"video"*)
        n=$(printf '%s' "$clean_line" | sed -n 's/.*found total[^:]*: \([0-9]*\),.*/\1/p')
        if [ -n "$n" ]; then
          eval "dl_total_${current_task//-/_}=$n"
          emit "$current_task" "descarga de videos: $n candidato(s) encontrado(s) para cubrir la duracion necesaria" "$line_ts"
        fi
        ;;
      *"video saved: "*)
        eval "dl_${current_task//-/_}=\$(( \${dl_${current_task//-/_}:-0} + 1 ))"
        eval "d=\${dl_${current_task//-/_}}"
        eval "t=\${dl_total_${current_task//-/_}:-}"
        if [ -n "$t" ] && [ "$t" -gt 0 ] 2>/dev/null; then
          pct=$(( d * 100 / t ))
          emit "$current_task" "descarga de videos: $d/$t candidatos descargados ($pct%)" "$line_ts"
        else
          emit "$current_task" "descarga de videos: $d descargado(s)" "$line_ts"
        fi
        ;;
      *"failed to download video"*|*"failed to download ordered video"*)
        emit "$current_task" "descarga de videos: fallo al descargar un video, reintentando con el siguiente" "$line_ts" ;;
      *"downloaded "*"videos"*)
        n=$(printf '%s' "$clean_line" | sed -n 's/.*downloaded \([0-9]*\) .*videos.*/\1/p')
        emit "$current_task" "etapa 5/6: descarga completa, ${n:-?} video(s) descargado(s) (de los candidatos disponibles)" "$line_ts" ;;
      *"## combining video: "*)
        idx=$(printf '%s' "$clean_line" | sed -n 's/.*combining video: \([0-9]*\).*/\1/p')
        eval "tv=\${total_videos_${current_task//-/_}:-}"
        if [ -z "$tv" ]; then
          tv=$(get_video_count "$current_task")
          [ -n "$tv" ] && eval "total_videos_${current_task//-/_}=$tv"
        fi
        if [[ -n "$tv" && "$tv" =~ ^[0-9]+$ && "$tv" -gt 0 ]]; then
          pct=$(( idx * 100 / tv ))
          emit "$current_task" "etapa 6/6: combinando video $idx/$tv ($pct%)" "$line_ts"
        else
          emit "$current_task" "etapa 6/6: combinando video $idx" "$line_ts"
        fi
        ;;
      *"## generating video: "*)
        idx=$(printf '%s' "$clean_line" | sed -n 's/.*generating video: \([0-9]*\).*/\1/p')
        eval "tv=\${total_videos_${current_task//-/_}:-}"
        if [ -z "$tv" ]; then
          tv=$(get_video_count "$current_task")
          [ -n "$tv" ] && eval "total_videos_${current_task//-/_}=$tv"
        fi
        if [[ -n "$tv" && "$tv" =~ ^[0-9]+$ && "$tv" -gt 0 ]]; then
          pct=$(( idx * 100 / tv ))
          emit "$current_task" "etapa 6/6: renderizando video $idx/$tv ($pct%)" "$line_ts"
        else
          emit "$current_task" "etapa 6/6: renderizando video $idx" "$line_ts"
        fi
        ;;
      *"finished, generated "*)
        n=$(printf '%s' "$clean_line" | sed -n 's/.*generated \([0-9]*\) videos\..*/\1/p')
        emit "$current_task" "tarea completada: ${n:-?} video(s) generado(s)" "$line_ts"
        current_task=""
        ;;
    esac
  done
}

if [ "$MODE" = "once" ]; then
  process_stream < "$LOG_FILE"
  echo "[task_progress] listo (--once)."
else
  echo "[task_progress] siguiendo $LOG_FILE ... (Ctrl+C para detener)"
  tail -n +1 -f "$LOG_FILE" | process_stream
fi

#!/usr/bin/env sh
# Levanta MoneyPrinterTurbo accesible en toda la red local como
# http://MoneyPrinterTurbo.local:8501 (WebUI) y :8080 (API).
#
# No modifica archivos del proyecto principal:
# - usa el .venv del proyecto tal cual para la API y el WebUI
# - crea su propio entorno aislado en lan/.venv solo para la
#   dependencia mDNS (zeroconf), sin tocar pyproject.toml/uv.lock

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$CURRENT_DIR/.." && pwd)

if [ ! -x "$CURRENT_DIR/.venv/bin/python" ]; then
  echo "***** Creando entorno aislado para mDNS en lan/.venv *****"
  python3 -m venv "$CURRENT_DIR/.venv" || exit 1
  "$CURRENT_DIR/.venv/bin/pip" install --quiet zeroconf || exit 1
fi

cleanup() {
  echo ""
  echo "***** Deteniendo mDNS, API y WebUI *****"
  [ -n "$MDNS_PID" ] && kill "$MDNS_PID" 2>/dev/null
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT

"$CURRENT_DIR/.venv/bin/python" "$CURRENT_DIR/mdns_advertise.py" &
MDNS_PID=$!

echo "***** Iniciando API (ya escucha en 0.0.0.0:8080 por defecto) *****"
(cd "$PROJECT_DIR" && "$PROJECT_DIR/.venv/bin/python" main.py) &
API_PID=$!

echo "***** Iniciando WebUI en 0.0.0.0 para acceso desde la red local *****"
# No delegamos en webui.sh: ese script usa la MISMA variable para el bind
# (--server.address) y para la direccion que el navegador usa al reconectar
# el websocket (--browser.serverAddress). Ponerla en 0.0.0.0 hace que el
# navegador intente reconectar a ws://0.0.0.0:PUERTO/, lo cual es invalido
# y causa que la carga tarde varios segundos (reintentos hasta el timeout).
# Aqui separamos ambas: bind en 0.0.0.0, pero el navegador usa el hostname
# mDNS real (MoneyPrinterTurbo.local).
WEBUI_PORT="${MPT_WEBUI_PORT:-8501}"
WEBUI_PORT=$("$PROJECT_DIR/.venv/bin/python" - <<PY
import socket
preferred = $WEBUI_PORT
candidates = [preferred] + [p for p in range(8502, 8600) if p != preferred]
for port in candidates:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            continue
        print(port)
        break
PY
)

if [ -z "$WEBUI_PORT" ]; then
  echo "***** No se encontro puerto disponible para el WebUI en 8501-8599 *****"
  exit 1
fi

echo "***** WebUI address: http://MoneyPrinterTurbo.local:$WEBUI_PORT *****"
# fileWatcherType=none: Streamlit's default file watcher walks every
# imported module's __path__ (including torch's, which is unusually
# expensive to introspect) to detect source changes, and it keeps polling
# for as long as the server runs, not just at startup. We don't need
# hot-reload for a LAN deployment, so disabling it removes that recurring
# overhead entirely instead of just at launch.
"$PROJECT_DIR/.venv/bin/python" -m streamlit run "$PROJECT_DIR/webui/Main.py" \
  --server.address=0.0.0.0 \
  --server.port="$WEBUI_PORT" \
  --browser.serverAddress=MoneyPrinterTurbo.local \
  --browser.gatherUsageStats=False \
  --server.showEmailPrompt=False \
  --server.enableCORS=True \
  --server.fileWatcherType=none

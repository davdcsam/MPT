#!/usr/bin/env python3
"""Anuncia MoneyPrinterTurbo.local por mDNS/Bonjour en la red local.

No forma parte del proyecto principal: vive en lan/ y usa su propio
entorno virtual (lan/.venv) para no tocar pyproject.toml ni uv.lock.
"""
import signal
import socket
import sys
import time

from zeroconf import IPVersion, ServiceInfo, Zeroconf

HOSTNAME = "MoneyPrinterTurbo"
# Puerto "principal" solo a efectos informativos del anuncio del servicio;
# el registro del hostname (MoneyPrinterTurbo.local -> IP) sirve para
# cualquier puerto (webui, API, etc.).
ADVERTISED_PORT = 8501


def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def main() -> None:
    ip = get_lan_ip()
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    info = ServiceInfo(
        "_http._tcp.local.",
        f"{HOSTNAME}._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=ADVERTISED_PORT,
        server=f"{HOSTNAME}.local.",
    )
    zc.register_service(info)
    print(f"***** Anunciando {HOSTNAME}.local -> {ip} por mDNS (Ctrl+C para detener) *****")
    sys.stdout.flush()

    stop = {"flag": False}

    def handle_sig(*_args):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        zc.unregister_service(info)
        zc.close()


if __name__ == "__main__":
    main()

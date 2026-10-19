"""Dashboard web: ``python3 -m can_honda web [arquivos|pastas] [--port 8090]``.

Servidor stdlib (``http.server``). Só serve os arquivos passados na linha de
comando (arquivos, globs, ou ``*.log``/``*.csv`` das pastas passadas;
padrão ``~/claude/can*.log``) — o navegador escolhe
pelo **nome**, nunca por caminho.

Modo online: ``--live can0`` (SocketCAN) e/ou ``--replay log`` (reemite um log em
tempo real, para testar sem o carro). Fontes ao vivo aparecem na mesma lista.

Telemetria (``--gps /dev/ttyACM0`` e/ou ``--live can0``): painel em ``/telemetry``
com mapa (Leaflet em ``static/vendor``, tiles do OpenStreetMap) e instrumentos.

API:
  GET /api/telemetry                     estado atual CAN + GPS + derivadas
  GET /api/track?since=SEQ               pontos do trajeto depois de SEQ
  GET /api/files                         fontes: logs + interfaces ao vivo
  GET /api/overview?file=NOME            estatísticas, IDs, ciclos de motor, sinais
  GET /api/chart?file=NOME[&x0&x1&bins]  séries para os gráficos (recorte do zoom)
"""

import glob
import json
import os
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .analysis import Analysis
from .dbc import load_dbc

STATIC = os.path.join(os.path.dirname(__file__), "static")


VENDOR = os.path.join(STATIC, "vendor")
CTYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".png": "image/png", ".html": "text/html; charset=utf-8"}


def vendor_file(rel):
    """Caminho de um arquivo dentro de ``static/vendor`` — ou None (sem escapar da pasta)."""
    p = os.path.realpath(os.path.join(VENDOR, rel))
    return p if p.startswith(os.path.realpath(VENDOR) + os.sep) and os.path.isfile(p) else None


class Library:
    """Logs disponíveis + cache de análises (invalidada pelo mtime) + fontes ao vivo."""

    def __init__(self, sources, dbc_path, live=(), telemetry=None, gps=None):
        self.telemetry = telemetry
        self.gps = gps
        self.dbc_path = dbc_path
        self.sources = sources
        self.live = {src.name: src for src in live}
        self._cache = {}
        self._locks = {}
        self._guard = threading.Lock()

    def files(self):
        found = {}
        for src in self.sources:
            src = os.path.expanduser(src)
            if os.path.isdir(src):
                paths = sorted(glob.glob(os.path.join(src, "*.log")) + glob.glob(os.path.join(src, "*.csv")))
            else:
                paths = sorted(glob.glob(src)) if glob.has_magic(src) else [src]
            for p in paths:
                if os.path.isfile(p):
                    found.setdefault(os.path.basename(p), os.path.abspath(p))
        return found

    def get(self, name):
        """(analysis, lock) — o lock protege as fontes ao vivo da thread leitora."""
        src = self.live.get(name)
        if src is not None:
            return src.analysis, src.lock
        path = self.files().get(name)
        if path is None:
            raise KeyError(name)
        with self._guard:
            lock = self._locks.setdefault(path, threading.Lock())
        with lock:
            a = self._cache.get(path)
            if a is None or a.mtime != os.path.getmtime(path):
                a = self._cache[path] = Analysis(path, load_dbc(self.dbc_path))
            return a, nullcontext()


def make_handler(lib: Library):
    class Handler(BaseHTTPRequestHandler):
        server_version = "can-honda-web"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html", "/telemetry"):
                    page = "telemetry.html" if u.path == "/telemetry" else "index.html"
                    with open(os.path.join(STATIC, page), "rb") as fh:
                        return self._send(200, fh.read(), "text/html; charset=utf-8")
                if u.path.startswith("/vendor/"):
                    p = vendor_file(u.path[len("/vendor/"):])
                    if p is None:
                        return self._send(404, {"error": "não encontrado"})
                    with open(p, "rb") as fh:
                        return self._send(200, fh.read(), CTYPES.get(os.path.splitext(p)[1],
                                                                     "application/octet-stream"))
                if u.path == "/api/telemetry":
                    tel = lib.telemetry.snapshot() if lib.telemetry else None
                    return self._send(200, {
                        "telemetry": tel,
                        "gps": lib.gps.snapshot() if lib.gps else None,
                        "can_sources": [src.info() for src in lib.live.values()],
                    })
                if u.path == "/api/track":
                    if lib.telemetry is None:
                        return self._send(200, {"seq": 0, "points": []})
                    return self._send(200, lib.telemetry.track(int(q.get("since", 0))))
                if u.path == "/api/files":
                    loaded = {os.path.basename(p) for p in lib._cache}
                    live = [dict(src.info(), live=True) for src in lib.live.values()]
                    return self._send(200, live + [
                        {"name": n, "size": os.path.getsize(p), "loaded": n in loaded, "live": False}
                        for n, p in sorted(lib.files().items())])
                if u.path == "/api/overview":
                    name = q.get("file", "")
                    a, lock = lib.get(name)
                    with lock:
                        o = a.overview()
                    if name in lib.live:
                        o["live"] = lib.live[name].info()
                    return self._send(200, o)
                if u.path == "/api/chart":
                    a, lock = lib.get(q.get("file", ""))
                    x0 = float(q["x0"]) if "x0" in q else None
                    x1 = float(q["x1"]) if "x1" in q else None
                    bins = max(50, min(int(q.get("bins", 800)), 3000))
                    with lock:
                        data = a.chart_data(x0, x1, bins)
                    return self._send(200, data)
                return self._send(404, {"error": "não encontrado"})
            except KeyError as e:
                return self._send(404, {"error": f"arquivo desconhecido: {e}"})
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})

    return Handler


def serve(sources, dbc_path, host="127.0.0.1", port=8090,
          live_ifaces=(), replays=(), retention=600.0, replay_rate=1000.0,
          gps_device=None, gps_replay=None, gps_record_dir=None):
    from . import gps as gpsmod
    from .live import replay_source, socketcan_source
    from .telemetry import Telemetry
    db = load_dbc(dbc_path)
    live = [socketcan_source(i, db, retention) for i in live_ifaces]
    live += [replay_source(os.path.expanduser(p), db, retention, replay_rate) for p in replays]
    telemetry = gps = None
    if live or gps_device or gps_replay:
        telemetry = Telemetry(db)
        if live:
            live[0].listeners.append(telemetry.feed_frame)   # 1ª fonte ao vivo = o carro
    if gps_device:
        record = None
        if gps_record_dir:
            os.makedirs(os.path.expanduser(gps_record_dir), exist_ok=True)
            record = os.path.join(os.path.expanduser(gps_record_dir),
                                  time.strftime("gps-nmea-%Y%m%d-%H%M%S.log"))
        gps = gpsmod.serial_source(gps_device, record, telemetry.on_fix)
    elif gps_replay:
        gps = gpsmod.replay_source(os.path.expanduser(gps_replay), telemetry.on_fix)
    lib = Library(sources, dbc_path, live, telemetry, gps)
    names = lib.files()
    if not names and not live and not gps:
        raise SystemExit("nenhum log encontrado em: " + ", ".join(sources))
    for src in live:
        src.start()
    if gps:
        gps.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(lib))
    print(f"dashboard em http://{host}:{port}/", flush=True)
    if telemetry:
        print(f"  telemetria: http://{host}:{port}/telemetry", flush=True)
    for src in live:
        print(f"  ao vivo: {src.name} (retenção {retention:.0f} s)", flush=True)
    if gps:
        print(f"  gps: {gps.name}" + (f" (gravando {gps.record_path})" if gps.record_path else ""), flush=True)
    for n in sorted(names):
        print(f"  arquivo: {n}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for src in live:
            src.stop()
        if gps:
            gps.stop()
        httpd.server_close()

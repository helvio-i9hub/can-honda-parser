"""Fontes ao vivo para o dashboard: SocketCAN (``can0``) ou replay de log.

Uma thread por fonte lê frames e alimenta uma ``Analysis`` incremental com
retenção (padrão 600 s). Se a interface não existir ou cair, a thread tenta
de novo a cada 2 s e o motivo aparece no dashboard.

O replay (``--replay arquivo``) reemite um log em ritmo de tempo real, com
timestamp do relógio — serve para ver o modo online sem o carro. Os logs
reais não têm timestamp, então o ritmo é uma taxa fixa (padrão 1000
frames/s, a ordem de grandeza do F-CAN do Civic).
"""

import os
import threading
import time
from dataclasses import replace

from .analysis import Analysis
from .frames import read_frames
from .socketcan import live_frames

RETRY_S = 2.0
TRIM_EVERY_S = 1.0


def replay_frames(path, rate=1000.0, loop=True):
    """Gera os frames de ``path`` com ``ts`` = agora, a ``rate`` frames/s."""
    n = 0
    t0 = time.time()
    while True:
        for f in read_frames(path):
            n += 1
            due = t0 + n / rate
            now = time.time()
            if due > now:
                time.sleep(due - now)
            yield replace(f, ts=time.time(), iface="replay", line_no=n)
        if not loop:
            return


class LiveSource:
    def __init__(self, name, frames_factory, db, retention=600.0, kind="socketcan"):
        self.name = name
        self.kind = kind
        self.lock = threading.Lock()
        self.analysis = Analysis(db=db, name=name, retention=retention)
        self.retention = retention
        self.status = "conectando"
        self.error = None
        self.started = time.time()
        self.last_frame = None
        self.fps = 0.0
        self._factory = frames_factory
        self._stop = threading.Event()
        self._count = 0
        self._thread = threading.Thread(target=self._loop, name=f"live-{name}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.status = "conectando"
                last_trim = last_rate = time.time()
                rate_n = 0
                for f in self._factory():
                    if self._stop.is_set():
                        return
                    with self.lock:
                        self.analysis.feed(f)
                        now = time.time()
                        if now - last_trim >= TRIM_EVERY_S:
                            self.analysis.trim()
                            last_trim = now
                    self.status, self.error, self.last_frame = "recebendo", None, now
                    rate_n += 1
                    if now - last_rate >= 1.0:
                        self.fps = rate_n / (now - last_rate)
                        rate_n, last_rate = 0, now
                self.status = "fim do replay" if self.kind == "replay" else "fonte encerrada"
                self.fps = 0.0
                if self.kind == "replay":
                    return
            except OSError as e:
                self.status, self.error, self.fps = "erro", f"{type(e).__name__}: {e}", 0.0
            self._stop.wait(RETRY_S)

    def info(self) -> dict:
        age = time.time() - self.last_frame if self.last_frame else None
        status = self.status
        if status == "recebendo" and age is not None and age > 2:
            status = "sem frames"          # interface de pé, barramento quieto (carro desligado?)
            self.fps = 0.0
        return {"name": self.name, "kind": self.kind, "status": status, "error": self.error,
                "fps": round(self.fps, 1), "last_frame_age": None if age is None else round(age, 1),
                "retention": self.retention, "uptime": round(time.time() - self.started, 1)}


def socketcan_source(iface, db, retention):
    return LiveSource(iface, lambda: live_frames(iface), db, retention, "socketcan")


def replay_source(path, db, retention, rate):
    name = "replay:" + os.path.basename(path)
    return LiveSource(name, lambda: replay_frames(path, rate), db, retention, "replay")

"""GPS NMEA ao vivo (``/dev/ttyACM0``, u-blox 7 por cdc_acm) ou replay de log.

Uma thread lê linhas, decodifica (``nmea.py``) e mantém o fix atual +
satélites em vista. Se o dispositivo sumir (USB solto), tenta de novo a cada
2 s e o motivo aparece no painel.

Log gravado / replay: uma sentença por linha com o timestamp do relógio na
frente, no mesmo estilo do ``candump -L``::

    (1792437933.113669) $GPRMC,192535.00,A,1853.18733,S,...*52

O replay reemite respeitando esses intervalos (linhas sem timestamp: uma
sentença a cada 0,1 s).
"""

import os
import re
import threading
import time
import tty

from .nmea import NmeaError, GsvJoiner, parse_line

RETRY_S = 2.0
KN_TO_KMH = 1.852
_TS = re.compile(r"^\((\d+\.\d+)\)\s+(.*)$")


def serial_lines(path):
    """Linhas de um dispositivo serial em modo raw (o baud do cdc_acm é ignorado)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY)
    try:
        tty.setraw(fd)
        buf = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                raise OSError(f"{path}: fim de dados (dispositivo removido?)")
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                ln = ln.strip()
                if ln:
                    yield time.time(), ln.decode("ascii", "replace")
    finally:
        os.close(fd)


def split_ts(line):
    """``(ts) sentença`` -> (ts, sentença); sem timestamp -> (None, linha)."""
    m = _TS.match(line.strip())
    return (float(m[1]), m[2]) if m else (None, line.strip())


def replay_lines(path, loop=True):
    """Reemite um log gravado em ritmo real, com o timestamp trocado por agora."""
    while True:
        t_log0 = None
        t_wall0 = due = time.time()
        with open(path, encoding="ascii", errors="replace") as fh:
            for raw in fh:
                ts, line = split_ts(raw)
                if not line:
                    continue
                if ts is None:
                    due += 0.1
                else:
                    if t_log0 is None:
                        t_log0 = ts
                    due = t_wall0 + (ts - t_log0)
                now = time.time()
                if due > now:
                    time.sleep(due - now)
                yield time.time(), line
        if not loop:
            return


class GpsState:
    """Fix atual a partir das sentenças RMC/GGA/GSA/VTG/GSV."""

    def __init__(self):
        self.fix = {}              # lat, lon, speed_kmh, course_deg, alt_m, sats, hdop, fix_type, utc
        self.satellites = []
        self.last_fix_ts = None    # relógio local do último RMC válido
        self.sentences = 0
        self.invalid = 0
        self._gsv = GsvJoiner()

    def feed(self, ts, line):
        """Devolve True quando chegou um RMC válido (= novo ponto de trajeto)."""
        try:
            rec = parse_line(line)
        except NmeaError:
            self.invalid += 1
            return False
        self.sentences += 1
        kind, f = rec["type"], self.fix
        if kind == "RMC":
            f["valid"] = rec["valid"]
            if rec["datetime"]:
                f["utc"] = rec["datetime"].isoformat().replace("+00:00", "Z")
            if rec["valid"] and rec["lat"] is not None:
                f["lat"], f["lon"] = rec["lat"], rec["lon"]
                f["speed_kmh"] = round((rec["speed_kn"] or 0.0) * KN_TO_KMH, 2)
                f["course_deg"] = rec["course_deg"]       # vazio parado
                self.last_fix_ts = ts
                return True
        elif kind == "GGA":
            f.update(alt_m=rec["alt_m"], sats=rec["sats_used"], hdop=rec["hdop"],
                     quality=rec["quality_txt"])
        elif kind == "GSA":
            f.update(fix_type=rec["fix_type"], pdop=rec["pdop"], vdop=rec["vdop"])
        elif kind == "GSV":
            rec = self._gsv.feed(rec)
            if rec is not None:
                self.satellites = rec["satellites"]
        return False


class GpsSource:
    """Thread leitora; ``on_fix(ts, fix)`` é chamado a cada RMC válido."""

    def __init__(self, name, lines_factory, kind="serial", record_path=None, on_fix=None):
        self.name = name
        self.kind = kind
        self.lock = threading.Lock()
        self.state = GpsState()
        self.status = "conectando"
        self.error = None
        self.last_line = None
        self.record_path = record_path
        self.on_fix = on_fix
        self._factory = lines_factory
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"gps-{name}", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        rec = open(self.record_path, "a", buffering=1) if self.record_path else None
        try:
            while not self._stop.is_set():
                try:
                    self.status = "conectando"
                    for ts, line in self._factory():
                        if self._stop.is_set():
                            return
                        if rec:
                            rec.write(f"({ts:.6f}) {line}\n")
                        with self.lock:
                            got = self.state.feed(ts, line)
                            fix = dict(self.state.fix) if got else None
                        self.status, self.error, self.last_line = "recebendo", None, ts
                        if fix and self.on_fix:
                            self.on_fix(ts, fix)
                    self.status = "fim do replay" if self.kind == "replay" else "fonte encerrada"
                    if self.kind == "replay":
                        return
                except OSError as e:
                    self.status, self.error = "erro", f"{type(e).__name__}: {e}"
                self._stop.wait(RETRY_S)
        finally:
            if rec:
                rec.close()

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            st = self.state
            fix = dict(st.fix)
            sats = list(st.satellites)
            fix_age = now - st.last_fix_ts if st.last_fix_ts else None
            counts = (st.sentences, st.invalid)
        status = self.status
        if status == "recebendo" and self.last_line and now - self.last_line > 3:
            status = "sem dados"
        elif status == "recebendo" and (fix_age is None or fix_age > 3):
            status = "sem fix"
        return {"name": self.name, "kind": self.kind, "status": status, "error": self.error,
                "fix": fix, "fix_age": None if fix_age is None else round(fix_age, 1),
                "satellites": sats, "sentences": counts[0], "invalid": counts[1],
                "recording": self.record_path}


def serial_source(device, record_path=None, on_fix=None):
    return GpsSource(device, lambda: serial_lines(device), "serial", record_path, on_fix)


def replay_source(path, on_fix=None, loop=True):
    return GpsSource("replay:" + os.path.basename(path), lambda: replay_lines(path, loop),
                     "replay", None, on_fix)

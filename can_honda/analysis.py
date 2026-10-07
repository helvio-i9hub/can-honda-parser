"""Análise completa de um log: séries por sinal, estatísticas, integridade
e ciclos de motor. Base do dashboard web (``can_honda.web``) e do ``summary``.

Eixo X: timestamp (s) quando o log tem, senão o **nº da linha** no arquivo —
os logs reais (candump padrão) não têm timestamp.
"""

import os
from array import array
from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional

from .dbc import Database, default_dbc
from .decoder import PROTOCOL_SIGNALS, Decoder
from .frames import FrameReader

RPM_KEY = "POWERTRAIN_DATA.ENGINE_RPM"
DISTINCT_CAP = 64


class SignalSeries:
    __slots__ = ("key", "can_id", "message", "signal", "x", "v",
                 "min", "max", "first", "last", "changes", "distinct", "_prev")

    def __init__(self, key, can_id, message, signal):
        self.key, self.can_id, self.message, self.signal = key, can_id, message, signal
        self.x = array("d")
        self.v = array("d")
        self.min = self.max = self.first = self.last = None
        self.changes = 0
        self.distinct = set()
        self._prev = None

    def add(self, x, v):
        self.x.append(x)
        self.v.append(v)
        if self.first is None:
            self.first = self.min = self.max = v
        else:
            if v < self.min:
                self.min = v
            if v > self.max:
                self.max = v
            if v != self._prev:
                self.changes += 1
        self.last = self._prev = v
        if len(self.distinct) <= DISTINCT_CAP:
            self.distinct.add(v)

    def trim(self, cut: float):
        i = bisect_left(self.x, cut)
        if i:
            del self.x[:i]
            del self.v[:i]

    # ---- metadados / classificação
    @property
    def binary(self) -> bool:
        return self.signal.length == 1

    @property
    def stepped(self) -> bool:
        return bool(self.signal.choices) or self.signal.length <= 4

    @property
    def constant(self) -> bool:
        return self.changes == 0

    def meta(self) -> dict:
        s = self.signal
        n_distinct = len(self.distinct)
        return {
            "key": self.key, "id": f"{self.can_id:03X}", "message": self.message,
            "signal": s.name, "unit": s.unit, "confidence": s.confidence,
            "comment": s.comment.split("]", 1)[1].strip() if s.comment.startswith("[") else s.comment,
            "choices": {str(k): v for k, v in s.choices.items()},
            "kind": "binary" if self.binary else ("step" if self.stepped else "line"),
            "samples": len(self.v), "min": self.min, "max": self.max,
            "first": self.first, "last": self.last, "changes": self.changes,
            "distinct": n_distinct if n_distinct <= DISTINCT_CAP else f">{DISTINCT_CAP}",
            "values": sorted(self.distinct) if n_distinct <= 8 else None,
        }

    # ---- recortes para o gráfico
    def _slice(self, x0, x1):
        return bisect_left(self.x, x0), bisect_right(self.x, x1)

    def series(self, x0: float, x1: float, bins: int) -> dict:
        """Pontos brutos se couberem; senão faixas min/max/média por bin."""
        i, j = self._slice(x0, x1)
        xs, vs = self.x, self.v
        if i > 0:
            i -= 1                      # um ponto antes, para a linha entrar no recorte
        if j - i <= bins * 2:
            return {"mode": "raw", "x": list(xs[i:j]), "v": list(vs[i:j])}
        span = (x1 - x0) or 1.0
        cnt = [0] * bins
        tot = [0.0] * bins
        lo = [0.0] * bins
        hi = [0.0] * bins
        for k in range(i, j):
            b = int((xs[k] - x0) * bins / span)
            if b < 0:
                continue
            if b >= bins:
                b = bins - 1
            v = vs[k]
            if cnt[b]:
                if v < lo[b]:
                    lo[b] = v
                if v > hi[b]:
                    hi[b] = v
            else:
                lo[b] = hi[b] = v
            cnt[b] += 1
            tot[b] += v
        out = {"mode": "band", "x": [], "mean": [], "min": [], "max": []}
        for b in range(bins):
            if cnt[b]:
                out["x"].append(x0 + (b + 0.5) * span / bins)
                out["mean"].append(round(tot[b] / cnt[b], 4))
                out["min"].append(lo[b])
                out["max"].append(hi[b])
        return out

    def intervals(self, x0: float, x1: float) -> List[list]:
        """Trechos [ini, fim] em que um sinal de 1 bit vale 1."""
        i, j = self._slice(x0, x1)
        if i > 0:
            i -= 1
        out, start = [], None
        xs, vs = self.x, self.v
        for k in range(i, j):
            if vs[k] and start is None:
                start = max(xs[k], x0)
            elif not vs[k] and start is not None:
                out.append([start, xs[k]])
                start = None
        if start is not None:
            out.append([start, min(xs[j - 1], x1) if j > i else x1])
        return out


def _cluster(points, gap):
    """Agrupa saltos de contador próximos (ex.: módulos reiniciando na ignição)."""
    out = []
    for x, line in points:
        if out and x - out[-1]["x1"] <= gap:
            c = out[-1]
            c["x1"], c["line1"] = x, line
            c["count"] += 1
        else:
            out.append({"x0": x, "x1": x, "line0": line, "line1": line, "count": 1})
    return out


class Analysis:
    """Estado incremental: ``feed(frame)`` frame a frame (arquivo ou ao vivo).

    ``Analysis(path)`` lê o arquivo inteiro. Para ao vivo, ``Analysis(name=...,
    retention=s)`` + ``feed`` + ``trim()`` periódico: as séries guardam só os
    últimos ``retention`` segundos; mín/máx/mudanças valem desde o início.
    """

    def __init__(self, path: Optional[str] = None, db: Optional[Database] = None,
                 name: Optional[str] = None, retention: Optional[float] = None):
        self.path = path
        self.name = name or (os.path.basename(path) if path else "?")
        self.mtime = os.path.getmtime(path) if path else None
        self.size = os.path.getsize(path) if path else None
        self.db = db or default_dbc()
        self.retention = retention
        self.signals: Dict[str, SignalSeries] = {}
        self.frames = 0
        self.skipped = 0
        self.x_kind = None
        self.x0 = self.x1 = 0.0
        self.runs: List[dict] = []
        self._dec = Decoder(self.db)
        self._dlcs: Dict[int, set] = {}
        self._run = None
        self._temps = {}
        self._jumps = []
        if path:
            reader = FrameReader(path)
            for f in reader:
                self.feed(f)
            self.skipped = reader.skipped

    def feed(self, f):
        if self.x_kind is None:
            self.x_kind = "time" if f.ts is not None else "line"
        x = f.ts if self.x_kind == "time" and f.ts is not None else float(f.line_no)
        if self.frames == 0:
            self.x0 = x
        self.x1 = x
        self.frames += 1
        self._dlcs.setdefault(f.can_id, set()).add(f.dlc)
        d = self._dec.decode(f)
        if d.counter_ok is False:
            self._jumps.append((x, f.line_no))
        if d.message is None or d.checksum_ok is False:
            return
        mname = d.message.name
        sigs = self.signals
        for name, v in d.values.items():
            key = f"{mname}.{name}"
            s = sigs.get(key)
            if s is None:
                s = sigs[key] = SignalSeries(key, f.can_id, mname, d.message.signals[name])
            s.add(x, v)
        if mname == "ENGINE_STATUS":
            self._temps = d.values
        elif f.can_id == 0x17C:
            self._run = self._track_run(self._run, x, f.line_no, d.values.get("ENGINE_RPM", 0), self._temps)

    def trim(self):
        """Descarta pontos mais velhos que ``retention`` (só ao vivo)."""
        if not self.retention or not self.frames:
            return
        cut = self.x1 - self.retention
        if cut <= self.x0:
            return
        for s in self.signals.values():
            s.trim(cut)
        self._jumps = [j for j in self._jumps if j[0] >= cut]
        self.x0 = cut

    @property
    def ids(self) -> List[dict]:
        from .honda import NO_CHECKSUM
        it = self._dec.integrity
        out = []
        for cid in sorted(it.frames):
            msg = self.db.messages.get(cid)
            n = it.frames[cid]
            out.append({
                "id": f"{cid:03X}", "message": msg.name if msg else None,
                "frames": n, "share": round(100 * n / self.frames, 2) if self.frames else 0,
                "dlc": sorted(self._dlcs[cid]),
                "checksum_bad": None if cid in NO_CHECKSUM else it.bad_checksum.get(cid, 0),
                "counter_jumps": it.counter_jumps.get(cid, 0),
            })
        return out

    @property
    def jump_clusters(self) -> List[dict]:
        return _cluster(self._jumps, (self.x1 - self.x0) * 0.002)

    def engine_runs(self) -> List[dict]:
        """Ciclos fechados + o ciclo em andamento (``open``)."""
        runs = self.runs + ([dict(self._run, open=True)] if self._run else [])
        out = []
        for r in runs:
            tail = sorted(r["_rpm_tail"])
            r = {k: v for k, v in r.items() if k != "_rpm_tail"}
            r["idle_end"] = tail[len(tail) // 2] if tail else None
            out.append(r)
        return out

    def _track_run(self, run, x, line_no, rpm, temps):
        if rpm > 0:
            if run is None:
                run = {"x0": x, "line0": line_no, "rpm_max": 0, "frames": 0,
                       "coolant0": temps.get("COOLANT_TEMP"), "intake0": temps.get("INTAKE_TEMP"),
                       "_rpm_tail": []}
            if run["coolant0"] is None:     # 0x324 (10 Hz) pode chegar depois do 1º 0x17C
                run["coolant0"] = temps.get("COOLANT_TEMP")
                run["intake0"] = temps.get("INTAKE_TEMP")
            run["frames"] += 1
            run["rpm_max"] = max(run["rpm_max"], rpm)
            run["x1"], run["line1"] = x, line_no
            run["coolant1"] = temps.get("COOLANT_TEMP")
            run["intake1"] = temps.get("INTAKE_TEMP")
            run["fuel"] = temps.get("TRIP_FUEL_CONSUMED")
            tail = run["_rpm_tail"]
            tail.append(rpm)
            if len(tail) > 2000:            # ~ final do ciclo = marcha lenta estabilizada
                del tail[:1000]
            return run
        if run is not None:
            self.runs.append(run)
        return None

    # ---- JSON
    def overview(self) -> dict:
        ids = self.ids
        bad = sum(r["checksum_bad"] or 0 for r in ids)
        checked = sum(r["frames"] for r in ids if r["checksum_bad"] is not None)
        return {
            "file": self.name, "size": self.size,
            "frames": self.frames, "skipped": self.skipped,
            "x_kind": self.x_kind or "line", "x0": self.x0, "x1": self.x1,
            "checksum_ok_pct": round(100 * (checked - bad) / checked, 3) if checked else None,
            "counter_jumps": sum(r["counter_jumps"] for r in ids),
            "jump_clusters": self.jump_clusters,
            "ids": ids, "runs": self.engine_runs(),
            "signals": [s.meta() for s in sorted(self.signals.values(),
                                                 key=lambda s: (s.can_id, s.key))],
        }

    def chart_data(self, x0=None, x1=None, bins=800, keys=None) -> dict:
        x0 = self.x0 if x0 is None else max(x0, self.x0)
        x1 = self.x1 if x1 is None else min(x1, self.x1)
        out = {}
        for key, s in self.signals.items():
            if keys is not None and key not in keys:
                continue
            if s.constant or s.signal.name in PROTOCOL_SIGNALS:
                continue
            out[key] = ({"mode": "intervals", "iv": s.intervals(x0, x1)} if s.binary
                        else s.series(x0, x1, bins))
        return {"x0": x0, "x1": x1, "series": out}

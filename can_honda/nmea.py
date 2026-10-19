"""NMEA 0183 — cópia de gps-uploader/gps_parser/nmea.py (helvio-i9hub/gps-uploader @ d64df84).

Mantida igual à origem; correções vão lá primeiro.
"""


from __future__ import annotations

import datetime as _dt
from typing import Callable, Dict, Iterable, Iterator, List, Optional

UTC = _dt.timezone.utc


class NmeaError(ValueError):
    """Linha que nao e uma sentenca NMEA valida."""


# --------------------------------------------------------------------------
# moldura: $ttsss,campo,...*CS
# --------------------------------------------------------------------------

def checksum(body: str) -> int:
    """XOR de todos os bytes entre ``$`` e ``*``."""
    cs = 0
    for ch in body.encode("ascii", "replace"):
        cs ^= ch
    return cs


def split_sentence(line: str, lenient: bool = False) -> List[str]:
    """Valida a moldura e devolve os campos (o primeiro e o endereco).

    ``lenient`` aceita sentenca sem checksum ou com checksum errado.
    """
    line = line.strip()
    # Lixo antes do inicio (resto de sentenca anterior): vale o ultimo '$'.
    start = max(line.rfind("$"), line.rfind("!"))
    if start < 0:
        raise NmeaError("sem '$' de inicio")
    line = line[start + 1:]
    star = line.rfind("*")
    if star >= 0:
        body, cs_txt = line[:star], line[star + 1:star + 3]
        if not lenient:
            try:
                expected = int(cs_txt, 16)
            except ValueError:
                raise NmeaError("checksum ilegivel: %r" % cs_txt)
            if checksum(body) != expected:
                raise NmeaError("checksum nao confere (%02X != %s)"
                                % (checksum(body), cs_txt.upper()))
    else:
        if not lenient:
            raise NmeaError("sem checksum")
        body = line
    fields = body.split(",")
    addr = fields[0]
    if len(addr) < 3 or not addr.isalnum():
        raise NmeaError("endereco invalido: %r" % addr)
    return fields


# --------------------------------------------------------------------------
# conversores de campo
# --------------------------------------------------------------------------

def _f(s: str) -> Optional[float]:
    return float(s) if s.strip() else None


def _i(s: str) -> Optional[int]:
    return int(s) if s.strip() else None


def _s(s: str) -> Optional[str]:
    return s if s != "" else None


def parse_coord(value: str, hemi: str) -> Optional[float]:
    """``ddmm.mmmm`` / ``dddmm.mmmm`` + hemisferio -> graus decimais."""
    if not value or not hemi:
        return None
    dot = value.find(".")
    if dot < 0:
        dot = len(value)
    deg = int(value[:dot - 2])
    minutes = float(value[dot - 2:])
    dec = deg + minutes / 60.0
    if hemi.upper() in ("S", "W"):
        dec = -dec
    return round(dec, 8)


def parse_time(value: str) -> Optional[_dt.time]:
    """``hhmmss[.sss]`` -> ``datetime.time`` (UTC)."""
    if not value:
        return None
    hh, mm = int(value[0:2]), int(value[2:4])
    sec = float(value[4:])
    whole = int(sec)
    micro = int(round((sec - whole) * 1_000_000))
    if micro == 1_000_000:  # arredondamento de 59.9999995
        whole, micro = whole + 1, 0
    return _dt.time(hh, mm, whole, micro, tzinfo=UTC)


def parse_date(value: str) -> Optional[_dt.date]:
    """``ddmmyy`` -> ``datetime.date``. Ano de 2 digitos: 80..99 = 19xx."""
    if not value:
        return None
    dd, mo, yy = int(value[0:2]), int(value[2:4]), int(value[4:6])
    year = 1900 + yy if yy >= 80 else 2000 + yy
    return _dt.date(year, mo, dd)


def _combine(date: Optional[_dt.date], time: Optional[_dt.time]):
    if date is None or time is None:
        return None
    return _dt.datetime.combine(date, time)


# --------------------------------------------------------------------------
# decodificadores por tipo (f = campos sem o endereco)
# --------------------------------------------------------------------------

def _get(f: List[str], i: int) -> str:
    return f[i] if i < len(f) else ""


def _rmc(f):
    t, d = parse_time(_get(f, 0)), parse_date(_get(f, 8))
    return {
        "time": t,
        "status": _s(_get(f, 1)),           # A = valido, V = alerta
        "valid": _get(f, 1) == "A",
        "lat": parse_coord(_get(f, 2), _get(f, 3)),
        "lon": parse_coord(_get(f, 4), _get(f, 5)),
        "speed_kn": _f(_get(f, 6)),
        "course_deg": _f(_get(f, 7)),
        "date": d,
        "datetime": _combine(d, t),
        "mag_var_deg": (-_f(_get(f, 9)) if _get(f, 10) == "W" and _get(f, 9)
                        else _f(_get(f, 9))),
        "mode": _s(_get(f, 11)),            # A aut., D dif., E estim., N inv.
        "nav_status": _s(_get(f, 12)),      # NMEA 4.1
    }


GGA_QUALITY = {0: "invalido", 1: "GPS", 2: "DGPS", 3: "PPS", 4: "RTK",
               5: "RTK float", 6: "estimado", 7: "manual", 8: "simulacao"}


def _gga(f):
    q = _i(_get(f, 5))
    return {
        "time": parse_time(_get(f, 0)),
        "lat": parse_coord(_get(f, 1), _get(f, 2)),
        "lon": parse_coord(_get(f, 3), _get(f, 4)),
        "quality": q,
        "quality_txt": GGA_QUALITY.get(q) if q is not None else None,
        "sats_used": _i(_get(f, 6)),
        "hdop": _f(_get(f, 7)),
        "alt_m": _f(_get(f, 8)),            # acima do geoide (MSL)
        "geoid_sep_m": _f(_get(f, 10)),
        "dgps_age_s": _f(_get(f, 12)),
        "dgps_station": _s(_get(f, 13)),
    }


def _gsa(f):
    prns = [int(p) for p in f[2:14] if p.strip()]
    return {
        "selection": _s(_get(f, 0)),        # M manual, A automatico
        "fix_type": _i(_get(f, 1)),         # 1 sem fix, 2 = 2D, 3 = 3D
        "prns": prns,
        "pdop": _f(_get(f, 14)),
        "hdop": _f(_get(f, 15)),
        "vdop": _f(_get(f, 16)),
        "system_id": _i(_get(f, 17)),       # NMEA 4.1
    }


def _gsv(f):
    sats = []
    body = f[3:]
    # Blocos de 4 (prn, elev, az, snr); NMEA 4.1 pode ter um signal_id solto
    # no fim -- sobra um campo, que nao forma bloco.
    for k in range(0, len(body) - len(body) % 4, 4):
        prn = _i(body[k])
        if prn is None:
            continue
        sats.append({"prn": prn, "elev_deg": _i(body[k + 1]),
                     "az_deg": _i(body[k + 2]), "snr_db": _i(body[k + 3])})
    return {
        "msgs_total": _i(_get(f, 0)),
        "msg_num": _i(_get(f, 1)),
        "sats_in_view": _i(_get(f, 2)),
        "satellites": sats,
    }


def _vtg(f):
    return {
        "course_true_deg": _f(_get(f, 0)),
        "course_mag_deg": _f(_get(f, 2)),
        "speed_kn": _f(_get(f, 4)),
        "speed_kmh": _f(_get(f, 6)),
        "mode": _s(_get(f, 8)),
    }


def _gll(f):
    return {
        "lat": parse_coord(_get(f, 0), _get(f, 1)),
        "lon": parse_coord(_get(f, 2), _get(f, 3)),
        "time": parse_time(_get(f, 4)),
        "status": _s(_get(f, 5)),
        "valid": _get(f, 5) == "A",
        "mode": _s(_get(f, 6)),
    }


def _zda(f):
    t = parse_time(_get(f, 0))
    d = None
    if _get(f, 1) and _get(f, 2) and _get(f, 3):
        d = _dt.date(int(f[3]), int(f[2]), int(f[1]))
    return {
        "time": t,
        "date": d,
        "datetime": _combine(d, t),
        "tz_hours": _i(_get(f, 4)),
        "tz_minutes": _i(_get(f, 5)),
    }


def _txt(f):
    return {
        "msgs_total": _i(_get(f, 0)),
        "msg_num": _i(_get(f, 1)),
        "severity": _i(_get(f, 2)),         # 00 erro, 01 aviso, 02 aviso, 07 usuario
        "text": ",".join(f[3:]),
    }


DECODERS: Dict[str, Callable[[List[str]], dict]] = {
    "RMC": _rmc, "GGA": _gga, "GSA": _gsa, "GSV": _gsv,
    "VTG": _vtg, "GLL": _gll, "ZDA": _zda, "TXT": _txt,
}


def parse_line(line: str, lenient: bool = False) -> dict:
    """Uma linha -> registro decodificado. Levanta ``NmeaError``."""
    fields = split_sentence(line, lenient=lenient)
    addr = fields[0]
    # Proprietaria ($PUBX, $PGRM...): 'P' + fabricante, sem talker padrao.
    if addr.startswith("P"):
        talker, kind = "P", addr[1:]
    else:
        talker, kind = addr[:2], addr[2:]
    rec = {"type": kind, "talker": talker, "sentence": addr}
    dec = DECODERS.get(kind)
    if dec is None:
        rec["fields"] = fields[1:]
        return rec
    try:
        rec.update(dec(fields[1:]))
    except (ValueError, IndexError) as exc:
        raise NmeaError("%s com campo invalido: %s" % (addr, exc))
    return rec


# --------------------------------------------------------------------------
# fluxo: linhas -> registros (com juncao do GSV em ciclos)
# --------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.lines = 0
        self.blank = 0
        self.ok = 0
        self.invalid = 0
        self.errors: Dict[str, int] = {}
        self.by_type: Dict[str, int] = {}

    def as_dict(self):
        return {"lines": self.lines, "blank": self.blank, "ok": self.ok,
                "invalid": self.invalid, "errors": dict(self.errors),
                "by_type": dict(sorted(self.by_type.items()))}


class GsvJoiner:
    """Junta as partes 1..N do GSV num registro so (um ciclo de satelites).

    Ciclo incompleto (parte perdida no serial) e descartado, nao emitido pela
    metade -- senao a contagem de satelites mentiria.
    """

    def __init__(self):
        self._parts: Dict[str, dict] = {}

    def feed(self, rec: dict) -> Optional[dict]:
        key = rec["sentence"]
        num, total = rec.get("msg_num"), rec.get("msgs_total")
        if not num or not total:
            return None
        if num == 1:
            self._parts[key] = {**rec, "satellites": list(rec["satellites"]),
                                "_next": 2}
        else:
            cur = self._parts.get(key)
            if cur is None or cur["_next"] != num:
                self._parts.pop(key, None)
                return None
            cur["satellites"].extend(rec["satellites"])
            cur["_next"] = num + 1
        cur = self._parts[key]
        if num == total:
            del self._parts[key]
            cur.pop("_next")
            cur.pop("msg_num", None)
            cur["msgs_total"] = total
            return cur
        return None


def iter_records(lines: Iterable[str], lenient: bool = False,
                 join_gsv: bool = True, stats: Optional[Stats] = None,
                 on_error: Optional[Callable[[str, str], None]] = None
                 ) -> Iterator[dict]:
    stats = stats if stats is not None else Stats()
    joiner = GsvJoiner() if join_gsv else None
    for raw in lines:
        stats.lines += 1
        line = raw.strip()
        if not line:
            stats.blank += 1
            continue
        try:
            rec = parse_line(line, lenient=lenient)
        except NmeaError as exc:
            stats.invalid += 1
            reason = str(exc).split(":")[0].split(" (")[0]
            stats.errors[reason] = stats.errors.get(reason, 0) + 1
            if on_error:
                on_error(line, str(exc))
            continue
        stats.ok += 1
        stats.by_type[rec["type"]] = stats.by_type.get(rec["type"], 0) + 1
        if joiner is not None and rec["type"] == "GSV":
            rec = joiner.feed(rec)
            if rec is None:
                continue
        yield rec

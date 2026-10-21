"""Telemetria ao vivo: estado do CAN + GPS e medidas derivadas, para o painel
``/telemetry`` do dashboard (``web --live can0 --gps /dev/ttyACM0``).

``feed_frame`` recebe cada frame CAN (listener do ``LiveSource``) e
``on_fix`` cada fix válido do GPS (``GpsSource``). ``snapshot()`` monta o
estado para o painel; ``track(since)`` devolve o trajeto (1 ponto por fix,
com velocidade/RPM/marcha do CAN no instante).

Derivadas e de onde vêm:
  - marcha estimada: RPM ÷ km/h agrupado nas relações medidas andando em
    12/10, 19/10 e 21/10 (1ª ~124, 2ª ~74, 3ª ~49, 4ª ~36); 5ª extrapolada das
    relações do câmbio de 5 marchas — [HIPOTESE] até aparecer nos logs
  - aceleração longitudinal: derivada de XMISSION_SPEED (100 Hz) em janela de 1 s
  - aceleração lateral: velocidade × taxa de giro do rumo do GPS
  - erro do velocímetro: CAR_SPEED contra a velocidade do GPS (média móvel)
  - distância: integrada do GPS e do CAN_SPEED; contador 0x377 [HIPOTESE]
  - rampa: variação de altitude GPS sobre ≥ 50 m percorridos
  - VIN: texto ASCII de 0x40C (byte 0 = página 1..3) [CONFIRMADO]
"""

import math
import threading
import time
from collections import deque

from .decoder import BusState, Decoder

# RPM por km/h em cada marcha (marcha travada, conversor acoplado).
GEAR_RATIO = {1: 124.0, 2: 74.0, 3: 49.0, 4: 36.0, 5: 25.0}
GEAR_TOL = 0.10
GEAR_MIN_KMH = 6.0
EARTH_R = 6371008.8
G = 9.80665
VIN_ID = 0x40C

SPEED = "CAR_SPEED.CAR_SPEED"
XSPEED = "ENGINE_DATA.XMISSION_SPEED"
RPM = "POWERTRAIN_RPM.ENGINE_RPM"


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def estimate_gear(rpm, kmh):
    """(marcha ou None, relação RPM/km/h). None = parado, embreagem do conversor ou troca."""
    if not rpm or not kmh or kmh < GEAR_MIN_KMH:
        return None, None
    ratio = rpm / kmh
    gear = min(GEAR_RATIO, key=lambda g: abs(math.log(ratio / GEAR_RATIO[g])))
    ok = abs(ratio / GEAR_RATIO[gear] - 1) <= GEAR_TOL
    return (gear if ok else None), round(ratio, 1)


class Vin:
    """Monta o VIN das 3 páginas de 0x40C (6 + 6 + 5 caracteres)."""

    def __init__(self):
        self.pages = {}

    def feed(self, data):
        if len(data) >= 8 and 1 <= data[0] <= 3:
            self.pages[data[0]] = data[1:7]

    @property
    def text(self):
        if len(self.pages) < 3:
            return None
        raw = b"".join(self.pages[k] for k in (1, 2, 3)).rstrip(b"\x00")
        return raw.decode("ascii", "replace") if all(32 < c < 127 for c in raw) else None


class Telemetry:
    def __init__(self, db, track_max=20000):
        self.lock = threading.Lock()
        self._dec = Decoder(db)
        self.bus = BusState()
        self.vin = Vin()
        self.started = time.time()
        self.can_frames = 0
        self.can_last = None
        self._xs = deque()                 # (ts, km/h) de XMISSION_SPEED, últimos ~1,5 s
        self.track_pts = deque(maxlen=track_max)
        self.seq = 0
        # acumulados desde o início do painel
        self.can_dist_m = 0.0
        self.gps_dist_m = 0.0
        self.moving_s = 0.0
        self.max_kmh = 0.0
        self.max_rpm = 0
        self.dist0 = None                  # DIST_COUNTER inicial
        self.fuel0 = None
        self._last_can_speed = None        # (ts, km/h) para integrar
        self._speed_err = None             # média móvel do erro do velocímetro (%)
        self._prev_fix = None
        self._alt_ref = None               # (dist_m, alt_m) para a rampa
        self.grade_pct = None
        self.lat_g = None

    # ------------------------------------------------------------- CAN
    def feed_frame(self, f):
        d = self._dec.decode(f)
        ts = f.ts if f.ts is not None else time.time()
        with self.lock:
            self.can_frames += 1
            self.can_last = time.time()
            if f.can_id == VIN_ID:
                self.vin.feed(f.data)
            if d.message is None or d.checksum_ok is False:
                return
            self.bus.update(d)
            v = self.bus.values
            if f.can_id == 0x158 and "XMISSION_SPEED" in d.values:
                self._xs.append((ts, d.values["XMISSION_SPEED"]))
                while self._xs and ts - self._xs[0][0] > 1.5:
                    self._xs.popleft()
            elif f.can_id == 0x309 and "CAR_SPEED" in d.values:
                kmh = d.values["CAR_SPEED"]
                if self._last_can_speed:
                    t0, k0 = self._last_can_speed
                    dt = ts - t0
                    if 0 < dt < 2:
                        self.can_dist_m += (k0 + kmh) / 2 / 3.6 * dt
                        if kmh > 1:
                            self.moving_s += dt
                self._last_can_speed = (ts, kmh)
                self.max_kmh = max(self.max_kmh, kmh)
            elif f.can_id == 0x1DC:
                self.max_rpm = max(self.max_rpm, v.get(RPM) or 0)
            elif f.can_id == 0x377 and self.dist0 is None:
                self.dist0 = d.values.get("DIST_COUNTER")
            elif f.can_id == 0x324 and self.fuel0 is None:
                self.fuel0 = d.values.get("TRIP_FUEL_CONSUMED")

    def _long_g(self):
        xs = self._xs
        if len(xs) < 20:
            return None
        t1 = xs[-1][0]
        new = [k for t, k in xs if t1 - t <= 0.25]
        old = [k for t, k in xs if 1.0 <= t1 - t <= 1.25]
        if not new or not old:
            return None
        dv = (sum(new) / len(new) - sum(old) / len(old)) / 3.6
        return round(dv / 1.0 / G, 3)

    # ------------------------------------------------------------- GPS
    def on_fix(self, ts, fix):
        with self.lock:
            v = self.bus.values
            can_kmh = v.get(SPEED)
            rpm = v.get(RPM)
            gear, ratio = estimate_gear(rpm, v.get(XSPEED, can_kmh))
            gps_kmh = fix.get("speed_kmh") or 0.0
            prev = self._prev_fix
            if prev:
                dt = ts - prev["t"]
                step = haversine_m(prev["lat"], prev["lon"], fix["lat"], fix["lon"])
                if gps_kmh > 2 and 0 < dt < 5:
                    self.gps_dist_m += step
                c0, c1 = prev.get("course"), fix.get("course_deg")
                if c0 is not None and c1 is not None and gps_kmh > 10 and 0 < dt < 3:
                    dc = (c1 - c0 + 540) % 360 - 180        # graus, + = direita
                    self.lat_g = round(gps_kmh / 3.6 * math.radians(dc) / dt / G, 3)
                elif gps_kmh <= 10:
                    self.lat_g = 0.0
            alt = fix.get("alt_m")
            if alt is not None:
                if self._alt_ref is None:
                    self._alt_ref = (self.gps_dist_m, alt)
                elif self.gps_dist_m - self._alt_ref[0] >= 50:
                    run = self.gps_dist_m - self._alt_ref[0]
                    self.grade_pct = round((alt - self._alt_ref[1]) / run * 100, 1)
                    self._alt_ref = (self.gps_dist_m, alt)
            if can_kmh is not None and gps_kmh > 15:
                err = (can_kmh - gps_kmh) / gps_kmh * 100
                self._speed_err = err if self._speed_err is None else 0.9 * self._speed_err + 0.1 * err
            self._prev_fix = {"t": ts, "lat": fix["lat"], "lon": fix["lon"], "course": fix.get("course_deg")}
            self.seq += 1
            self.track_pts.append({
                "seq": self.seq, "t": round(ts, 3), "lat": fix["lat"], "lon": fix["lon"],
                "gps_kmh": gps_kmh, "can_kmh": can_kmh, "rpm": rpm, "gear": gear,
                "alt": alt, "course": fix.get("course_deg"),
            })

    def track(self, since=0):
        with self.lock:
            return {"seq": self.seq, "points": [p for p in self.track_pts if p["seq"] > since]}

    # ------------------------------------------------------------- painel
    def snapshot(self):
        now = time.time()
        with self.lock:
            v = dict(self.bus.values)
            long_g = self._long_g()
            can = {
                "frames": self.can_frames,
                "age": None if self.can_last is None else round(now - self.can_last, 1),
            }
            dist = v.get("DISTANCE_377.DIST_COUNTER")
            fuel = v.get("ENGINE_STATUS.TRIP_FUEL_CONSUMED")
            gear, ratio = estimate_gear(v.get(RPM), v.get(XSPEED, v.get(SPEED)))
            trip = {
                "elapsed_s": round(now - self.started),
                "moving_s": round(self.moving_s),
                "can_dist_km": round(self.can_dist_m / 1000, 3),
                "gps_dist_km": round(self.gps_dist_m / 1000, 3),
                "avg_kmh": round(self.can_dist_m / self.moving_s * 3.6, 1) if self.moving_s > 5 else None,
                "max_kmh": round(self.max_kmh, 1),
                "max_rpm": self.max_rpm,
                "dist_counter": dist,
                "dist_counter_delta": None if dist is None or self.dist0 is None else dist - self.dist0,
                "fuel_raw": fuel,
                "fuel_delta": None if fuel is None or self.fuel0 is None else fuel - self.fuel0,
            }
            derived = {
                "gear": gear, "rpm_per_kmh": ratio,
                "long_g": long_g, "lat_g": self.lat_g, "grade_pct": self.grade_pct,
                "speedo_err_pct": None if self._speed_err is None else round(self._speed_err, 1),
            }
            vin = self.vin.text
        return {"t": round(now, 3), "can": can, "signals": v, "derived": derived, "trip": trip, "vin": vin}

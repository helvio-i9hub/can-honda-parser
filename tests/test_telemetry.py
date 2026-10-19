"""GPS + telemetria sobre trechos REAIS sincronizados de 19/10/2026: NMEA do
u-blox 7 em /dev/ttyACM0 (gps-ublox7-nmea.log, 60 s) e a can0 nos mesmos
5 s iniciais (drive-candump-L.log), carro a ~46 km/h em 3ª."""

import json
import os
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from can_honda.dbc import DEFAULT_DBC, default_dbc
from can_honda.frames import read_frames
from can_honda.gps import GpsSource, GpsState, split_ts
from can_honda.telemetry import Telemetry, Vin, estimate_gear, haversine_m
from can_honda.web import Library, make_handler, vendor_file

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NMEA = os.path.join(FIX, "gps-ublox7-nmea.log")
DRIVE = os.path.join(FIX, "drive-candump-L.log")


def nmea_lines():
    with open(NMEA) as fh:
        return [split_ts(ln) for ln in fh]


def run_synced(seconds=5.0):
    """Alimenta CAN e GPS na ordem dos timestamps, como ao vivo."""
    tel, gs = Telemetry(default_dbc()), GpsState()
    ev = [(f.ts, 0, f) for f in read_frames(DRIVE)] + [(ts, 1, ln) for ts, ln in nmea_lines()]
    ev.sort(key=lambda e: (e[0], e[1]))
    t_end = ev[0][0] + seconds
    for ts, kind, x in ev:
        if ts > t_end:
            break
        if kind == 0:
            tel.feed_frame(x)
        elif gs.feed(ts, x):
            tel.on_fix(ts, dict(gs.fix))
    return tel, gs


class TestGps(unittest.TestCase):
    def test_split_ts(self):
        ts, line = split_ts("(1792437933.113669) $GPRMC,192535.00,A*52\n")
        self.assertEqual((ts, line), (1792437933.113669, "$GPRMC,192535.00,A*52"))
        self.assertEqual(split_ts("$GPGGA,1*00"), (None, "$GPGGA,1*00"))

    def test_state_from_real_capture(self):
        gs = GpsState()
        fixes = sum(gs.feed(ts, ln) for ts, ln in nmea_lines())
        self.assertEqual(fixes, 61)                  # 1 RMC válido por segundo
        self.assertEqual(gs.invalid, 0)
        f = gs.fix
        self.assertEqual(f["fix_type"], 3)
        self.assertGreaterEqual(f["sats"], 6)
        self.assertAlmostEqual(f["lat"], -18.89, delta=0.05)
        self.assertAlmostEqual(f["lon"], -48.24, delta=0.05)
        self.assertAlmostEqual(f["alt_m"], 930, delta=30)
        self.assertTrue(gs.satellites and "snr_db" in gs.satellites[0])
        self.assertTrue(f["utc"].startswith("2026-10-19T"))

    def test_source_thread_calls_on_fix(self):
        got = []
        src = GpsSource("fixture", lambda: iter(nmea_lines()), "replay",
                        on_fix=lambda ts, fix: got.append(fix))
        src.start()
        t = time.time()
        while src.status != "fim do replay" and time.time() - t < 5:
            time.sleep(0.02)
        self.assertEqual(len(got), 61)
        snap = src.snapshot()
        self.assertEqual(snap["sentences"], 491)

    def test_record_writes_timestamped_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gps.log")
            src = GpsSource("fixture", lambda: iter(nmea_lines()[:20]), "replay", record_path=path)
            src._loop()
            with open(path) as fh:
                lines = fh.readlines()
            self.assertEqual(len(lines), 20)
            self.assertEqual(split_ts(lines[0])[1][:6], "$GPRMC")


class TestTelemetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tel, cls.gs = run_synced()
        cls.snap = cls.tel.snapshot()

    def test_vin_from_0x40c(self):
        self.assertEqual(self.snap["vin"], "93HFB2530EZ157414")

    def test_gear_third_at_46_kmh(self):
        d = self.snap["derived"]
        self.assertEqual(d["gear"], 3)
        self.assertAlmostEqual(d["rpm_per_kmh"], 49, delta=2)

    def test_speedometer_reads_high_vs_gps(self):
        err = self.snap["derived"]["speedo_err_pct"]
        self.assertGreater(err, 2)
        self.assertLess(err, 7)

    def test_track_points_carry_can_values(self):
        pts = self.tel.track()["points"]
        self.assertEqual(len(pts), 5)
        later = pts[1:]                                # o 1º fix chega antes do 1º CAR_SPEED
        self.assertTrue(all(p["can_kmh"] and p["rpm"] and p["gear"] == 3 for p in later))
        self.assertEqual(self.tel.track(since=pts[-1]["seq"])["points"], [])

    def test_distances_agree(self):
        tr = self.snap["trip"]
        # 4 intervalos de GPS (~12 m cada) contra 5 s de CAR_SPEED a ~46 km/h (~64 m)
        self.assertAlmostEqual(tr["gps_dist_km"], 0.049, delta=0.005)
        self.assertAlmostEqual(tr["can_dist_km"], 0.064, delta=0.004)
        self.assertEqual(tr["dist_counter"], 32104)
        self.assertGreater(tr["fuel_delta"], 0)
        json.dumps(self.snap, allow_nan=False)

    def test_gear_estimate_edges(self):
        self.assertEqual(estimate_gear(None, 40), (None, None))
        self.assertEqual(estimate_gear(800, 3), (None, None))         # quase parado
        self.assertEqual(estimate_gear(1240, 10)[0], 1)
        self.assertEqual(estimate_gear(2220, 30)[0], 2)
        self.assertIsNone(estimate_gear(2000, 20)[0])                  # 100: entre 1ª e 2ª (troca)

    def test_haversine(self):
        self.assertAlmostEqual(haversine_m(0, 0, 0, 1), 111195, delta=5)

    def test_vin_incomplete(self):
        v = Vin()
        v.feed(bytes.fromhex("0139334846423200"))
        self.assertIsNone(v.text)


class TestTelemetryApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tel, gs = run_synced()
        src = GpsSource("fixture", lambda: iter(()), "replay")
        src.state = gs
        cls.lib = Library([os.path.join(FIX, "brake.log")], DEFAULT_DBC, telemetry=tel, gps=src)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.lib))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urlopen(self.base + path) as r:
            return r.headers.get("Content-Type"), r.read()

    def test_telemetry_endpoint(self):
        _, body = self.get("/api/telemetry")
        d = json.loads(body)
        self.assertEqual(d["telemetry"]["vin"], "93HFB2530EZ157414")
        self.assertEqual(d["gps"]["fix"]["fix_type"], 3)
        self.assertEqual(d["can_sources"], [])

    def test_track_endpoint(self):
        d = json.loads(self.get("/api/track?since=3")[1])
        self.assertEqual([p["seq"] for p in d["points"]], [4, 5])

    def test_pages_and_vendor(self):
        ctype, body = self.get("/telemetry")
        self.assertIn(b"/vendor/leaflet/leaflet.js", body)
        ctype, body = self.get("/vendor/leaflet/leaflet.js")
        self.assertTrue(ctype.startswith("text/javascript"))
        self.assertIn(b"Leaflet 1.9.4", body[:200])

    def test_vendor_cannot_escape(self):
        self.assertIsNone(vendor_file("../telemetry.html"))
        self.assertIsNone(vendor_file("../../web.py"))
        with self.assertRaises(HTTPError) as cm:
            self.get("/vendor/../web.py")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

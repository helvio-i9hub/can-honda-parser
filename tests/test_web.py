"""Análise e API do dashboard sobre trechos reais (tests/fixtures)."""

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from can_honda.analysis import Analysis
from can_honda.dbc import DEFAULT_DBC
from can_honda.web import Library, make_handler

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class TestAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = Analysis(os.path.join(FIX, "engine-start.log"))

    def test_overview(self):
        o = self.a.overview()
        self.assertEqual(o["x_kind"], "line")
        self.assertEqual(o["checksum_ok_pct"], 100.0)
        self.assertEqual(o["counter_jumps"], 0)
        self.assertEqual(len(o["runs"]), 1)
        self.assertGreater(o["runs"][0]["rpm_max"], 1000)
        rpm = next(s for s in o["signals"] if s["key"] == "POWERTRAIN_DATA.ENGINE_RPM")
        self.assertEqual((rpm["kind"], rpm["min"], rpm["confidence"]), ("line", 0, "CONFIRMADO"))
        json.dumps(o, allow_nan=False)

    def test_series_raw_and_band(self):
        s = self.a.signals["POWERTRAIN_DATA.ENGINE_RPM"]
        raw = s.series(self.a.x0, self.a.x1, 2000)
        self.assertEqual(raw["mode"], "raw")
        band = s.series(self.a.x0, self.a.x1, 20)
        self.assertEqual(band["mode"], "band")
        self.assertLessEqual(len(band["x"]), 20)
        self.assertEqual(max(band["max"]), s.max)
        self.assertTrue(all(lo <= m <= hi for lo, m, hi in zip(band["min"], band["mean"], band["max"])))

    def test_constant_signals_not_charted(self):
        series = self.a.chart_data()["series"]
        self.assertNotIn("WHEEL_SPEEDS.WHEEL_SPEED_FL", series)
        self.assertIn("POWERTRAIN_DATA.ENGINE_RPM", series)

    def test_intervals(self):
        a = Analysis(os.path.join(FIX, "left-blinker.log"))
        iv = a.signals["SCM_FEEDBACK.LEFT_BLINKER"].intervals(a.x0, a.x1)
        self.assertEqual(len(iv), 1)
        self.assertLess(iv[0][0], iv[0][1])


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lib = Library([os.path.join(FIX, "*.log")], DEFAULT_DBC)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lib))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urlopen(self.base + path) as r:
            return r.status, r.read()

    def test_index_and_files(self):
        st, body = self.get("/")
        self.assertEqual(st, 200)
        self.assertIn(b"CAN Honda", body)
        names = [f["name"] for f in json.loads(self.get("/api/files")[1])]
        self.assertIn("brake.log", names)

    def test_overview_and_zoom(self):
        o = json.loads(self.get("/api/overview?file=idle-warming.log")[1])
        mid = (o["x0"] + o["x1"]) / 2
        c = json.loads(self.get(f"/api/chart?file=idle-warming.log&x0={o['x0']}&x1={mid}&bins=100")[1])
        self.assertEqual(c["x1"], mid)
        xs = c["series"]["POWERTRAIN_DATA.ENGINE_RPM"]["x"]
        self.assertTrue(xs and xs[-1] <= mid)

    def test_unknown_file_rejected(self):
        for name in ("../../etc/passwd", "nada.log"):
            with self.assertRaises(HTTPError) as cm:
                self.get(f"/api/overview?file={name}")
            self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

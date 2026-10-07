"""Modo online: fonte ao vivo alimentada por trecho REAL capturado da can0
(candump -L, com timestamp) e pelo replay de log."""

import os
import time
import unittest
from dataclasses import replace

from can_honda.analysis import Analysis
from can_honda.dbc import default_dbc
from can_honda.frames import read_frames
from can_honda.live import LiveSource, replay_frames

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LIVE = os.path.join(FIX, "live-candump-L.log")


def wait(cond, timeout=5.0):
    t = time.time()
    while not cond() and time.time() - t < timeout:
        time.sleep(0.02)
    return cond()


class TestTimestampedCapture(unittest.TestCase):
    def test_real_rates_from_capture(self):
        frames = list(read_frames(LIVE))
        self.assertTrue(all(f.ts is not None and f.iface == "can0" for f in frames))
        ts = [f.ts for f in frames if f.can_id == 0x17C]
        hz = (len(ts) - 1) / (ts[-1] - ts[0])
        self.assertAlmostEqual(hz, 100, delta=3)          # POWERTRAIN_DATA a 100 Hz

    def test_analysis_time_axis(self):
        a = Analysis(LIVE)
        o = a.overview()
        self.assertEqual(o["x_kind"], "time")
        self.assertEqual((o["checksum_ok_pct"], o["counter_jumps"]), (100.0, 0))


class TestLiveSource(unittest.TestCase):
    def test_feeds_and_reports(self):
        frames = [replace(f, ts=time.time()) for f in read_frames(LIVE)]
        src = LiveSource("teste", lambda: iter(frames), default_dbc(), retention=600).start()
        self.assertTrue(wait(lambda: src.analysis.frames == len(frames)))
        info = src.info()
        self.assertIn(info["status"], ("fonte encerrada", "recebendo", "sem frames"))
        with src.lock:
            o = src.analysis.overview()
        self.assertIn("POWERTRAIN_DATA.ENGINE_RPM", {s["key"] for s in o["signals"]})
        src.stop()

    def test_error_is_reported_and_retried(self):
        def broken():
            raise OSError(19, "No such device")
        src = LiveSource("can9", broken, default_dbc()).start()
        self.assertTrue(wait(lambda: src.info()["status"] == "erro"))
        self.assertIn("No such device", src.info()["error"])
        src.stop()

    def test_retention_trims_series(self):
        a = Analysis(name="x", retention=0.5)
        frames = list(read_frames(LIVE))
        t0 = frames[0].ts
        for f in frames:
            a.feed(f)
        a.trim()
        s = a.signals["POWERTRAIN_DATA.ENGINE_RPM"]
        self.assertGreaterEqual(s.x[0], a.x1 - 0.5)
        self.assertGreater(a.x1 - t0, 1.0)                 # o trecho tem > 1 s
        self.assertEqual(a.frames, len(frames))            # contadores valem desde o início

    def test_replay_paces_frames(self):
        gen = replay_frames(os.path.join(FIX, "brake.log"), rate=2000, loop=False)
        t = time.time()
        out = [next(gen) for _ in range(200)]
        self.assertGreaterEqual(time.time() - t, 0.08)     # ~200/2000 s
        self.assertTrue(all(f.ts is not None for f in out))


if __name__ == "__main__":
    unittest.main()


class TestPlaySafety(unittest.TestCase):
    def test_refuses_physical_and_missing(self):
        from unittest import mock
        from can_honda import socketcan
        with mock.patch.object(socketcan, "interface_kind", return_value="physical"):
            with self.assertRaises(PermissionError):
                socketcan.play([], "can0")
        with mock.patch.object(socketcan, "interface_kind", return_value=None):
            with self.assertRaises(OSError):
                socketcan.play([], "nada0")

    @unittest.skipUnless(os.path.isdir("/sys/class/net/vcan0"), "sem vcan0")
    def test_vcan0_roundtrip(self):
        """O que play envia chega inteiro e em ordem (tolera tráfego alheio intercalado)."""
        import threading
        from can_honda import socketcan
        frames = list(read_frames(os.path.join(FIX, "brake.log")))
        mine = [(f.can_id, f.data) for f in frames]
        state = {"i": 0}

        def reader():
            for f in socketcan.live_frames("vcan0"):
                if state["i"] < len(mine) and (f.can_id, f.data) == mine[state["i"]]:
                    state["i"] += 1
                if state["i"] == len(mine):
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertEqual(socketcan.play(frames, "vcan0", rate=5000), len(frames))
        t.join(3)
        self.assertEqual(state["i"], len(mine))

"""Testes sobre trechos REAIS de can-sample-0.log / can-sample-1.log
(tests/fixtures/*.log, candump padrão sem timestamp)."""

import io
import os
import unittest
from contextlib import redirect_stdout

from can_honda import Decoder, checksum_ok, counter_of, honda_checksum, parse_line, read_frames
from can_honda.cli import main
from can_honda.dbc import parse_dbc
from can_honda.decoder import BusState

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    return os.path.join(FIX, name)


def changes(name, key):
    """Sequência de valores (com mudança) de MENSAGEM.SINAL num fixture."""
    dec, st, seq = Decoder(), BusState(), []
    for f in read_frames(fx(name)):
        ch = st.update(dec.decode(f))
        if key in ch:
            seq.append(ch[key][1])
    return seq


class TestFrames(unittest.TestCase):
    def test_candump_default(self):
        f = parse_line("  can0  17C   [8]  00 00 02 CA 00 00 00 1B", 7)
        self.assertEqual((f.can_id, f.data.hex(), f.ts, f.iface, f.line_no),
                         (0x17C, "000002ca0000001b", None, "can0", 7))

    def test_candump_timestamp_and_L(self):
        a = parse_line("(1717000000.500000)  can0  1DC   [4]  02 03 0E 0C")
        b = parse_line("(1717000000.500000) can0 1DC#02030E0C")
        self.assertEqual(a.data, b.data)
        self.assertEqual(a.ts, 1717000000.5)

    def test_savvycan_csv(self):
        f = parse_line("201911608,0000017C,false,Rx,0,8,00,00,02,CA,00,00,00,1B,")
        self.assertEqual((f.can_id, f.dlc, f.ts), (0x17C, 8, 201.911608))
        self.assertIsNone(parse_line("Time Stamp,ID,Extended,Dir,Bus,LEN,D1"))

    def test_garbage_and_wrong_dlc(self):
        self.assertIsNone(parse_line("lixo"))
        self.assertIsNone(parse_line("  can0  17C   [8]  00 00"))


class TestHondaProtocol(unittest.TestCase):
    def test_checksum_known_frames(self):
        self.assertEqual(honda_checksum(0x17C, bytes.fromhex("000002CA0000001B")), 0xB)
        self.assertTrue(checksum_ok(0x039, bytes.fromhex("00001B")))
        self.assertFalse(checksum_ok(0x039, bytes.fromhex("00001C")))
        self.assertEqual(counter_of(bytes.fromhex("00001B")), 1)

    def test_all_fixture_frames_pass(self):
        for name in sorted(os.listdir(FIX)):
            dec = Decoder()
            for f in read_frames(fx(name)):
                dec.decode(f)
            self.assertEqual(dec.integrity.bad_checksum, {}, name)
            self.assertEqual(dec.integrity.counter_jumps, {}, name)


class TestDbc(unittest.TestCase):
    def test_motorola_intel_signed(self):
        db = parse_dbc('BO_ 1 T: 4 X\n'
                       ' SG_ M : 7|12@0+ (1,0) [0|0] ""  X\n'
                       ' SG_ S : 23|8@0- (0.5,0) [0|0] "u" X\n'
                       ' SG_ I : 4|12@1+ (1,0) [0|0] "" X\n')
        sig = db.messages[1].signals
        data = bytes.fromhex("ABCDF000")
        self.assertEqual(sig["M"].raw(data), 0xABC)
        self.assertEqual(sig["S"].decode(data), -8.0)
        self.assertEqual(sig["I"].raw(data), (int.from_bytes(data, "little") >> 4) & 0xFFF)

    def test_short_frame_gives_none(self):
        sig = Decoder().db.messages[0x17C].signals["ENGINE_RPM"]
        self.assertIsNone(sig.raw(b"\x00\x00"))

    def test_default_dbc_loads_comments_and_choices(self):
        db = Decoder().db
        rpm = db.messages[0x17C].signals["ENGINE_RPM"]
        self.assertEqual(rpm.confidence, "CONFIRMADO")
        self.assertEqual(db.messages[0x1A6].signals["LIGHTS_SETTING"].choices[3], "HIGH_BEAM")
        for m in db.messages.values():
            for s in m.signals.values():
                if s.name not in ("COUNTER", "CHECKSUM"):
                    self.assertIn(s.confidence, ("CONFIRMADO", "OPENDBC", "HIPOTESE"), s.name)


class TestRealSignals(unittest.TestCase):
    def test_rpm_17c_equals_1dc(self):
        dec, last = Decoder(), {}
        pairs = 0
        for f in read_frames(fx("idle-warming.log")):
            d = dec.decode(f)
            if f.can_id in (0x17C, 0x1DC):
                last[f.can_id] = d.values["ENGINE_RPM"]
                if f.can_id == 0x1DC:
                    pairs += 1
                    self.assertLessEqual(abs(last[0x1DC] - last.get(0x17C, last[0x1DC])), 10)
        self.assertGreater(pairs, 10)
        self.assertTrue(1100 < last[0x17C] < 1300)      # marcha lenta fria

    def test_engine_start(self):
        rpm = changes("engine-start.log", "POWERTRAIN_DATA.ENGINE_RPM")
        self.assertEqual(rpm[0], 0)
        self.assertGreater(max(rpm), 1000)

    def test_coolant_and_intake(self):
        ect = changes("idle-warming.log", "ENGINE_STATUS.COOLANT_TEMP")
        iat = changes("idle-warming.log", "ENGINE_STATUS.INTAKE_TEMP")
        self.assertTrue(all(30 <= v <= 40 for v in ect), ect)
        self.assertTrue(all(20 <= v <= 35 for v in iat), iat)

    def test_left_blinker(self):
        self.assertEqual(changes("left-blinker.log", "SCM_FEEDBACK.LEFT_BLINKER")[:2], [0, 1])
        self.assertNotIn(1, changes("left-blinker.log", "SCM_FEEDBACK.RIGHT_BLINKER"))

    def test_door_rl(self):
        self.assertEqual(changes("door-rl.log", "DOORS_STATUS.DOOR_OPEN_RL"), [0, 1])
        self.assertEqual(changes("door-rl.log", "DOORS_STATUS.DOOR_OPEN_FL"), [1])

    def test_brake(self):
        self.assertEqual(changes("brake.log", "POWERTRAIN_DATA.BRAKE_PRESSED")[:2], [0, 1])


class TestCli(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(list(argv)), 0)
        return buf.getvalue()

    def test_stats(self):
        out = self.run_cli("stats", fx("engine-start.log"))
        self.assertIn("POWERTRAIN_DATA", out)
        self.assertIn("n/a", out)                     # 0x255 sem checksum Honda

    def test_summary(self):
        out = self.run_cli("summary", fx("engine-start.log"))
        self.assertIn("1 ciclo(s) de motor", out)

    def test_csv(self):
        out = self.run_cli("csv", fx("idle-warming.log"), "--every", "5",
                           "--signal", "ENGINE_RPM", "--signal", "COOLANT_TEMP")
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "line,ts,POWERTRAIN_DATA.ENGINE_RPM,"
                                   "POWERTRAIN_RPM.ENGINE_RPM,ENGINE_STATUS.COOLANT_TEMP")
        self.assertGreater(len(lines), 3)

    def test_decode_and_bytes(self):
        self.assertIn("LEFT_BLINKER", self.run_cli("decode", fx("left-blinker.log")))
        self.assertIn("ID 324", self.run_cli("bytes", fx("idle-warming.log"), "--id", "324"))


if __name__ == "__main__":
    unittest.main()

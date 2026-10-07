"""Leitura de frames CAN de arquivos de log.

Formatos aceitos (detectados linha a linha):

* candump padrão ............ ``  can0  17C   [8]  00 00 02 CA 00 00 00 1B``
* candump -t a/d/z .......... ``(1717000000.123456)  can0  17C   [8]  00 ...``
* candump -L (log) .......... ``(1717000000.123456) can0 17C#000002CA0000001B``
* SavvyCAN CSV .............. ``201907177,0000017C,false,Rx,0,8,00,00,02,CA,...``
  (timestamp em microssegundos)

Os logs reais em ~/claude/can-sample-*.log são candump padrão, **sem
timestamp** — ``Frame.ts`` fica ``None`` e a ordem das linhas é o único relógio.
"""

import re
from dataclasses import dataclass
from typing import Iterator, Optional

_HEX_BYTES = r"((?:[0-9A-Fa-f]{2}[ \t]*)*)"

_RE_CANDUMP = re.compile(
    r"^\s*(?:\((?P<ts>-?\d+(?:\.\d+)?)\)\s+)?(?P<iface>\S+)\s+"
    r"(?P<id>[0-9A-Fa-f]{1,8})\s+\[(?P<dlc>\d{1,2})\]\s+" + _HEX_BYTES + r"\s*$"
)
_RE_CANDUMP_L = re.compile(
    r"^\s*\((?P<ts>-?\d+(?:\.\d+)?)\)\s+(?P<iface>\S+)\s+"
    r"(?P<id>[0-9A-Fa-f]{1,8})#(?P<data>[0-9A-Fa-f]*)\s*$"
)


@dataclass(frozen=True)
class Frame:
    can_id: int
    data: bytes
    ts: Optional[float] = None      # segundos; None quando o log não tem
    iface: str = ""
    line_no: int = 0                # linha no arquivo de origem (1-based)

    @property
    def dlc(self) -> int:
        return len(self.data)

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def __str__(self) -> str:
        return f"{self.can_id:03X} [{self.dlc}] {self.hex()}"


def _savvy(line: str, line_no: int) -> Optional[Frame]:
    p = [x.strip() for x in line.rstrip(",\r\n").split(",")]
    if len(p) < 6 or not p[0].isdigit():
        return None
    try:
        dlc = int(p[5])
        data = bytes(int(x, 16) for x in p[6:6 + dlc])
        if len(data) != dlc:
            return None
        return Frame(int(p[1], 16), data, int(p[0]) / 1e6, f"bus{p[4]}", line_no)
    except ValueError:
        return None


def parse_line(line: str, line_no: int = 0) -> Optional[Frame]:
    """Converte uma linha de log em Frame; ``None`` se não for um frame."""
    m = _RE_CANDUMP.match(line)
    if m:
        data = bytes.fromhex(m.group(5))
        if len(data) != int(m.group("dlc")):
            return None
        ts = float(m.group("ts")) if m.group("ts") else None
        return Frame(int(m.group("id"), 16), data, ts, m.group("iface"), line_no)
    m = _RE_CANDUMP_L.match(line)
    if m:
        if len(m.group("data")) % 2:
            return None
        return Frame(int(m.group("id"), 16), bytes.fromhex(m.group("data")),
                     float(m.group("ts")), m.group("iface"), line_no)
    if "," in line:
        return _savvy(line, line_no)
    return None


class FrameReader:
    """Itera frames de um arquivo e conta as linhas descartadas."""

    def __init__(self, path: str):
        self.path = path
        self.skipped = 0

    def __iter__(self) -> Iterator[Frame]:
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                f = parse_line(line, n)
                if f is None:
                    self.skipped += 1
                    continue
                yield f


def read_frames(path: str) -> Iterator[Frame]:
    return iter(FrameReader(path))

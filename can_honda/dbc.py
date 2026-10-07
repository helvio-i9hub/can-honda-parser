"""Parser DBC mínimo (BO_, SG_, CM_, VAL_) e extração de sinais.

Suporta Motorola (``@0``, big-endian, bit inicial = MSB na numeração
"dente de serra" do DBC) e Intel (``@1``), com ou sem sinal. Multiplexação
não é suportada (o barramento Honda não usa).

A confiança de cada sinal vem do prefixo do comentário: ``[CONFIRMADO]``,
``[OPENDBC]`` ou ``[HIPOTESE]``.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

_RE_BO = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)")
_RE_SG = re.compile(
    r"^\s*SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*"
    r"\(\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)\s*"
    r"\[\s*([-+0-9.eE]+)\s*\|\s*([-+0-9.eE]+)\s*\]\s*\"([^\"]*)\""
)
_RE_CM_SG = re.compile(r"CM_\s+SG_\s+(\d+)\s+(\w+)\s+\"((?:[^\"\\]|\\.)*)\"\s*;", re.S)
_RE_CM_BO = re.compile(r"CM_\s+BO_\s+(\d+)\s+\"((?:[^\"\\]|\\.)*)\"\s*;", re.S)
_RE_VAL = re.compile(r"VAL_\s+(\d+)\s+(\w+)\s+((?:-?\d+\s+\"[^\"]*\"\s*)+);")
_RE_VAL_ITEM = re.compile(r"(-?\d+)\s+\"([^\"]*)\"")
_RE_CONF = re.compile(r"^\s*\[(\w+)\]\s*")

DEFAULT_DBC = os.path.join(os.path.dirname(__file__), "data", "honda_civic_2014.dbc")


@dataclass
class Signal:
    name: str
    start: int
    length: int
    little_endian: bool
    signed: bool
    factor: float
    offset: float
    minimum: float
    maximum: float
    unit: str
    comment: str = ""
    choices: Dict[int, str] = field(default_factory=dict)

    @property
    def confidence(self) -> str:
        m = _RE_CONF.match(self.comment)
        return m.group(1) if m else ""

    def raw(self, data: bytes) -> Optional[int]:
        """Valor bruto do campo, ou ``None`` se o frame for curto demais."""
        n = len(data) * 8
        if self.little_endian:
            if self.start + self.length > n:
                return None
            v = (int.from_bytes(data, "little") >> self.start) & ((1 << self.length) - 1)
        else:
            byte, bit = divmod(self.start, 8)
            msb = n - 1 - (byte * 8 + 7 - bit)       # posição no inteiro big-endian
            lsb = msb - self.length + 1
            if byte >= len(data) or lsb < 0:
                return None
            v = (int.from_bytes(data, "big") >> lsb) & ((1 << self.length) - 1)
        if self.signed and v >> (self.length - 1):
            v -= 1 << self.length
        return v

    def decode(self, data: bytes):
        r = self.raw(data)
        if r is None:
            return None
        v = r * self.factor + self.offset
        if float(self.factor).is_integer() and float(self.offset).is_integer():
            return int(v)
        return round(v, 6)


@dataclass
class Message:
    can_id: int
    name: str
    dlc: int
    sender: str
    signals: Dict[str, Signal] = field(default_factory=dict)
    comment: str = ""


@dataclass
class Database:
    messages: Dict[int, Message]
    comment: str = ""

    def by_name(self, name: str) -> Optional[Message]:
        for m in self.messages.values():
            if m.name == name:
                return m
        return None


def _num(s: str) -> float:
    f = float(s)
    return int(f) if f.is_integer() and "." not in s and "e" not in s.lower() else f


def parse_dbc(text: str) -> Database:
    msgs: Dict[int, Message] = {}
    cur = None
    for line in text.splitlines():
        m = _RE_BO.match(line)
        if m:
            cid = int(m.group(1)) & 0x1FFFFFFF
            cur = msgs[cid] = Message(cid, m.group(2), int(m.group(3)), m.group(4))
            continue
        m = _RE_SG.match(line)
        if m and cur is not None:
            g = m.groups()
            cur.signals[g[0]] = Signal(
                g[0], int(g[1]), int(g[2]), g[3] == "1", g[4] == "-",
                _num(g[5]), _num(g[6]), _num(g[7]), _num(g[8]), g[9])
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            cur = None
    for cid, name, txt in _RE_CM_SG.findall(text):
        sig = msgs.get(int(cid) & 0x1FFFFFFF, Message(0, "", 0, "")).signals.get(name)
        if sig:
            sig.comment = txt
    for cid, txt in _RE_CM_BO.findall(text):
        if int(cid) in msgs:
            msgs[int(cid)].comment = txt
    for cid, name, body in _RE_VAL.findall(text):
        sig = msgs.get(int(cid), Message(0, "", 0, "")).signals.get(name)
        if sig:
            sig.choices = {int(k): v for k, v in _RE_VAL_ITEM.findall(body)}
    top = re.search(r'^CM_\s+"((?:[^"\\]|\\.)*)"\s*;', text, re.M | re.S)
    return Database(msgs, top.group(1) if top else "")


def load_dbc(path: str) -> Database:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_dbc(fh.read())


def default_dbc() -> Database:
    return load_dbc(DEFAULT_DBC)

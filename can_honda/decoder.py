"""Decodificação de frames em sinais físicos + estado agregado do barramento."""

from typing import Dict, Optional

from .dbc import Database, default_dbc
from .frames import Frame
from .honda import Integrity

# Sinais de protocolo, omitidos da saída "física".
PROTOCOL_SIGNALS = frozenset({"COUNTER", "CHECKSUM"})


class Decoded:
    __slots__ = ("frame", "message", "values", "checksum_ok", "counter_ok")

    def __init__(self, frame, message, values, checksum_ok, counter_ok):
        self.frame = frame
        self.message = message          # dbc.Message ou None (ID sem definição)
        self.values = values            # {sinal: valor}
        self.checksum_ok = checksum_ok  # True/False/None (ID sem checksum Honda)
        self.counter_ok = counter_ok

    @property
    def name(self) -> str:
        return self.message.name if self.message else f"ID_{self.frame.can_id:03X}"

    def text(self, db_choices: bool = True) -> str:
        parts = []
        for k, v in self.values.items():
            sig = self.message.signals[k]
            label = sig.choices.get(v) if db_choices and isinstance(v, int) else None
            unit = f" {sig.unit}" if sig.unit else ""
            parts.append(f"{k}={label}({v})" if label else f"{k}={v}{unit}")
        return "  ".join(parts) if parts else self.frame.hex()


class Decoder:
    def __init__(self, db: Optional[Database] = None, protocol: bool = False):
        self.db = db or default_dbc()
        self.protocol = protocol
        self.integrity = Integrity()

    def decode(self, frame: Frame) -> Decoded:
        ck, ct = self.integrity.feed(frame.can_id, frame.data)
        msg = self.db.messages.get(frame.can_id)
        values: Dict[str, object] = {}
        if msg:
            for name, sig in msg.signals.items():
                if not self.protocol and name in PROTOCOL_SIGNALS:
                    continue
                v = sig.decode(frame.data)
                if v is not None:
                    values[name] = v
        return Decoded(frame, msg, values, ck, ct)


class BusState:
    """Último valor conhecido de cada sinal (``MENSAGEM.SINAL``)."""

    def __init__(self):
        self.values: Dict[str, object] = {}

    def update(self, d: Decoded) -> Dict[str, tuple]:
        """Atualiza e devolve ``{chave: (antes, depois)}`` do que mudou."""
        changed = {}
        if d.message is None or d.checksum_ok is False:
            return changed
        for k, v in d.values.items():
            key = f"{d.message.name}.{k}"
            old = self.values.get(key)
            if old != v:
                changed[key] = (old, v)
                self.values[key] = v
        return changed

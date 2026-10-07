"""Checksum e contador de mensagem do protocolo Honda.

Último byte de cada frame: bits 5:4 = contador de 2 bits (0..3, rola),
bits 3:0 = checksum. O checksum é a soma de todos os nibbles do ID e dos
dados (exceto o próprio nibble do checksum), ``(8 - soma) & 0xF``.

Validado nos logs reais: 100% dos frames batem, exceto 0x255.
"""

# IDs vistos nos logs que não seguem o esquema.
NO_CHECKSUM = frozenset({0x255})
NO_COUNTER = frozenset({0x255, 0x1D0})   # 0x1D0: bits 5:4 são parte de WHEEL_SPEED_RR


def honda_checksum(can_id: int, data: bytes) -> int:
    s = 0
    a = can_id
    while a:
        s += a & 0xF
        a >>= 4
    last = len(data) - 1
    for i, x in enumerate(data):
        if i == last:
            x >>= 4
        while x:
            s += x & 0xF
            x >>= 4
    return (8 - s) & 0xF


def checksum_ok(can_id: int, data: bytes) -> bool:
    return bool(data) and (data[-1] & 0xF) == honda_checksum(can_id, data)


def counter_of(data: bytes) -> int:
    return (data[-1] >> 4) & 0x3


class Integrity:
    """Acumula checksum e saltos de contador por ID."""

    def __init__(self):
        self.frames = {}
        self.bad_checksum = {}
        self.counter_jumps = {}
        self._last_counter = {}

    def new_file(self):
        """Arquivos diferentes não têm continuidade de contador."""
        self._last_counter.clear()

    def feed(self, can_id: int, data: bytes) -> tuple:
        """Retorna (checksum_ok | None, counter_ok | None) para este frame."""
        self.frames[can_id] = self.frames.get(can_id, 0) + 1
        ck = None
        if can_id not in NO_CHECKSUM and data:
            ck = checksum_ok(can_id, data)
            if not ck:
                self.bad_checksum[can_id] = self.bad_checksum.get(can_id, 0) + 1
        ct = None
        if can_id not in NO_COUNTER and data:
            c = counter_of(data)
            prev = self._last_counter.get(can_id)
            self._last_counter[can_id] = c
            if prev is not None:
                ct = c == (prev + 1) & 0x3
                if not ct:
                    self.counter_jumps[can_id] = self.counter_jumps.get(can_id, 0) + 1
        return ck, ct

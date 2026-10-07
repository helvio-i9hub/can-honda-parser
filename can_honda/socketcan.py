"""Leitura ao vivo via SocketCAN (Linux), só com o módulo ``socket``.

Sobe a interface antes:  sudo ip link set can0 up type can bitrate 500000
"""

import socket
import struct
import time
from typing import Iterator

from .frames import Frame

_CAN_FRAME = struct.Struct("=IB3x8s")
_EFF_FLAG, _RTR_FLAG, _ERR_FLAG = 0x80000000, 0x40000000, 0x20000000


def live_frames(iface: str = "can0") -> Iterator[Frame]:
    if not hasattr(socket, "AF_CAN"):
        raise OSError("SocketCAN indisponível nesta plataforma")
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    n = 0
    try:
        while True:
            pkt = s.recv(_CAN_FRAME.size)
            can_id, dlc, data = _CAN_FRAME.unpack(pkt)
            if can_id & (_RTR_FLAG | _ERR_FLAG):
                continue
            n += 1
            yield Frame(can_id & (0x1FFFFFFF if can_id & _EFF_FLAG else 0x7FF),
                        data[:dlc], time.time(), iface, n)
    finally:
        s.close()

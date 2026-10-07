"""SocketCAN (Linux) só com o módulo ``socket``: leitura ao vivo e
injeção de logs em interface **virtual** (vcan).

Interface física (adaptador no carro):
    sudo ip link set can0 up type can bitrate 500000
Interface virtual, para alimentar o parser/dashboard sem o carro:
    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 up
"""

import os
import socket
import struct
import time
from typing import Iterable, Iterator, Optional

from .frames import Frame

_CAN_FRAME = struct.Struct("=IB3x8s")
_EFF_FLAG, _RTR_FLAG, _ERR_FLAG = 0x80000000, 0x40000000, 0x20000000


def _socket(iface: str) -> socket.socket:
    if not hasattr(socket, "AF_CAN"):
        raise OSError("SocketCAN indisponível nesta plataforma")
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    try:
        s.bind((iface,))
    except OSError:
        s.close()
        raise
    return s


def interface_kind(iface: str) -> Optional[str]:
    """``"virtual"`` (vcan, sem dispositivo), ``"physical"`` ou ``None`` se não existe."""
    base = f"/sys/class/net/{iface}"
    if not os.path.isdir(base):
        return None
    return "physical" if os.path.exists(os.path.join(base, "device")) else "virtual"


def live_frames(iface: str = "can0") -> Iterator[Frame]:
    s = _socket(iface)
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


def play(frames: Iterable[Frame], iface: str, rate: float = 1000.0,
         speed: float = 1.0, allow_physical: bool = False) -> int:
    """Transmite ``frames`` em ``iface``. Devolve quantos foram enviados.

    Ritmo: com timestamp no log, respeita os intervalos originais (÷ ``speed``);
    sem timestamp, ``rate`` frames/s. ``rate <= 0`` = o mais rápido possível.
    Recusa interface física salvo ``allow_physical`` — o parser nunca deve
    transmitir no barramento do carro por engano.
    """
    kind = interface_kind(iface)
    if kind is None:
        raise OSError(f"interface {iface} não existe")
    if kind == "physical" and not allow_physical:
        raise PermissionError(f"{iface} é uma interface física; play só envia para vcan")
    s = _socket(iface)
    sent = 0
    t_wall = time.monotonic()
    t_log0 = None
    try:
        for f in frames:
            if f.ts is not None and speed > 0:
                if t_log0 is None:
                    t_log0 = f.ts
                due = t_wall + (f.ts - t_log0) / speed
            elif rate > 0:
                due = t_wall + sent / rate
            else:
                due = 0
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            can_id = f.can_id | (_EFF_FLAG if f.can_id > 0x7FF else 0)
            pkt = _CAN_FRAME.pack(can_id, len(f.data), bytes(f.data).ljust(8, b"\0"))
            while True:
                try:
                    s.send(pkt)
                    break
                except BlockingIOError:      # fila do vcan cheia
                    time.sleep(0.001)
                except OSError as e:
                    if e.errno == 105:       # ENOBUFS
                        time.sleep(0.001)
                        continue
                    raise
            sent += 1
    finally:
        s.close()
    return sent

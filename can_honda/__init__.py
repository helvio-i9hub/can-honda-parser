"""can_honda — parser de logs CAN do Honda Civic 2014 (F-CAN, 11 bits).

Python stdlib puro. Lê candump / SavvyCAN CSV, valida checksum+contador
Honda e decodifica sinais a partir de um DBC (``data/honda_civic_2014.dbc``).
"""

from .frames import Frame, read_frames, parse_line
from .honda import honda_checksum, checksum_ok, counter_of
from .dbc import load_dbc, default_dbc
from .decoder import Decoder

__all__ = [
    "Frame", "read_frames", "parse_line",
    "honda_checksum", "checksum_ok", "counter_of",
    "load_dbc", "default_dbc", "Decoder",
]

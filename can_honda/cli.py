"""Linha de comando: ``python3 -m can_honda <comando> ...``

Comandos:
  stats    IDs do log: frames, DLC, checksum/contador Honda, se há definição no DBC
  decode   sinais decodificados (por padrão, só quando o valor muda)
  csv      snapshot do estado do barramento em CSV (um registro a cada N frames de um ID)
  bytes    análise por byte de um ID (valores distintos, nº de mudanças) — para descobrir sinais
  summary  ciclos de motor (RPM > 0): duração, RPM, temperaturas, combustível
  web      dashboard no navegador (sinais, gráficos, integridade, ciclos de motor);
           com --live can0 --gps /dev/ttyACM0, painel de telemetria com mapa em /telemetry
  signals  lista os sinais do DBC com unidade e confiança
  live     igual ao decode, mas lendo de uma interface SocketCAN (can0, vcan0…)
  play     injeta um log numa interface virtual (vcan0) — entrada para live/web sem o carro
"""

import argparse
import csv
import itertools
import os
import sys
from collections import defaultdict

from .dbc import DEFAULT_DBC, load_dbc
from .decoder import BusState, Decoder
from .frames import FrameReader
from .honda import NO_CHECKSUM


def _hexid(s: str) -> int:
    return int(s, 16)


def _frames(paths, dec=None):
    for p in paths:
        if dec is not None:
            dec.integrity.new_file()
        reader = FrameReader(p)
        yield from reader
        if reader.skipped:
            print(f"# {p}: {reader.skipped} linha(s) ignorada(s)", file=sys.stderr)


def _wanted(args):
    ids = set(args.id or [])
    sigs = {s.upper() for s in (args.signal or [])}
    return ids, sigs


# ---------------------------------------------------------------- stats
def cmd_stats(args):
    dec = Decoder(load_dbc(args.dbc))
    dlcs = defaultdict(set)
    first_ts, last_ts = {}, {}
    total = 0
    for f in _frames(args.files, dec):
        total += 1
        dec.integrity.feed(f.can_id, f.data)
        dlcs[f.can_id].add(f.dlc)
        if f.ts is not None:
            first_ts.setdefault(f.can_id, f.ts)
            last_ts[f.can_id] = f.ts
    it = dec.integrity
    print(f"{total} frames, {len(it.frames)} IDs")
    print(f"{'ID':>4} {'frames':>8} {'%':>6} {'Hz':>7} DLC  {'cksum':>11} {'cont.salto':>10}  mensagem")
    for cid in sorted(it.frames):
        n = it.frames[cid]
        hz = ""
        if cid in first_ts and last_ts[cid] > first_ts[cid]:
            hz = f"{(n - 1) / (last_ts[cid] - first_ts[cid]):7.1f}"
        bad = it.bad_checksum.get(cid, 0)
        ck = "n/a" if cid in NO_CHECKSUM else f"{n - bad}/{n}"
        msg = dec.db.messages.get(cid)
        print(f"{cid:4X} {n:8d} {100 * n / total:6.2f} {hz:>7} "
              f"{','.join(map(str, sorted(dlcs[cid]))):>3}  {ck:>11} "
              f"{it.counter_jumps.get(cid, 0):10d}  {msg.name if msg else '-'}")
    return 0


# ---------------------------------------------------------------- decode/live
def _print_decoded(frames, args):
    dec = Decoder(load_dbc(args.dbc), protocol=args.protocol)
    state = BusState()
    ids, sigs = _wanted(args)
    for f in frames:
        if ids and f.can_id not in ids:
            continue
        d = dec.decode(f)
        if d.message is None and not args.unknown:
            continue
        where = f"{f.ts:.6f}" if f.ts is not None else f"L{f.line_no}"
        flag = "" if d.checksum_ok is not False else "  !CHECKSUM"
        if args.all or d.message is None:
            if sigs:
                d.values = {k: v for k, v in d.values.items() if k.upper() in sigs}
                if not d.values:
                    continue
            print(f"{where:>12} {f.can_id:03X} {d.name:<18} {d.text()}{flag}")
            continue
        for key, (old, new) in state.update(d).items():
            if sigs and key.split(".", 1)[1].upper() not in sigs:
                continue
            unit = d.message.signals[key.split(".", 1)[1]].unit
            print(f"{where:>12} {f.can_id:03X} {key:<40} {old} -> {new}{' ' + unit if unit else ''}")
    return 0


def cmd_decode(args):
    return _print_decoded(_frames(args.files), args)


def cmd_live(args):
    from .socketcan import live_frames
    try:
        return _print_decoded(live_frames(args.iface), args)
    except KeyboardInterrupt:
        return 0


def cmd_play(args):
    from .socketcan import play
    total = 0
    try:
        rounds = itertools.count() if args.loop < 0 else range(max(1, args.loop))
        for n in rounds:
            for path in args.files:
                total += play(FrameReader(path), args.iface, args.rate, args.speed)
            print(f"# volta {n + 1}: {total} frames enviados para {args.iface}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    except (OSError, PermissionError) as e:
        print(f"erro: {e}", file=sys.stderr)
        if "não existe" in str(e):
            print("crie a vcan:  sudo modprobe vcan && sudo ip link add dev "
                  f"{args.iface} type vcan && sudo ip link set {args.iface} up", file=sys.stderr)
        return 2
    print(f"{total} frames enviados para {args.iface}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- csv
def cmd_csv(args):
    db = load_dbc(args.dbc)
    dec = Decoder(db)
    state = BusState()
    _, sigs = _wanted(args)
    cols = [f"{m.name}.{s}" for m in sorted(db.messages.values(), key=lambda m: m.can_id)
            for s in m.signals if s not in ("COUNTER", "CHECKSUM")
            and (not sigs or s.upper() in sigs)]
    out = open(args.output, "w", newline="") if args.output else sys.stdout
    w = csv.writer(out)
    w.writerow(["line", "ts"] + cols)
    seen = 0
    for f in _frames(args.files):
        state.update(dec.decode(f))
        if f.can_id == args.ref:
            seen += 1
            if seen % args.every == 0:
                w.writerow([f.line_no, "" if f.ts is None else f"{f.ts:.6f}"]
                           + [state.values.get(c, "") for c in cols])
    if out is not sys.stdout:
        out.close()
    return 0


# ---------------------------------------------------------------- bytes
def cmd_bytes(args):
    vals = defaultdict(lambda: defaultdict(set))
    changes = defaultdict(lambda: defaultdict(int))
    last, count = {}, defaultdict(int)
    for f in _frames(args.files):
        if args.id and f.can_id not in args.id:
            continue
        count[f.can_id] += 1
        prev = last.get(f.can_id)
        for i, b in enumerate(f.data):
            vals[f.can_id][i].add(b)
            if prev is not None and i < len(prev) and prev[i] != b:
                changes[f.can_id][i] += 1
        last[f.can_id] = f.data
    for cid in sorted(count):
        print(f"\nID {cid:03X}  frames={count[cid]}")
        for i in sorted(vals[cid]):
            v = vals[cid][i]
            shown = " ".join(f"{x:02X}" for x in sorted(v)) if len(v) <= 12 \
                else f"min={min(v):02X} max={max(v):02X}"
            print(f"  b{i}  distintos={len(v):3d}  mudancas={changes[cid][i]:8d}  {shown}")
    return 0


# ---------------------------------------------------------------- summary
def cmd_summary(args):
    from .analysis import Analysis
    db = load_dbc(args.dbc)
    for path in args.files:
        a = Analysis(path, db)
        if len(args.files) > 1:
            print(f"== {path}")
        runs = a.engine_runs()
        print(f"{len(runs)} ciclo(s) de motor")
        for i, r in enumerate(runs, 1):
            dur = (f"{r['x1'] - r['x0']:.1f} s" if a.x_kind == "time"
                   else f"{r['frames']} frames 0x17C (sem timestamp)")
            print(f"  #{i}: linhas {r['line0']}-{r['line1']}  {dur}\n"
                  f"      RPM max {r['rpm_max']}  marcha lenta final ~{r['idle_end']}\n"
                  f"      liquido arref. {r['coolant0']} -> {r['coolant1']} C   "
                  f"ar admissao {r['intake0']} -> {r['intake1']} C   "
                  f"TRIP_FUEL_CONSUMED {r['fuel']}")
    return 0


def cmd_web(args):
    from .web import serve
    sources = args.sources
    if not sources and not (args.live or args.replay or args.vcan or args.gps or args.gps_replay):
        sources = [os.path.expanduser("~/claude/can*.log")]
    serve(sources, args.dbc, args.host, args.port,
          live_ifaces=(args.live or []) + (["vcan0"] if args.vcan else []), replays=args.replay or (),
          retention=args.retention, replay_rate=args.replay_rate,
          gps_device=args.gps, gps_replay=args.gps_replay,
          gps_record_dir=None if args.no_gps_record else args.gps_record)
    return 0


# ---------------------------------------------------------------- signals
def cmd_signals(args):
    db = load_dbc(args.dbc)
    for m in sorted(db.messages.values(), key=lambda m: m.can_id):
        print(f"{m.can_id:03X} {m.name} [{m.dlc}]" + (f"  — {m.comment}" if m.comment else ""))
        for s in m.signals.values():
            if s.name in ("COUNTER", "CHECKSUM"):
                continue
            print(f"     {s.name:<26} {s.start:>2}|{s.length:<2} {s.unit:<4} "
                  f"{s.confidence or '-':<11} {_strip_conf(s.comment)}")
    return 0


def _strip_conf(c: str) -> str:
    return c.split("]", 1)[1].strip() if c.startswith("[") else c


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="can_honda", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dbc", default=DEFAULT_DBC, help="arquivo DBC (padrão: Civic 2014)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def files(sp):
        sp.add_argument("files", nargs="+", help="logs candump / SavvyCAN CSV")

    def filters(sp):
        sp.add_argument("--id", type=_hexid, action="append", help="filtra ID hex (repetível)")
        sp.add_argument("--signal", action="append", help="filtra sinal por nome (repetível)")

    def decode_opts(sp):
        filters(sp)
        sp.add_argument("--all", action="store_true", help="todo frame, não só mudanças")
        sp.add_argument("--unknown", action="store_true", help="mostra IDs sem definição (hex)")
        sp.add_argument("--protocol", action="store_true", help="inclui COUNTER/CHECKSUM")

    sp = sub.add_parser("stats"); files(sp); sp.set_defaults(fn=cmd_stats)
    sp = sub.add_parser("decode"); files(sp); decode_opts(sp); sp.set_defaults(fn=cmd_decode)
    sp = sub.add_parser("live"); sp.add_argument("iface", nargs="?", default="can0")
    decode_opts(sp); sp.set_defaults(fn=cmd_live)
    sp = sub.add_parser("play", help="injeta log em vcan")
    sp.add_argument("files", nargs="+", help="logs candump / SavvyCAN CSV")
    sp.add_argument("--iface", default="vcan0", help="interface virtual de destino (padrão vcan0)")
    sp.add_argument("--rate", type=float, default=1000.0,
                    help="frames/s para logs sem timestamp (padrão 1000 ≈ F-CAN do Civic; 0 = máximo)")
    sp.add_argument("--speed", type=float, default=1.0, help="fator de velocidade para logs com timestamp")
    sp.add_argument("--loop", type=int, default=1, help="repetições (padrão 1; -1 = infinito)")
    sp.set_defaults(fn=cmd_play)
    sp = sub.add_parser("csv"); files(sp); filters(sp)
    sp.add_argument("-o", "--output")
    sp.add_argument("--ref", type=_hexid, default=0x17C, help="ID que dispara cada linha (padrão 17C)")
    sp.add_argument("--every", type=int, default=10, help="uma linha a cada N frames do --ref")
    sp.set_defaults(fn=cmd_csv)
    sp = sub.add_parser("bytes"); files(sp)
    sp.add_argument("--id", type=_hexid, action="append"); sp.set_defaults(fn=cmd_bytes)
    sp = sub.add_parser("summary"); files(sp); sp.set_defaults(fn=cmd_summary)
    sp = sub.add_parser("signals"); sp.set_defaults(fn=cmd_signals)
    sp = sub.add_parser("web", help="dashboard web")
    sp.add_argument("sources", nargs="*", help="logs, globs ou pastas (padrão: ~/claude/can*.log)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8090)
    sp.add_argument("--live", action="append", metavar="IFACE",
                    help="interface SocketCAN ao vivo, ex.: --live can0 (repetível)")
    sp.add_argument("--vcan", action="store_true",
                    help="atalho para --live vcan0 (interface virtual; alimente com 'play')")
    sp.add_argument("--replay", action="append", metavar="LOG",
                    help="reemite um log em tempo real como fonte ao vivo (teste sem carro)")
    sp.add_argument("--replay-rate", type=float, default=1000.0, help="frames/s do replay (padrão 1000)")
    sp.add_argument("--retention", type=float, default=600.0,
                    help="segundos guardados nas séries ao vivo (padrão 600)")
    sp.add_argument("--gps", metavar="DEV", help="GPS NMEA serial, ex.: --gps /dev/ttyACM0 (painel /telemetry)")
    sp.add_argument("--gps-replay", metavar="LOG", help="reemite um log NMEA gravado como GPS ao vivo")
    sp.add_argument("--gps-record", metavar="DIR", default="~/claude",
                    help="pasta onde gravar o NMEA do --gps (padrão ~/claude, gps-nmea-*.log)")
    sp.add_argument("--no-gps-record", action="store_true", help="não grava o NMEA do --gps")
    sp.set_defaults(fn=cmd_web)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:
        return 0

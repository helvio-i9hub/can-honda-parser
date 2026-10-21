# can-honda-parser — notas para o Claude

Parser de CAN do Honda Civic 2014 (F-CAN 500k, 11 bits). **Python stdlib pura** —
não adicionar dependências (python-can, cantools).

## Estrutura
- `can_honda/frames.py` — leitores candump (padrão/-t/-L) + SavvyCAN CSV, detecção por linha
- `can_honda/honda.py` — checksum (soma de nibbles, `(8-s)&0xF`) e contador (bits 5:4 do último byte);
  exceções `NO_CHECKSUM={0x255}`, `NO_COUNTER={0x255,0x1D0}`
- `can_honda/dbc.py` — parser DBC mínimo (BO_/SG_/CM_/VAL_), Motorola+Intel, signed
- `can_honda/data/honda_civic_2014.dbc` — **a fonte de verdade dos sinais**
- `can_honda/decoder.py` — Decoder (frame → valores + integridade) e BusState (último valor/mudanças)
- `can_honda/cli.py` — stats / decode / csv / bytes / summary / signals / live / web
- `can_honda/analysis.py` — passada única no log: séries brutas por sinal (array), estatísticas,
  ciclos de motor, saltos de contador agrupados; reamostra (raw ou faixa mín/máx/média) por recorte
- `can_honda/web.py` + `static/index.html` — dashboard (http.server, SVG à mão, sem CDN);
  só serve arquivos da lista (por nome), padrão `~/claude/can*.log`, porta 8090
- `can_honda/live.py` — LiveSource (thread + lock + retenção) para `--live can0` (socketcan.py) e
  `--replay log` (reemite em tempo real); Analysis é incremental (`feed`/`trim`) e serve aos dois
- `socketcan.py` — `live_frames` (leitura) e `play` (injeção) só com `socket`; `play` RECUSA interface
  física (`interface_kind`: sem link `device` no sysfs = virtual). `web --vcan` = `--live vcan0`
- `can_honda/gps.py` — GpsSource (thread serial `/dev/ttyACM0` raw + reconexão, replay, gravação
  `(ts) $GP...`); `nmea.py` é CÓPIA do gps-uploader (corrigir lá primeiro)
- `can_honda/telemetry.py` — estado CAN (listener do LiveSource) + GPS → derivadas (marcha por RPM/km/h,
  G long./lat., erro do velocímetro, distâncias, rampa, VIN de 0x40C); painel `static/telemetry.html`
  em `/telemetry` (Leaflet vendorizado em `static/vendor/leaflet`, tiles OSM)
- `tools/capture-can.sh` — gravador candump -L que espera a interface e segmenta se ela cair

## Dados reais
- `~/claude/can-sample-0.log` e `can-sample-1.log` — candump padrão, SEM timestamp, carro parado
- `~/can.log` é prefixo idêntico de can-sample-0.log; `~/savvy-honda-obd` é CSV do SavvyCAN (com ts µs)
- scripts antigos de referência (não confiáveis): `~/Downloads/civic_can.py`, `python-can-decode-v1.py`

## Fatos dos logs
- `can0` (gs_usb, 500k) existe nesta máquina quando o adaptador está no carro; sem sudo sem senha.
  ATENÇÃO: o usuário às vezes cria uma **vcan chamada can0** — checar `ip -details link show can0`
  (gs_usb tem `ECHO`/bitrate; vcan diz `vcan`) antes de tratar dados como do carro.
  Captura com timestamp: `~/claude/can-live-20261007.log` (10 s, -L). Taxas: 100/50/25/10/5/3,3 Hz (README).
- Para parar o servidor use o PID (`ss -ltnp 'sport = :8090'`); `pkill -f "can_honda web"` mata o próprio shell.
- Saltos de contador só nos ciclos de ignição (sample-0 L~505600/~512700, sample-1 L~149600) =
  módulos reiniciando, não perda de captura. (Cuidado: no `stats` a coluna Hz vazia desloca campos no awk.)
- 19/10/2026: primeiras capturas ANDANDO (`~/claude/can-drive-20261019-161919.log` + `gps-nmea-*.log`).
  Velocidades confirmadas; GPS ~4-6% abaixo do velocímetro. RPM/km/h: 1ª ~124, 2ª ~74, 3ª ~49; 4ª ~36 (21/10, viagem da caixa em
  ~/claude/can-drive-20261021/). TRIP_FUEL_CONSUMED: unidade provável 0,1 mL (hipótese, 8,4 km/L).
  0x1B0 (STANDSTILL) fica todo zerado andando — layout opendbc não vale. GPS: u-blox 7, 1 Hz.

## Regras
- Todo sinal novo no DBC leva tag de confiança no `CM_ SG_`: `[CONFIRMADO]` só com evidência nos logs
  (dizer qual), `[OPENDBC]` quando vem do layout opendbc, `[HIPOTESE]` caso contrário.
  O teste `test_default_dbc_loads_comments_and_choices` exige a tag.
- Fixtures de teste = trechos reais (`sed -n 'A,Bp'`), nunca dados sintéticos para sinais.
- IDs sem definição ainda: 039 18E 1ED 21E 221 295 320 328 372 378 386 3D7 428 42D 42E 454 6C1.
  0x40C = VIN em ASCII (byte 0 = página 1..3), decodificado em telemetry.py (DBC não tem string).
  Use `bytes --id` e `decode --unknown --id` para investigar.

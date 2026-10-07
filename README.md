# can-honda-parser

Parser de logs CAN do **Honda Civic 2014** (9ª geração, F-CAN 500 kbit/s,
IDs de 11 bits). Python **stdlib pura**, sem `python-can` nem `cantools`.

- lê **candump** (padrão, `-t`, `-L`) e **SavvyCAN CSV**
- valida **checksum e contador Honda** de cada frame
- decodifica sinais a partir de um **DBC** (`can_honda/data/honda_civic_2014.dbc`),
  que também abre no SavvyCAN / CANdevStudio
- lê **ao vivo** via SocketCAN (`socket.AF_CAN`)

## Uso

```bash
python3 -m can_honda stats   ~/claude/can-sample-0.log      # IDs, checksum, contador
python3 -m can_honda summary ~/claude/can-sample-0.log      # ciclos de motor
python3 -m can_honda decode  ~/claude/can-sample-1.log --signal LEFT_BLINKER --signal BRAKE_PRESSED
python3 -m can_honda csv     ~/claude/can-sample-0.log -o estado.csv --every 10
python3 -m can_honda bytes   ~/claude/can-sample-0.log --id 18E   # caçar sinal em ID desconhecido
python3 -m can_honda signals                                # o que o DBC sabe, com confiança
python3 -m can_honda live can0                              # sudo ip link set can0 up type can bitrate 500000
python3 -m can_honda web                                    # dashboard em http://127.0.0.1:8090/
```

## Dashboard web

```bash
python3 -m can_honda web                       # padrão: ~/claude/can*.log
python3 -m can_honda web ~/capturas/ outro.log --port 8090 --host 0.0.0.0
```

- indicadores (frames, checksum, saltos de contador, ciclos de motor, arrefecimento)
- ciclos de motor com botão de zoom
- um gráfico por sinal variável (faixa mín/máx + média quando reamostrado,
  degrau para sinais discretos) e uma linha do tempo para os sinais de 1 bit
- crosshair sincronizado em todos os gráficos; **arraste para dar zoom**
  (o servidor reamostra o trecho), duplo clique volta
- tabelas: todos os sinais (mín/máx/último/mudanças), integridade por ID, saltos de contador
- filtro por confiança e por nome

### Modo online (ao vivo)

```bash
sudo ip link set can0 up type can bitrate 500000      # adaptador gs_usb / CANable
python3 -m can_honda web --live can0                  # só a can0
python3 -m can_honda web ~/claude/can*.log --live can0  # can0 e os arquivos no mesmo seletor
python3 -m can_honda web --replay ~/claude/can-sample-1.log   # simula ao vivo sem o carro
```

- `● can0 — ao vivo` aparece no seletor; a página atualiza a cada 1 s
- janela móvel de 30 s / 2 min / 5 min / 10 min, eixo X em hora local
- arrastar num gráfico **pausa** e dá zoom; duplo clique ou "Seguir ao vivo" volta
- indicador de estado: recebendo (frames/s), sem frames (barramento quieto) ou erro
  da interface — a leitura tenta reconectar a cada 2 s se a `can0` cair
- o servidor guarda os últimos `--retention` segundos (padrão 600) das séries;
  mín/máx/mudanças, integridade e ciclos de motor valem desde o início
- leitura SocketCAN com o módulo `socket` da stdlib (sem python-can); não transmite nada

Só os arquivos passados (ou os `*.log`/`*.csv` das pastas) ficam acessíveis.
O primeiro carregamento de um log de 43 MB leva ~6 s; depois fica em cache
(invalidado pelo mtime).

`decode` imprime só **mudanças** de valor (`--all` para todo frame,
`--unknown` para IDs sem definição). Frames com checksum ruim aparecem
com `!CHECKSUM` e não atualizam o estado.

## O que os logs reais mostram

`can-sample-0.log` (1 131 059 frames) e `can-sample-1.log` (155 675 frames),
candump **sem timestamp**, carro **parado**:

- checksum Honda bate em **100%** dos frames de 34 dos 35 IDs (0x255 não usa)
- saltos de contador (40 no sample-0, 11 no sample-1) só nos **ciclos de ignição**
  (linhas ~505 600 e ~512 700 do sample-0, ~149 600 do sample-1), em quase todos
  os IDs ao mesmo tempo → módulos reiniciando, não perda de captura
- 3 ciclos de motor; partida a frio: líquido 23 → 65 °C, marcha lenta 1300 → 765 rpm
- sequência de teste visível em `can-sample-1`: pisca esq./dir., farol 0→3,
  portas traseira esq./dianteira dir., freio

Cada sinal do DBC tem a confiança no comentário:

| tag | significado |
|---|---|
| `[CONFIRMADO]` | comportamento verificado nos logs (RPM, temperaturas, pisca, freio, pedal) |
| `[OPENDBC]` | layout do opendbc, coerente mas não exercitado (velocidades: carro parado) |
| `[HIPOTESE]` | palpite |

Frequências medidas ao vivo na `can0` (captura `candump -L` de 07/10/2026,
~981 frames/s no total, 0 erros):

| Hz | IDs |
|---|---|
| 100 | 13C 158 17C 18E |
| 50 | 1A4 1A6 1B0 1D0 1DC 1ED |
| 25 | 039 21E 221 255 294 295 |
| 10 | 305 309 320 324 328 372 374 377 378 386 |
| 5 | 3D7 |
| 3,3 | 405 40C 428 42D 42E 454 465 6C1 |

**Não verificável com estes logs:** velocidade do veículo/rodas (sempre 0),
escala do torque em 0x13C, unidade de `TRIP_FUEL_CONSUMED`. Os can-sample-*
não têm timestamp; capture com `candump -L` (ex.: `~/claude/can-live-20261007.log`).

## Testes

```bash
python3 -m unittest discover -s tests
```

As fixtures em `tests/fixtures/` são trechos **reais** dos logs acima
(partida, aquecimento, pisca, porta, freio).

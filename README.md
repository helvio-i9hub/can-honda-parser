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
```

`decode` imprime só **mudanças** de valor (`--all` para todo frame,
`--unknown` para IDs sem definição). Frames com checksum ruim aparecem
com `!CHECKSUM` e não atualizam o estado.

## O que os logs reais mostram

`can-sample-0.log` (1 131 059 frames) e `can-sample-1.log` (155 675 frames),
candump **sem timestamp**, carro **parado**:

- checksum Honda bate em **100%** dos frames de 34 dos 35 IDs (0x255 não usa);
  **nenhum salto de contador** → captura sem perda de frames
- 3 ciclos de motor; partida a frio: líquido 23 → 65 °C, marcha lenta 1300 → 765 rpm
- sequência de teste visível em `can-sample-1`: pisca esq./dir., farol 0→3,
  portas traseira esq./dianteira dir., freio

Cada sinal do DBC tem a confiança no comentário:

| tag | significado |
|---|---|
| `[CONFIRMADO]` | comportamento verificado nos logs (RPM, temperaturas, pisca, freio, pedal) |
| `[OPENDBC]` | layout do opendbc, coerente mas não exercitado (velocidades: carro parado) |
| `[HIPOTESE]` | palpite |

**Não verificável com estes logs:** velocidade do veículo/rodas (sempre 0),
escala do torque em 0x13C, unidade de `TRIP_FUEL_CONSUMED`. Sem timestamp
não há frequência por ID — capture com `candump -L` ou `-ta` para ter.

## Testes

```bash
python3 -m unittest discover -s tests
```

As fixtures em `tests/fixtures/` são trechos **reais** dos logs acima
(partida, aquecimento, pisca, porta, freio).

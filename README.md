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

**Não verificável com estes logs:** velocidade do veículo/rodas (sempre 0),
escala do torque em 0x13C, unidade de `TRIP_FUEL_CONSUMED`. Sem timestamp
não há frequência por ID — capture com `candump -L` ou `-ta` para ter.

## Testes

```bash
python3 -m unittest discover -s tests
```

As fixtures em `tests/fixtures/` são trechos **reais** dos logs acima
(partida, aquecimento, pisca, porta, freio).

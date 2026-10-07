# Honda Civic 2014 — IDs CAN confirmados

Atualizado em 07/10/2026. Versão editável: [Claude Docs](https://claude.ai/code/artifact/d0c70986-d6cb-41d5-a5c5-efbfb3ae3805). Fonte da verdade dos sinais: [`can_honda/data/honda_civic_2014.dbc`](../can_honda/data/honda_civic_2014.dbc).

## Resumo

Seis IDs do F-CAN do Civic 2014 têm sinais confirmados por medição: 0x13C, 0x17C, 0x1DC, 0x255, 0x294 e 0x324. Eles cobrem RPM, acelerador, freio, pisca, temperaturas do motor e combustível do trecho.

O barramento é o F-CAN de 500 kbit/s, com IDs de 11 bits. Foram vistos 35 IDs; 14 têm definição no DBC do projeto (`can_honda/data/honda_civic_2014.dbc`). O esquema de checksum e contador da Honda vale para 34 dos 35 IDs.

Evidências usadas, todas com o carro parado:

| Captura | Formato | Frames | Conteúdo |
| --- | --- | --- | --- |
| `can-sample-0.log` | candump, sem timestamp | 1.131.059 | 2 ciclos de motor, partida a frio, ciclos de ignição |
| `can-sample-1.log` | candump, sem timestamp | 155.675 | 1 ciclo de motor; pisca, farol, portas e freio acionados em sequência |
| `can-live-20261007.log` | candump -L, ao vivo na can0 em 07/10/2026 | 9.812 (10 s) | partida e marcha lenta fria; base das frequências medidas |

Critério de confiança:

- **Confirmado**: comportamento verificado nos logs (curva física coerente, correlação com outro sinal ou eco do mesmo valor em outro ID).
- **opendbc**: layout do projeto opendbc, coerente com os logs mas não exercitado (por exemplo, velocidades com o carro parado).
- **Hipótese**: palpite, só como pista.

## IDs confirmados

Cada ID abaixo tem pelo menos um sinal verificado; os demais sinais do mesmo ID estão na última seção.

| ID | Mensagem (DBC) | Frequência | DLC | Sinais confirmados | Evidência principal |
| --- | --- | --- | --- | --- | --- |
| 0x13C | GAS\_PEDAL\_2 | 100 Hz | 8 | CAR\_GAS | ecoa POWERTRAIN\_DATA.PEDAL\_GAS byte a byte |
| 0x17C | POWERTRAIN\_DATA | 100 Hz | 8 | ENGINE\_RPM, PEDAL\_GAS, BRAKE\_PRESSED | RPM idêntico a 0x1DC; curva de marcha lenta fria → quente |
| 0x1DC | POWERTRAIN\_RPM | 50 Hz | 4 | ENGINE\_RPM | idêntico a 0x17C em todos os frames |
| 0x255 | ROUGH\_WHEEL\_SPEED | 25 Hz | 8 | SET\_TO\_X55, SET\_TO\_X55\_2, LONG\_COUNTER | bytes fixos 0x55 0x55 e contador 00..FF |
| 0x294 | SCM\_FEEDBACK | 25 Hz | 8 | LEFT\_BLINKER, RIGHT\_BLINKER | bits alternando no ritmo do pisca, um lado por vez |
| 0x324 | ENGINE\_STATUS | 10 Hz | 8 | COOLANT\_TEMP, INTAKE\_TEMP, TRIP\_FUEL\_CONSUMED | aquecimento 23 → 65 °C; calor acumulado na admissão após desligar |

## Decodificação dos sinais confirmados

Todos os campos são big-endian (Motorola) e sem sinal. Os bytes são contados a partir de 0; o último byte de cada frame é contador + checksum.

| ID | Sinal | Posição | Fórmula | Valores observados | Por que está confirmado |
| --- | --- | --- | --- | --- | --- |
| 0x17C | ENGINE\_RPM | bytes 2–3 | rpm = bruto | 0 desligado; \~32–256 na partida; \~1.300 fria → \~770 quente; máx. 2.442 | igual a 0x1DC; queda da marcha lenta acompanha o aquecimento |
| 0x1DC | ENGINE\_RPM | bytes 1–2 | rpm = bruto | mesmos valores de 0x17C | duas ECUs/IDs com o mesmo número |
| 0x17C | PEDAL\_GAS | byte 0 | bruto (0–255) | 0 a 0x7F (127) nas aceleradas | mesmo valor de 0x13C byte 4 |
| 0x13C | CAR\_GAS | byte 4 | bruto (0–255) | 0 a 0x79 (121) | eco de PEDAL\_GAS |
| 0x17C | BRAKE\_PRESSED | byte 6, bit 5 (máscara 0x20) | 1 = freio pisado | alterna em cada pisada do teste de `can-sample-1` | coincide com a sequência de freio do teste |
| 0x324 | COOLANT\_TEMP | byte 0 | °C = bruto − 40 | 23 → 65 °C em marcha lenta; ao vivo 36 → 58 °C | curva de aquecimento e resfriamento lento após desligar |
| 0x324 | INTAKE\_TEMP | byte 1 | °C = bruto − 40 | \~26 °C rodando; sobe 33 → 41 °C com o motor desligado | calor acumulado na admissão depois de desligar |
| 0x324 | TRIP\_FUEL\_CONSUMED | bytes 2–3 | bruto, unidade desconhecida | cresce sem parar com o motor ligado; zera ao desligar (máx. 1.943) | monotônico por ciclo de motor |
| 0x294 | LEFT\_BLINKER | byte 0, bit 5 (máscara 0x20) | 1 = pisca aceso | alterna no ritmo do pisca | lado esquerdo segundo o opendbc |
| 0x294 | RIGHT\_BLINKER | byte 0, bit 6 (máscara 0x40) | 1 = pisca aceso | alterna no ritmo do pisca | lado direito segundo o opendbc |
| 0x255 | SET\_TO\_X55, SET\_TO\_X55\_2 | bytes 4 e 5 | constante | sempre 0x55 | fixo em todas as capturas |
| 0x255 | LONG\_COUNTER | byte 6 | contador 0–255 | percorre 00..FF e rola | sequência contínua |

Nos piscas, os logs confirmam que os dois bits alternam, um de cada vez. Qual deles é o esquerdo vem do opendbc e ainda não foi checado acionando um lado de propósito.

## Protocolo Honda: checksum e contador

O esquema da Honda bate em 100% dos frames de 34 dos 35 IDs, nas três capturas. Ele ocupa o último byte de cada frame:

- **bits 3:0**: checksum de 4 bits;
- **bits 5:4**: contador de 2 bits, que vai de 0 a 3 e rola.

O checksum soma todos os nibbles do ID e dos dados, menos o próprio nibble do checksum:

```math
\text{checksum} = \left(8 - \sum \text{nibbles(ID, dados sem o nibble do checksum)}\right) \bmod 16
```

Exemplo: o frame 0x17C `00 00 02 CA 00 00 00 1B` tem checksum 0xB e contador 1.

Exceções:

- **0x255** não usa esse checksum; só 6 de 28.595 frames batem, por acaso.
- **0x1D0** tem checksum, mas os bits 5:4 ficam sempre em 0 com o carro parado. No layout do opendbc eles são o fim de WHEEL\_SPEED\_RR, não um contador.

Os saltos de contador só aparecem nos ciclos de ignição: 40 em `can-sample-0` e 11 em `can-sample-1`, em quase todos os IDs ao mesmo tempo. São os módulos reiniciando, não perda de captura. A captura ao vivo de 10 s teve zero saltos.

## Frequências medidas

Os 6 IDs confirmados chegam de 100 Hz (RPM, pedal) a 10 Hz (temperaturas). São 35 IDs a ~981 frames/s no total; os 4 IDs de 100 Hz somam 41% do tráfego.

| Frequência | IDs (confirmados em **negrito**) | Frames/s |
| --- | --- | --- |
| 100 Hz | **0x13C**, 0x158, **0x17C**, 0x18E | 400 |
| 50 Hz | 0x1A4, 0x1A6, 0x1B0, 0x1D0, **0x1DC**, 0x1ED | 300 |
| 25 Hz | 0x039, 0x21E, 0x221, **0x255**, **0x294**, 0x295 | 150 |
| 10 Hz | 0x305, 0x309, 0x320, **0x324**, 0x328, 0x372, 0x374, 0x377, 0x378, 0x386 | 100 |
| 5 Hz | 0x3D7 | 5 |
| 3,3 Hz | 0x405, 0x40C, 0x428, 0x42D, 0x42E, 0x454, 0x465, 0x6C1 | 26 |

Fonte: `can-live-20261007.log`, `candump -L` na can0, 10 s em 07/10/2026, motor em marcha lenta.

As taxas são fixas por ID: em 10 s, cada ID mandou exatamente 10 vezes a sua frequência, com uma diferença de no máximo um frame. Os logs `can-sample-*` não têm timestamp; nelas, as proporções entre IDs batem com essas taxas.

## Ainda não confirmado

A maior lacuna é a velocidade: todas as capturas foram com o carro parado, então nenhum sinal de velocidade pôde ser verificado.

| ID | Sinais | Confiança | O que se viu | O que confirmaria |
| --- | --- | --- | --- | --- |
| 0x158 | XMISSION\_SPEED, XMISSION\_SPEED2 | opendbc | sempre 0 | captura andando |
| 0x309 | CAR\_SPEED | opendbc | sempre 0 | captura andando |
| 0x1D0 | WHEEL\_SPEED\_FL/FR/RL/RR | opendbc | sempre 0 | captura andando; curva para ver as rodas divergirem |
| 0x255 | WHEEL\_SPEED\_FL/FR/RL/RR | opendbc | só 0x00 ou 0xFA | captura andando |
| 0x1B0 | WHEELS\_MOVING, BRAKE\_ERROR\_1/2 | opendbc | sempre 0 | captura andando |
| 0x1A4 | USER\_BRAKE, COMPUTER\_BRAKING | opendbc | constantes | frenagem andando |
| 0x1A6 | LIGHTS\_SETTING, CRUISE\_BUTTONS, CRUISE\_SETTING, MAIN\_ON | opendbc | LIGHTS\_SETTING vai de 0 a 3 junto com o teste de farol | girar a alavanca de farol posição por posição |
| 0x17C | GAS\_PRESSED, BRAKE\_SWITCH | opendbc | alternam junto com pedal e freio | registrar cada pisada com horário |
| 0x405 | DOOR\_OPEN\_FL/FR/RL/RR, TRUNK\_OPEN | opendbc | FR e RL alternam; FL fica em 1 o tempo todo | abrir e fechar uma porta de cada vez |
| 0x305 | SEATBELT\_DRIVER\_LAMP, SEATBELT\_DRIVER\_LATCHED | opendbc | constante 0x80 0x14 | afivelar e soltar o cinto |
| 0x13C | ENGINE\_TORQUE\_ESTIMATE, ENGINE\_TORQUE\_REQUEST | hipótese | −105 desligado; \~170–440 em marcha lenta | carga conhecida (A/C, direção) ou escala de referência |
| 0x1A4 | FLAGS\_B3 | hipótese | byte 3 alterna entre 0x00 e 0xC0 poucas vezes | correlacionar com eventos da cabine |
| 0x324 | ENGINE\_OFF\_FLAGS | hipótese | 0x0E com motor desligado, 0 ligado | mais ciclos de ignição |

Sem nenhuma definição ainda (21 IDs): 0x039, 0x18E, 0x1ED, 0x21E, 0x221, 0x295, 0x320, 0x328, 0x372, 0x374, 0x377, 0x378, 0x386, 0x3D7, 0x40C, 0x428, 0x42D, 0x42E, 0x454, 0x465 e 0x6C1. No 0x374, o byte 4 vai a 0x57 e o byte 0 a 0x40 nos ciclos de ignição.

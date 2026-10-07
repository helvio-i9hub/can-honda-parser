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
- `can_honda/cli.py` — stats / decode / csv / bytes / summary / signals / live

## Dados reais
- `~/claude/can-sample-0.log` e `can-sample-1.log` — candump padrão, SEM timestamp, carro parado
- `~/can.log` é prefixo idêntico de can-sample-0.log; `~/savvy-honda-obd` é CSV do SavvyCAN (com ts µs)
- scripts antigos de referência (não confiáveis): `~/Downloads/civic_can.py`, `python-can-decode-v1.py`

## Regras
- Todo sinal novo no DBC leva tag de confiança no `CM_ SG_`: `[CONFIRMADO]` só com evidência nos logs
  (dizer qual), `[OPENDBC]` quando vem do layout opendbc, `[HIPOTESE]` caso contrário.
  O teste `test_default_dbc_loads_comments_and_choices` exige a tag.
- Fixtures de teste = trechos reais (`sed -n 'A,Bp'`), nunca dados sintéticos para sinais.
- IDs sem definição ainda: 039 18E 1ED 21E 221 295 320 328 372 374 377 378 386 3D7 40C 428 42D 42E 454 465 6C1.
  Use `bytes --id` e `decode --unknown --id` para investigar.

#!/usr/bin/env bash
# Grava a can0 em formato candump -L (com timestamp) para analisar depois.
#
#   tools/capture-can.sh [iface] [segundos_max] [pasta]
#   tools/capture-can.sh can0 3600 ~/claude
#
# Espera a interface aparecer e ficar UP (o adaptador pode ser ligado
# depois); se ela cair no meio, fecha o arquivo e abre outro quando voltar.
# Para antes do tempo: Ctrl-C (ou kill no PID impresso).
# Só escuta — nunca transmite no barramento.

IFACE=${1:-can0}
MAX=${2:-3600}
DIR=${3:-$HOME/claude}
END=$(( $(date +%s) + MAX ))
echo "capture-can: pid $$, iface $IFACE, até $(date -d @$END +%H:%M:%S), pasta $DIR"

trap 'echo "capture-can: interrompido"; kill $CPID 2>/dev/null; exit 0' INT TERM

waiting=0
while [ "$(date +%s)" -lt "$END" ]; do
    flags=$(ip -br link show "$IFACE" 2>/dev/null)
    if [ -z "$flags" ] || ! grep -q "UP" <<<"$flags"; then
        if [ $waiting -eq 0 ]; then
            echo "capture-can: $(date +%H:%M:%S) esperando $IFACE ${flags:+(está DOWN: sudo ip link set $IFACE up type can bitrate 500000)}"
            waiting=1
        fi
        sleep 1
        continue
    fi
    waiting=0
    out="$DIR/can-drive-$(date +%Y%m%d-%H%M%S).log"
    echo "capture-can: $(date +%H:%M:%S) gravando $out"
    left=$(( END - $(date +%s) ))
    timeout "$left" candump -L "$IFACE" > "$out" &
    CPID=$!
    wait $CPID
    echo "capture-can: $(date +%H:%M:%S) segmento fechado: $(wc -l < "$out") frames"
    [ -s "$out" ] || rm -f "$out"
    sleep 1
done
echo "capture-can: tempo máximo atingido"

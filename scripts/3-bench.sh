#!/usr/bin/env bash
# =============================================================================
# 3-bench.sh — coleta.
#
# Regras de método embutidas (todas descritas no Cap. 4):
#   1. um container por vez, pinado no core definido em bench.env;
#   2. limites de cgroup verificados a cada execução — não só declarados;
#   3. ordem dos arms sorteada por repetição, controlando deriva térmica;
#   4. warm-up descartado + histograma de Event Loop zerado antes da janela;
#   5. container recriado entre repetições, sem heap residual;
#   6. utilização do enlace medida, não estimada.
#
# Exemplos:
#   bash scripts/3-bench.sh                                       matriz principal
#   IO_DELAYS="5 50" CONCURRENCIES=100 bash scripts/3-bench.sh    fator de I/O
#   REPS=1 DURATION=20 CONCURRENCIES=100 ENDPOINTS=get bash scripts/3-bench.sh   piloto
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
[ -f bench.env ] && . ./bench.env || { echo "rode scripts/1-setup.sh primeiro"; exit 1; }

REPS="${REPS:-5}"
DURATION="${DURATION:-60}"
WARMUP="${WARMUP:-10}"
COOLDOWN="${COOLDOWN:-30}"
CONCURRENCIES="${CONCURRENCIES:-10 100 500}"
ENDPOINTS="${ENDPOINTS:-get post}"
IO_DELAYS="${IO_DELAYS:-0}"      # ms de latência de dependência simulada
ITEMS="${ITEMS:-50}"
WORKERS="${WORKERS:-4}"          # autocannon é single-thread por padrão
CPU_LIMIT="${CPU_LIMIT:-1.0}"
MEM_LIMIT="${MEM_LIMIT:-512m}"
PORT=3000
TARGET_HOST="${TARGET_HOST:-127.0.0.1}"
NETWORK_MODE="${NETWORK_MODE:-bridge}"
BASE="http://$TARGET_HOST:$PORT"

ARMS=(
  "express-plain|bench-express|VALIDATION=off"
  "express-zod|bench-express|VALIDATION=zod"
  "fastify-plain|bench-fastify|SCHEMA=off"
  "fastify-schema|bench-fastify|SCHEMA=on"
)
# Arm de controle: mesmo Ajv dos dois lados. Separa "vantagem do framework"
# de "vantagem da biblioteca de validação".
[ "${WITH_AJV_ARM:-0}" = "1" ] && ARMS+=("express-ajv|bench-express|VALIDATION=ajv")

if command -v taskset >/dev/null 2>&1 && [ -n "${LOAD_CPUS:-}" ]; then
  PIN=(taskset -c "$LOAD_CPUS")
else
  PIN=(); echo "AVISO: gerador sem pinagem de core" >&2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="results/run-$STAMP"
mkdir -p "$OUT/raw" "$OUT/stats"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }

{
  echo "data          : $(date -Is)"
  echo "kernel        : $(uname -a)"
  echo "cpu           : $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//')"
  echo "ram           : $(awk '/MemTotal/{printf "%.1f GB", $2/1048576}' /proc/meminfo)"
  echo "so            : $( (. /etc/os-release && echo "$PRETTY_NAME") 2>/dev/null )"
  echo "docker        : $(docker --version)"
  echo "cgroup        : $(stat -fc %T /sys/fs/cgroup)"
  echo "server_cpu    : $SERVER_CPU   load_cpus: ${LOAD_CPUS:-nenhum}"
  echo "l3 do alvo    : $(cat /sys/devices/system/cpu/cpu$SERVER_CPU/cache/index3/shared_cpu_list 2>/dev/null)"
  echo "irmão smt     : $(cat /sys/devices/system/cpu/cpu$SERVER_CPU/topology/thread_siblings_list 2>/dev/null)"
  echo "governor      : $(cat /sys/devices/system/cpu/cpu$SERVER_CPU/cpufreq/scaling_governor 2>/dev/null)"
  echo "modo          : target=$TARGET_HOST network=$NETWORK_MODE items=$ITEMS workers=$WORKERS"
  echo "matriz        : arms=${#ARMS[@]} endpoints=[$ENDPOINTS] io=[$IO_DELAYS] conc=[$CONCURRENCIES] reps=$REPS"
  echo "--- topologia ---"; lscpu -e 2>/dev/null || true
} > "$OUT/environment.txt"

start_container() {
  local image="$1" envs="$2"
  local e=(); for kv in $envs; do e+=(-e "$kv"); done
  local net=(-p "$PORT:3000")
  # --network host tira docker-proxy e DNAT do caminho; cgroup continua valendo.
  [ "$NETWORK_MODE" = "host" ] && net=(--network host -e "PORT=$PORT")
  docker rm -f bench-target >/dev/null 2>&1 || true
  docker run -d --name bench-target \
    --cpus="$CPU_LIMIT" --cpuset-cpus="$SERVER_CPU" \
    --memory="$MEM_LIMIT" --memory-swap="$MEM_LIMIT" \
    "${net[@]}" -e "ITEMS=$ITEMS" "${e[@]}" "$image" >/dev/null

  local n m
  n=$(docker inspect -f '{{.HostConfig.NanoCpus}}' bench-target)
  m=$(docker inspect -f '{{.HostConfig.Memory}}' bench-target)
  [ "$n" = "0" ] || [ "$m" = "0" ] && { echo "ERRO: cgroup não aplicado" >&2; exit 1; }

  for _ in $(seq 1 60); do
    curl -fsS --max-time 1 "$BASE/internal/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "ERRO: health check falhou" >&2; docker logs bench-target >&2; exit 1
}

# NÃO use awk aqui: o awk padrão do Debian/Ubuntu é o mawk, que bufferiza a
# ENTRADA em blocos. Como `docker stats` emite ~1 linha por segundo, o mawk só
# processaria ao encher o buffer — e o CSV de CPU/RAM sairia vazio a coleta
# inteira, sem erro nenhum. O `read` do bash é linha a linha.
#
# Também NÃO agrupe a pipeline em { ... } &: isso faz $! apontar para o
# subshell, e matar o subshell deixa o `docker stats` órfão. Numa coleta
# completa seriam ~240 processos acumulando conexão com o daemon.
STATS_PID=""
sample_stats() {
  local out="$1"
  echo "ts,cpu_percent,mem_usage_mb,mem_limit_mb" > "$out"
  docker stats --format '{{.CPUPerc}};{{.MemUsage}}' bench-target 2>/dev/null \
  | while IFS=';' read -r cpu mem; do
      [ -z "$cpu" ] && continue
      printf '%s,%s,%s,%s\n' "$(date +%s)" "${cpu%\%}" "${mem%% /*}" "${mem##*/ }"
    done >> "$out" &
  STATS_PID=$!
}
stop_stats() {
  if [ -n "$STATS_PID" ]; then
    # Mata o grupo de processos inteiro: leva o `docker stats` junto.
    kill -- -"$STATS_PID" 2>/dev/null || kill "$STATS_PID" 2>/dev/null || true
    wait "$STATS_PID" 2>/dev/null || true
  fi
  STATS_PID=""
}

# Coleta longa: Ctrl-C não pode deixar container nem sampler para trás.
trap 'stop_stats; docker rm -f bench-target >/dev/null 2>&1 || true' EXIT INT TERM

link_start() {
  LIF=$(ip route get "$TARGET_HOST" 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
  LSPD=$(cat "/sys/class/net/$LIF/speed" 2>/dev/null || echo 0)
  LRX=$(cat "/sys/class/net/$LIF/statistics/rx_bytes" 2>/dev/null || echo 0)
  LTX=$(cat "/sys/class/net/$LIF/statistics/tx_bytes" 2>/dev/null || echo 0)
}
link_end() {  # guarda contra "os dois frameworks empataram no cabo"
  local rx tx bits pct=0
  rx=$(cat "/sys/class/net/$LIF/statistics/rx_bytes" 2>/dev/null || echo 0)
  tx=$(cat "/sys/class/net/$LIF/statistics/tx_bytes" 2>/dev/null || echo 0)
  bits=$(( (rx - LRX + tx - LTX) * 8 ))
  [ "$LSPD" -gt 0 ] && pct=$(( bits / DURATION * 100 / (LSPD * 1000000) ))
  printf '{"iface":"%s","speed_mbps":%s,"util_pct":%s}\n' "$LIF" "$LSPD" "$pct" > "$1"
}

run_case() {
  local arm="$1" image="$2" envs="$3" ep="$4" io="$5" conc="$6" rep="$7"
  local tag="${arm}__${ep}__io${io}__c${conc}__r${rep}"
  log "  $tag"
  start_container "$image" "$envs IO_DELAY_MS=$io"

  local body=()
  [ "$ep" = "post" ] && body=(-m POST -H 'content-type=application/json' -i load/payload.json)

  # 1) warm-up descartado: JIT do V8 e estabilização de heap
  "${PIN[@]}" "$AC" -c "$conc" -d "$WARMUP" -p 1 -w "$WORKERS" \
    "${body[@]}" "$BASE/api/v1/recursos" >/dev/null 2>&1 || true

  # 2) janela de medição começa limpa
  curl -fsS -X POST "$BASE/internal/reset-metrics" >/dev/null
  sample_stats "$OUT/stats/$tag.csv"
  link_start

  "${PIN[@]}" "$AC" -c "$conc" -d "$DURATION" -p 1 -w "$WORKERS" -j \
    "${body[@]}" "$BASE/api/v1/recursos" > "$OUT/raw/$tag.autocannon.json"

  link_end "$OUT/raw/$tag.link.json"
  curl -fsS "$BASE/internal/metrics" > "$OUT/raw/$tag.metrics.json"
  stop_stats

  docker logs bench-target > "$OUT/raw/$tag.container.log" 2>&1 || true
  docker rm -f bench-target >/dev/null 2>&1 || true
  sleep "$COOLDOWN"
}

log "build"
docker build -q -f express-api/Dockerfile -t bench-express . >/dev/null
docker build -q -f fastify-api/Dockerfile -t bench-fastify . >/dev/null

# autocannon local e travado no lockfile. Com `npx` a resolução acontece a cada
# uma das ~240 invocações e pode ir à rede no meio da coleta.
AC="$ROOT/load/node_modules/.bin/autocannon"
if [ ! -x "$AC" ]; then
  log "instalando autocannon (uma vez)"
  ( cd load && npm ci --silent >/dev/null 2>&1 || npm install --silent >/dev/null 2>&1 )
fi
[ -x "$AC" ] || { echo "ERRO: autocannon não instalou. Rode: cd load && npm install" >&2; exit 1; }
log "autocannon $("$AC" --version 2>/dev/null | head -1)"
TOTAL=$(( ${#ARMS[@]} * $(echo $ENDPOINTS | wc -w) * $(echo $IO_DELAYS | wc -w) * $(echo $CONCURRENCIES | wc -w) * REPS ))
log "saída: $OUT | $TOTAL execuções | ~$(( TOTAL * (WARMUP + DURATION + COOLDOWN) / 60 )) min"

for rep in $(seq 1 "$REPS"); do
  log "== repetição $rep/$REPS =="
  mapfile -t SH < <(printf '%s\n' "${ARMS[@]}" | shuf)   # ordem sorteada
  for entry in "${SH[@]}"; do
    IFS='|' read -r arm image envs <<< "$entry"
    for ep in $ENDPOINTS; do
      for io in $IO_DELAYS; do
        for conc in $CONCURRENCIES; do
          run_case "$arm" "$image" "$envs" "$ep" "$io" "$conc" "$rep"
        done
      done
    done
  done
done

log "concluído. rode: python3 scripts/4-analyze.py $OUT"

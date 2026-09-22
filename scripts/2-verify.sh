#!/usr/bin/env bash
# =============================================================================
# 2-verify.sh — pré-voo. Roda ANTES de gastar horas coletando dado inválido.
#
# Cada checagem aqui existe por causa de uma falha SILENCIOSA: nenhuma delas
# gera erro em tempo de execução, todas geram número errado.
# =============================================================================
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
[ -f bench.env ] && . ./bench.env || { echo "rode scripts/1-setup.sh primeiro"; exit 1; }

ITEMS="${ITEMS:-50}"
TARGET_HOST="${TARGET_HOST:-127.0.0.1}"
PORT=3999
FAIL=0; WARN=0

ok()   { echo "  [ok]   $*"; }
bad()  { echo "  [FALHA] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [aviso] $*"; WARN=$((WARN+1)); }
cleanup() { docker rm -f bench-verify >/dev/null 2>&1 || true; }
trap cleanup EXIT

boot() { # imagem, env...
  local image="$1"; shift
  local args=(); for kv in "$@"; do args+=(-e "$kv"); done
  cleanup
  docker run -d --name bench-verify \
    --cpus=1.0 --cpuset-cpus="$SERVER_CPU" \
    --memory=512m --memory-swap=512m \
    -p "$PORT:3000" -e "ITEMS=$ITEMS" "${args[@]}" "$image" >/dev/null || return 1
  for _ in $(seq 1 40); do
    curl -fsS --max-time 1 "http://$TARGET_HOST:$PORT/internal/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

echo "=============================================================="
echo " 2. PRÉ-VOO   (SERVER_CPU=$SERVER_CPU  LOAD_CPUS=$LOAD_CPUS  ITEMS=$ITEMS)"
echo "=============================================================="

echo
echo "-- build --"
docker build -q -f express-api/Dockerfile -t bench-express . >/dev/null || { bad "build express"; exit 1; }
docker build -q -f fastify-api/Dockerfile -t bench-fastify . >/dev/null || { bad "build fastify"; exit 1; }
ok "imagens construídas"

# --------------------------------------------------------- limites de cgroup
echo
echo "-- limites de recurso realmente aplicados --"
boot bench-express VALIDATION=off || { bad "container não subiu"; exit 1; }
NANO=$(docker inspect -f '{{.HostConfig.NanoCpus}}' bench-verify)
MEM=$(docker inspect -f '{{.HostConfig.Memory}}' bench-verify)
SWAP=$(docker inspect -f '{{.HostConfig.MemorySwap}}' bench-verify)
CSET=$(docker inspect -f '{{.HostConfig.CpusetCpus}}' bench-verify)
[ "$NANO" = "1000000000" ] && ok "CPU limitada a 1.0" || bad "CPU não limitada (NanoCpus=$NANO)"
[ "$MEM"  = "536870912" ]  && ok "RAM limitada a 512 MB" || bad "RAM não limitada (Memory=$MEM)"
[ "$MEM"  = "$SWAP" ]      && ok "swap desabilitado" || bad "swap ativo — mediria disco, não RAM"
[ "$CSET" = "$SERVER_CPU" ] && ok "pinado no core $CSET" || bad "cpuset='$CSET', esperado '$SERVER_CPU'"
HEAP=$(docker exec bench-verify node -e 'console.log(Math.round(require("v8").getHeapStatistics().heap_size_limit/1048576))' 2>/dev/null || echo 9999)
[ "$HEAP" -le 400 ] && ok "heap do V8 em ${HEAP} MB, abaixo do cgroup" \
                    || bad "heap do V8 em ${HEAP} MB — OOM antes de saturar"
cleanup

# ------------------------------------------------- isolamento alvo x gerador
echo
echo "-- isolamento entre alvo e gerador --"
SL3=$(cat "/sys/devices/system/cpu/cpu${SERVER_CPU}/cache/index3/shared_cpu_list" 2>/dev/null)
SIB=$(cat "/sys/devices/system/cpu/cpu${SERVER_CPU}/topology/thread_siblings_list" 2>/dev/null)
if [ -n "$LOAD_CPUS" ] && [ -n "$SL3" ]; then
  INTER=$(python3 -c '
import sys
def e(s):
    o=set()
    for p in s.replace(" ","").split(","):
        if "-" in p:
            a,b=p.split("-"); o|=set(range(int(a),int(b)+1))
        elif p: o.add(int(p))
    return o
print("yes" if e(sys.argv[1]) & e(sys.argv[2]) else "no")' "$SL3" "$LOAD_CPUS")
  [ "$INTER" = "no" ] && ok "gerador fora do domínio de L3 do alvo (L3 [$SL3])" \
                      || bad "gerador dentro do L3 [$SL3] do alvo — contenção de cache"
fi
if [ -n "$SIB" ]; then
  SI=$(python3 -c '
import sys
def e(s):
    o=set()
    for p in s.replace(" ","").split(","):
        if "-" in p:
            a,b=p.split("-"); o|=set(range(int(a),int(b)+1))
        elif p: o.add(int(p))
    return o
a=e(sys.argv[1]); a.discard(int(sys.argv[3]))
print("yes" if a & e(sys.argv[2]) else "no")' "$SIB" "${LOAD_CPUS:-999}" "$SERVER_CPU")
  [ "$SI" = "no" ] && ok "irmão SMT [$SIB] ocioso" \
                   || warn "irmão SMT do alvo está no gerador: rouba unidades de execução"
fi

# ------------------------------------------------- equivalência entre arms
echo
echo "-- equivalência funcional dos arms --"
PAYLOAD=$(cat load/payload.json)
EMPTY_MD5=$(printf '' | md5sum | cut -c1-12)
declare -A GH=() PH=()
arm() {
  local name="$1" image="$2"; shift 2
  boot "$image" "$@" || { bad "$name não subiu"; return; }
  GH[$name]=$(curl -fsS "http://$TARGET_HOST:$PORT/api/v1/recursos" | md5sum | cut -c1-12)
  PH[$name]=$(curl -fsS -X POST -H 'content-type: application/json' -d "$PAYLOAD" \
              "http://$TARGET_HOST:$PORT/api/v1/recursos" | md5sum | cut -c1-12)
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
         -d '{"nome":"x"}' "http://$TARGET_HOST:$PORT/api/v1/recursos")
  echo "         $name  GET=${GH[$name]}  POST=${PH[$name]}  invalido=$code"
  case "$name" in
    *-plain) [ "$code" = "201" ] || warn "$name deveria aceitar payload inválido (sem validação)" ;;
    *)       [ "$code" = "400" ] || bad  "$name NÃO está validando o payload" ;;
  esac
  cleanup
}
arm express-plain  bench-express VALIDATION=off
arm express-zod    bench-express VALIDATION=zod
arm express-ajv    bench-express VALIDATION=ajv
arm fastify-plain  bench-fastify SCHEMA=off
arm fastify-schema bench-fastify SCHEMA=on

# Comparar só "são iguais entre si" não basta: com zero arms medidos o laço
# nunca executa, e com curl falhando em todos os cinco o md5 de resposta VAZIA
# é idêntico nos cinco. Os dois casos passariam como "equivalentes". Por isso
# conta-se quantos arms responderam e rejeita-se o md5 de corpo vazio.
if [ "${#GH[@]}" -lt 5 ]; then
  bad "só ${#GH[@]} de 5 arms responderam — equivalência NÃO verificada"
else
  RG=""; RP=""; DIFF=0
  for k in "${!GH[@]}"; do
    if [ "${GH[$k]}" = "$EMPTY_MD5" ] || [ "${PH[$k]}" = "$EMPTY_MD5" ]; then
      bad "$k devolveu resposta vazia — curl falhou, não é equivalência"
      DIFF=1; continue
    fi
    if [ -z "$RG" ]; then RG="${GH[$k]}"; RP="${PH[$k]}"; continue; fi
    [ "${GH[$k]}" = "$RG" ] || { bad "GET de $k difere dos demais"; DIFF=1; }
    [ "${PH[$k]}" = "$RP" ] || { bad "POST de $k difere dos demais"; DIFF=1; }
  done
  [ "$DIFF" = "0" ] && ok "todos os 5 arms devolvem bytes idênticos — comparação é justa"
fi

# ---------------------------------------------------------------- rede / SO
echo
echo "-- rede e limites do SO --"
if [ "$TARGET_HOST" = "127.0.0.1" ]; then
  ok "loopback: sem teto de banda, ITEMS livre"
  warn "o tráfego não passa por NIC real — declare como ameaça à validade externa"
else
  IF=$(ip route get "$TARGET_HOST" 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
  SP=$(cat "/sys/class/net/$IF/speed" 2>/dev/null || echo 0)
  [ -d "/sys/class/net/$IF/wireless" ] && bad "interface $IF é Wi-Fi" || ok "interface $IF a ${SP} Mbps"
  boot bench-express VALIDATION=off && {
    B=$(curl -fsS -o /dev/null -w '%{size_download}' "http://$TARGET_HOST:$PORT/api/v1/recursos"); cleanup
    [ "$SP" -gt 0 ] && {
      CEIL=$(( SP * 1000000 * 94 / 100 / 8 / (B + 330) ))
      echo "         corpo GET ${B} B -> teto do enlace ≈ ${CEIL} req/s"
      [ "$CEIL" -ge 40000 ] && ok "folga suficiente" \
        || bad "teto de ${CEIL} req/s: os frameworks empatam no cabo. Reduza ITEMS."
    }
  }
fi
NOF=$(ulimit -n)
[ "$NOF" -ge 65535 ] && ok "ulimit -n = $NOF" || bad "ulimit -n = $NOF — rode: ulimit -n 65535"
LOAD1=$(cut -d' ' -f1 /proc/loadavg)
awk -v l="$LOAD1" 'BEGIN{exit !(l<0.5)}' && ok "load average $LOAD1" || bad "load average $LOAD1 — feche tudo"

echo
if [ "$FAIL" -gt 0 ]; then
  echo ">> $FAIL FALHA(S) e $WARN aviso(s). NÃO colete antes de corrigir."
  exit 1
fi
echo ">> pré-voo aprovado ($WARN aviso(s)). Próximo: bash scripts/3-bench.sh"
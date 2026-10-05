#!/usr/bin/env bash
# =============================================================================
# coleta-rotas.sh — isola o fator "numero de rotas registradas".
#
# Requer o patch extra-routes.patch aplicado antes:
#   git apply extra-routes.patch
#   grep -c EXTRA_ROUTES scripts/3-bench.sh     # deve retornar 4
#
# Custo: 3 niveis x 2 arms x 1 endpoint x 1 concorrencia x 5 reps
#        = 30 execucoes x 100s = ~50 minutos de maquina.
#
# Rode DENTRO de tmux:  tmux new -s rotas
# Detache com Ctrl+B depois D. Volte com: tmux a -t rotas
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# --- Verificacao previa: 30 segundos que evitam perder 50 minutos -------------
# Se as rotas de enchimento nao existirem no container, a coleta mede zero e o
# resultado nulo fica indistinguivel de "roteamento nao importa".
echo "=== verificando se EXTRA_ROUTES chega no container ==="
docker build -q -f express-api/Dockerfile -t bench-express . >/dev/null
docker build -q -f fastify-api/Dockerfile -t bench-fastify . >/dev/null

for IMG in bench-express bench-fastify; do
  docker rm -f precheck >/dev/null 2>&1 || true
  docker run -d --rm --name precheck -e EXTRA_ROUTES=200 -p 3100:3000 "$IMG" >/dev/null
  for _ in $(seq 1 30); do
    curl -fsS --max-time 1 http://127.0.0.1:3100/internal/health >/dev/null 2>&1 && break
    sleep 0.5
  done
  F=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/api/v1/filler-199/abc)
  R=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/api/v1/recursos)
  docker rm -f precheck >/dev/null 2>&1 || true
  echo "  $IMG -> filler-199: $F   recursos: $R"
  if [ "$F" != "204" ]; then
    echo "ABORTADO: filler-199 devolveu $F em vez de 204." >&2
    echo "A rota 199 nao foi registrada. O patch nao esta aplicado," >&2
    echo "ou a imagem esta desatualizada. Nao gaste os 50 minutos." >&2
    exit 1
  fi
done
echo "=== verificacao OK: as 200 rotas existem nos dois frameworks ==="
echo

# --- Coleta -------------------------------------------------------------------
# ITEMS=50 ancora nos dados que voce ja tem.
# Nivel 0 roda de novo de proposito: mesma sessao termica, mesmo kernel,
# mesma hora do dia. Comparar entre dias introduz exatamente o tipo de
# variavel que o resto do trabalho controla.
for R in 0 50 200; do
  echo ">>> nivel EXTRA_ROUTES=$R"
  EXTRA_ROUTES="$R" \
  ONLY_ARMS="express-plain fastify-plain" \
  ENDPOINTS=get \
  CONCURRENCIES=100 \
  ITEMS=50 \
    bash scripts/3-bench.sh
done

echo
echo "=== concluido ==="
echo "Os diretorios se autoidentificam: o environment.txt de cada um grava"
echo "routes=N. Confira com:"
echo "  grep -H 'modo' results/run-*/environment.txt | tail -3"

#!/usr/bin/env bash
# =============================================================================
# coleta-ajv.sh — isola biblioteca de validacao de framework.
#
# Sem este bloco, a comparacao fastify-schema vs express-zod confunde dois
# efeitos: o framework e a biblioteca (Zod de um lado, Ajv do outro). O arm
# express-ajv usa o MESMO Ajv 8.20.0 do Fastify, mantendo a biblioteca
# constante.
#
# So POST: no GET nao ha corpo para validar, e o efeito nulo ali ja esta
# medido (p = 0,5476) e serve de controle.
#
# 5 arms x POST x c=100 x 5 repeticoes = 25 execucoes, ~42 min.
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== verificando se o arm express-ajv valida de fato ==="
docker build -q -f express-api/Dockerfile -t bench-express . >/dev/null

docker rm -f precheck >/dev/null 2>&1 || true
docker run -d --rm --name precheck -e VALIDATION=ajv -e ITEMS=50 \
  -p 3100:3000 bench-express >/dev/null
for _ in $(seq 1 30); do
  curl -fsS --max-time 1 http://127.0.0.1:3100/internal/health >/dev/null 2>&1 && break
  sleep 0.5
done

OK=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
     -H 'content-type: application/json' --data @load/payload.json \
     http://127.0.0.1:3100/api/v1/recursos)
BAD=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      -H 'content-type: application/json' --data '{"nome":"x"}' \
      http://127.0.0.1:3100/api/v1/recursos)
docker rm -f precheck >/dev/null 2>&1 || true

echo "  payload valido   -> $OK   (esperado 201)"
echo "  payload invalido -> $BAD  (esperado 400)"
if [ "$OK" != "201" ] || [ "$BAD" != "400" ]; then
  echo "ABORTADO: o Ajv nao esta validando no arm express-ajv." >&2
  echo "Se o invalido devolveu 201, a validacao esta desligada e a coleta" >&2
  echo "mediria um arm identico ao express-plain. Nao gaste os 42 minutos." >&2
  exit 1
fi
echo "=== verificacao OK: o Ajv rejeita o invalido e aceita o valido ==="
echo

WITH_AJV_ARM=1 ENDPOINTS=post CONCURRENCIES=100 ITEMS=50 REPS=5 \
  bash scripts/3-bench.sh

echo
echo "=== concluido. analise: ==="
echo "  python3 scripts/5-consolidar.py results/run-<novo-stamp> --out analise-ajv"

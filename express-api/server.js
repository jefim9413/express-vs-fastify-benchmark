'use strict';
const express = require('express');
const { performance, monitorEventLoopDelay } = require('node:perf_hooks');
const { bodySchema, buildDataset, buildCreated } = require('../common/contract');

const PORT = Number(process.env.PORT || 3000);
const ITEMS = Number(process.env.ITEMS || 50);
const VALIDATION = process.env.VALIDATION || 'off';   // off | ajv | zod
const EXTRA_ROUTES = Number(process.env.EXTRA_ROUTES || 0);
const EXTRA_MIDDLEWARES = Number(process.env.EXTRA_MIDDLEWARES || 0);
const IO_DELAY_MS = Number(process.env.IO_DELAY_MS || 0);

const app = express();

const loopDelay = monitorEventLoopDelay({ resolution: 10 });
loopDelay.enable();

for (let i = 0; i < EXTRA_MIDDLEWARES; i++) app.use((req, res, next) => next());

app.use(express.json({ limit: '1mb' }));

let validate = null;
if (VALIDATION === 'ajv') {
  const Ajv = require('ajv');

  // coerceTypes/removeAdditional/allErrors desligados: qualquer um deles muda o
  // trabalho que o validador faz, e o Zod não tem equivalente exato. Manter os
  // três em false é o que torna a comparação com o Fastify honesta.
  const ajv = new Ajv({ coerceTypes: false, removeAdditional: false, allErrors: false });
  const compiled = ajv.compile(bodySchema);
  validate = (body) => (compiled(body) ? null : compiled.errors);
} else if (VALIDATION === 'zod') {
  const { z } = require('zod');
  const schema = z.object({
    nome: z.string().min(3).max(120),
    email: z.string().min(6).max(160).regex(/^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$/),
    idade: z.number().int().min(0).max(130),
    ativo: z.boolean(),
    tags: z.array(z.string().min(1).max(32)).min(1).max(10),
    endereco: z.object({
      rua: z.string().min(1).max(120),
      numero: z.number().int().min(0).max(99999),
      cidade: z.string().min(1).max(80),
      uf: z.string().length(2),
      cep: z.string().regex(/^[0-9]{5}-?[0-9]{3}$/)
    }).strict()
  }).strict();
  validate = (body) => {
    const r = schema.safeParse(body);
    return r.success ? null : r.error.issues;
  };
}

for (let i = 0; i < EXTRA_ROUTES; i++) {
  app.get(`/api/v1/filler-${i}/:id`, (req, res) => res.status(204).end());
}

const DATASET = buildDataset(ITEMS);
// Latência de dependência simulada (banco, cache, serviço externo), aplicada
// DEPOIS da validação como numa API real. setTimeout simula ESPERA, não
// trabalho de I/O: não há syscall nem thread do pool. Declarado como limitação
// no Cap. 4.
const afterIO = (fn) => (IO_DELAY_MS > 0 ? () => setTimeout(fn, IO_DELAY_MS) : fn);

app.get('/api/v1/recursos', (req, res) => {
  afterIO(() => res.status(200).json(DATASET))();
});

app.post('/api/v1/recursos', (req, res) => {
  if (validate) {
    const errors = validate(req.body);
    if (errors) return res.status(400).json({ erro: 'payload invalido' });
  }
  afterIO(() => res.status(201).json(buildCreated(req.body)))();
});

app.get('/internal/health', (req, res) => res.status(200).json({ ok: true }));

app.get('/internal/metrics', (req, res) => {
  const mem = process.memoryUsage();
  res.status(200).json({
    framework: 'express',
    validation: VALIDATION,
    uptimeMs: Math.round(performance.now()),
    eventLoopDelay: {
      meanMs: loopDelay.mean / 1e6,
      p50Ms: loopDelay.percentile(50) / 1e6,
      p95Ms: loopDelay.percentile(95) / 1e6,
      p99Ms: loopDelay.percentile(99) / 1e6,
      maxMs: loopDelay.max / 1e6
    },
    memoryMb: {
      rss: mem.rss / 1048576,
      heapUsed: mem.heapUsed / 1048576,
      heapTotal: mem.heapTotal / 1048576,
      external: mem.external / 1048576
    }
  });
});

app.post('/internal/reset-metrics', (req, res) => {
  loopDelay.reset();
  res.status(204).end();
});

const server = app.listen(PORT, '0.0.0.0', () => {
  console.log(JSON.stringify({
    framework: 'express',
    version: require('express/package.json').version,
    node: process.version,
    port: PORT, validation: VALIDATION, items: ITEMS,
    extraRoutes: EXTRA_ROUTES, extraMiddlewares: EXTRA_MIDDLEWARES,
    ioDelayMs: IO_DELAY_MS
  }));
});

server.keepAliveTimeout = 72000;
server.headersTimeout = 73000;

process.on('SIGTERM', () => server.close(() => process.exit(0)));
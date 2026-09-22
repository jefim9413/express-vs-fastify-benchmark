'use strict';

const Fastify = require('fastify');
const { performance, monitorEventLoopDelay } = require('node:perf_hooks');
const {
  bodySchema, listResponseSchema, createdResponseSchema,
  buildDataset, buildCreated
} = require('../common/contract');

const PORT = Number(process.env.PORT || 3000);
const ITEMS = Number(process.env.ITEMS || 50);
const SCHEMA = process.env.SCHEMA || 'off';               // off | on
const EXTRA_ROUTES = Number(process.env.EXTRA_ROUTES || 0);
const EXTRA_HOOKS = Number(process.env.EXTRA_HOOKS || 0);
const IO_DELAY_MS = Number(process.env.IO_DELAY_MS || 0);

// bodyLimit explícito e igual ao `express.json({ limit: '1mb' })` do outro arm.
// Hoje coincide com o default do Fastify, mas deixar implícito significaria que
// uma mudança de default criaria assimetria de configuração sem aviso nenhum.
const fastify = Fastify({ logger: false, bodyLimit: 1048576 });
const loopDelay = monitorEventLoopDelay({ resolution: 10 });
loopDelay.enable();

for (let i = 0; i < EXTRA_HOOKS; i++) {
  fastify.addHook('onRequest', (req, reply, done) => done());
}

for (let i = 0; i < EXTRA_ROUTES; i++) {
  fastify.get(`/api/v1/filler-${i}/:id`, (req, reply) => reply.status(204).send());
}

const DATASET = buildDataset(ITEMS);
const getOpts = SCHEMA === 'on'
  ? { schema: { response: { 200: listResponseSchema } } }
  : {};

const postOpts = SCHEMA === 'on'
  ? { schema: { body: bodySchema, response: { 201: createdResponseSchema } } }
  : {};

// Latência de dependência simulada (banco, cache, serviço externo). Aplicada
// DEPOIS da validação, como numa API real: valida, depois consulta.
// LIMITAÇÃO: setTimeout simula ESPERA, não trabalho de I/O — não há syscall,
// thread do pool nem parsing de resposta de banco. Declarado no Cap. 4.
// É este fator que responde "a partir de quando a escolha deixa de importar".
const afterIO = (fn) => (IO_DELAY_MS > 0 ? () => setTimeout(fn, IO_DELAY_MS) : fn);

fastify.get('/api/v1/recursos', getOpts, (req, reply) => {
  afterIO(() => reply.status(200).send(DATASET))();
});

fastify.post('/api/v1/recursos', postOpts, (req, reply) => {
  afterIO(() => reply.status(201).send(buildCreated(req.body)))();
});

fastify.get('/internal/health', (req, reply) => reply.status(200).send({ ok: true }));

fastify.get('/internal/metrics', (req, reply) => {
  const mem = process.memoryUsage();
  reply.status(200).send({
    framework: 'fastify',
    validation: SCHEMA === 'on' ? 'ajv-jit' : 'off',
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

fastify.post('/internal/reset-metrics', (req, reply) => {
  loopDelay.reset();
  reply.status(204).send();
});

fastify.listen({ port: PORT, host: '0.0.0.0' }, (err, address) => {
  if (err) { console.error(err); process.exit(1); }
  console.log(JSON.stringify({
    framework: 'fastify',
    version: require('fastify/package.json').version,
    node: process.version,
    address, schema: SCHEMA, items: ITEMS,
    extraRoutes: EXTRA_ROUTES, extraHooks: EXTRA_HOOKS,
    ioDelayMs: IO_DELAY_MS
  }));
});

process.on('SIGTERM', () => fastify.close().then(() => process.exit(0)));
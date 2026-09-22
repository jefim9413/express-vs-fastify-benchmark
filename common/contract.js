'use strict';

/**
 * Contrato ÚNICO compartilhado pelas duas APIs.
 *
 * Este arquivo existe para eliminar a principal variável de confusão do
 * experimento: qualquer divergência de payload, de schema ou de tamanho de
 * resposta entre os frameworks invalidaria a comparação. Ambas as imagens
 * Docker copiam ESTE mesmo arquivo.
 */

// JSON Schema (draft-07 compatível com Ajv 8 e com o compilador do Fastify).
// Nenhum `format` é usado de propósito: `format` exige ajv-formats, que não
// vem embutido no Fastify 5. Usar `pattern` mantém as duas pontas idênticas.
const bodySchema = {
  type: 'object',
  required: ['nome', 'email', 'idade', 'ativo', 'tags', 'endereco'],
  additionalProperties: false,
  properties: {
    nome: { type: 'string', minLength: 3, maxLength: 120 },
    email: { type: 'string', minLength: 6, maxLength: 160, pattern: '^[^@\\s]+@[^@\\s]+\\.[a-zA-Z]{2,}$' },
    idade: { type: 'integer', minimum: 0, maximum: 130 },
    ativo: { type: 'boolean' },
    tags: { type: 'array', minItems: 1, maxItems: 10, items: { type: 'string', minLength: 1, maxLength: 32 } },
    endereco: {
      type: 'object',
      required: ['rua', 'numero', 'cidade', 'uf', 'cep'],
      additionalProperties: false,
      properties: {
        rua: { type: 'string', minLength: 1, maxLength: 120 },
        numero: { type: 'integer', minimum: 0, maximum: 99999 },
        cidade: { type: 'string', minLength: 1, maxLength: 80 },
        uf: { type: 'string', minLength: 2, maxLength: 2 },
        cep: { type: 'string', pattern: '^[0-9]{5}-?[0-9]{3}$' }
      }
    }
  }
};

const recursoSchema = {
  type: 'object',
  properties: {
    id: { type: 'integer' },
    nome: { type: 'string' },
    email: { type: 'string' },
    idade: { type: 'integer' },
    ativo: { type: 'boolean' },
    tags: { type: 'array', items: { type: 'string' } },
    endereco: {
      type: 'object',
      properties: {
        rua: { type: 'string' },
        numero: { type: 'integer' },
        cidade: { type: 'string' },
        uf: { type: 'string' },
        cep: { type: 'string' }
      }
    }
  }
};

const listResponseSchema = {
  type: 'object',
  properties: {
    total: { type: 'integer' },
    dados: { type: 'array', items: recursoSchema }
  }
};

const createdResponseSchema = {
  type: 'object',
  properties: {
    id: { type: 'integer' },
    recebido: { type: 'boolean' },
    recurso: recursoSchema
  }
};

// Dataset estático. Construído UMA vez no boot: o custo de geração não entra
// na medição, apenas o custo de serialização — que é exatamente o que se quer
// isolar (JSON.stringify reflexivo vs. fast-json-stringify preditivo).
const UFS = ['CE', 'SP', 'RJ', 'MG', 'BA', 'PE', 'RS', 'PR'];

function buildDataset(items) {
  const dados = [];
  for (let i = 0; i < items; i++) {
    dados.push({
      id: i + 1,
      nome: `Recurso de Teste Numero ${i + 1}`,
      email: `usuario${i + 1}@benchmark.local`,
      idade: 18 + (i % 60),
      ativo: i % 2 === 0,
      tags: ['benchmark', 'node', `grupo-${i % 5}`],
      endereco: {
        rua: `Rua Projetada ${i % 200}`,
        numero: 100 + (i % 900),
        cidade: 'Quixada',
        uf: UFS[i % UFS.length],
        cep: '63900-000'
      }
    });
  }
  return { total: dados.length, dados };
}

// A ordem das chaves aqui precisa espelhar a de `recursoSchema`: o Fastify com
// schema serializa na ordem declarada, o Express na ordem de inserção. Se as
// duas divergirem, as respostas terão o mesmo conteúdo e bytes diferentes —
// e a comparação de serialização perde o sentido. O 2-verify.sh confere isso
// comparando o MD5 dos cinco arms.
function buildCreated(body) {
  return { id: 1, recebido: true, recurso: { id: 1, ...body } };
}

module.exports = {
  bodySchema,
  listResponseSchema,
  createdResponseSchema,
  buildDataset,
  buildCreated
};
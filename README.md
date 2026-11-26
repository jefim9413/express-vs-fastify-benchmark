# Benchmark Express.js x Fastify — artefato experimental do TCC 2

**Autor:** Jefferson de Souza Marques · Universidade Federal do Ceará, Campus Quixadá
**Orientador:** Prof. Dr. Antônio Joel Ramiro de Castro
**Licença:** código sob MIT (`LICENSE`); dados sob CC BY 4.0 (`LICENSE-DATA`)

Experimento controlado comparando Express.js e Fastify em APIs REST, com isolamento
por cgroups, pinagem de núcleo e tratamento estatístico não-paramétrico.

**Coleta em três fases:** 495 execuções em oito blocos, 819.998.831 requisições.
A fase principal (23 e 24/09/2026) cobriu 440 execuções em 88 células com n=5, CV
mediano de 0,81% e nenhuma resposta fora da faixa de sucesso. Em 05/10/2026 foram
acrescentados dois blocos de controle: 30 execuções isolando o fator `EXTRA_ROUTES`
e 25 isolando a biblioteca de validação.

Stack: Node 22.22.2 · Express 5.2.1 · Fastify 5.12.1 · Ajv 8.20.0 · Zod 3.25.76 ·
Docker Engine 29.8.1 · Ubuntu 24.04.5 LTS · AMD Ryzen 9 9950X.

---

## Principais achados

**A vantagem do Fastify não é uma constante do par de frameworks.** Na comparação
direta entre os arranjos sem schema, o Fastify superou o Express em **20 de 22
condições**; as duas exceções ocorreram com 50 ms de latência de dependência, onde
a diferença (−0,3%) não se distingue de ruído. No cenário realista de mercado, com
ambos validando, a vantagem se sustentou em **17 de 22** e as cinco exceções são
informativas, não acidentais.

| Achado                                                                    | Evidência                                                              |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| A vantagem decresce com o tamanho da resposta                             | +406,8% em 0,43 KB → +62,8% em 49,1 KB                                 |
| A serialização preditiva reduz o desempenho                             | penalidade de 5,2% (0,43 KB) a 42,8% (49,1 KB)                          |
| A validação custa o mesmo nos dois frameworks                           | com Ajv 8.20.0 dos dois lados: 1,69 µs no Fastify, 1,74 µs no Express |
| A biblioteca de validação importa mais que se supõe                    | Zod 4,53 µs contra 1,74 µs do Ajv, no mesmo framework                 |
| Acima de ~50 ms de I/O a vazão converge                                  | ganho cai para −0,3% (p = 0,42 no GET)                                 |
| Mas o consumo de CPU não converge                                        | 18,0% no Express contra 8,9% no Fastify                                 |
| O roteamento não explica a diferença de base, mas a amplia com a escala | 0,0688 µs por rota no Express contra 0,0007 µs no Fastify             |

**Caso de inversão:** com resposta de 49,1 KB, o arm `fastify-schema` ficou **mais
lento** que o `express-plain` nas três concorrências testadas (−8,5% em c=10, −6,5%
em c=100, −3,8% em c=500; p = 0,0079 em todas). Habilitar `response schema` em
endpoints com payload grande reverte a vantagem do framework, e a reversão não
depende do nível de carga.

**Síntese mecanicista:** medido em microssegundos de CPU por requisição, o overhead
do Express é um custo aproximadamente fixo. Ajustando o custo contra o número de
itens na resposta, o intercepto do Express fica em 43,3 µs no GET e 56,7 µs no POST,
contra 8,3 µs e 19,1 µs do Fastify, uma diferença de 35 a 38 µs, consistente entre
endpoints e independente do tamanho da resposta. Tudo o que o experimento variou
acrescenta trabalho comum aos dois frameworks e dilui esse custo fixo
proporcionalmente. É o que explica os três eixos com um princípio só.

| Cenário (c=100) |   Express |  Fastify | Diferença absoluta | Diferença relativa |
| ---------------- | --------: | -------: | ------------------: | ------------------: |
| GET 0,43 KB      |  44,9 µs |  9,1 µs |            35,9 µs |               +407% |
| GET 12,3 KB      |  67,4 µs | 28,1 µs |            39,3 µs |               +142% |
| POST 0,46 KB     |  56,9 µs | 19,1 µs |            37,8 µs |               +200% |
| GET 49,1 KB      | 145,5 µs | 89,6 µs |            55,9 µs |                +63% |

O custo marginal por item serializado separa os dois caminhos de serialização:
0,406 µs/item no `JSON.stringify` nativo contra 0,740 µs/item no
`fast-json-stringify` 82% a mais. No POST, cuja resposta tem tamanho fixo, a
inclinação é 0,000 µs/item nos dois, o que confirma que a decomposição mede o que
pretende medir.

---

## Dados coletados

Oito blocos, todos no mesmo ambiente verificado (kernel 7.0.0-31-generic,
Docker 29.8.1, governor `performance`, núcleo 1 do CCD `0-7,16-23`):

| Bloco                   | Conteúdo                                        | Execuções |
| ----------------------- | ------------------------------------------------ | ----------: |
| `run-20260923-231432` | `ITEMS=1`resposta de 0,43 KB                   |         120 |
| `run-20260924-023922` | `ITEMS=50`resposta de 12,3 KB                  |         120 |
| `run-20260924-085547` | `ITEMS=200`resposta de 49,1 KB                 |         120 |
| `run-20260924-122041` | `IO_DELAYS="5 50"` em `ITEMS=50`, c=100      |          80 |
| `run-20261005-162638` | `EXTRA_ROUTES=0`linha de base do eixo de rotas |          10 |
| `run-20261005-164343` | `EXTRA_ROUTES=50`                              |          10 |
| `run-20261005-170050` | `EXTRA_ROUTES=200`                             |          10 |
| `run-20261005-193604` | 5 arms com `WITH_AJV_ARM=1`, só POST          |          25 |

Os quatro primeiros cobrem 4 arms × 2 endpoints × 3 concorrências × 5 repetições.
Os três seguintes cobrem 2 arms × GET × c=100 × 5 repetições: roteamento é resolvido
antes de o handler existir, de modo que não interage com validação, e os arranjos
com schema não acrescentariam informação ao eixo.

O `environment.txt` de cada bloco registra CPU, RAM, kernel, governor, topologia de
L3, núcleo utilizado e a matriz executada inclusive `items` e `routes`, que não
entram no nome dos arquivos e são a chave para a análise entre blocos.

**Não apague os diretórios `results/run-*`** são a evidência bruta que sustenta
os números do Capítulo 5.

### Por que há três pastas de análise

Os eixos foram coletados em sessões separadas por onze dias. Juntar tudo numa
análise única faria a célula comum às duas (`ITEMS=50`, `EXTRA_ROUTES=0`) acumular
dez repetições de dois dias distintos, misturando condições térmicas sem que isso
aparecesse em lugar nenhum. `analise/` reúne os quatro blocos de setembro;
`analise-rotas/`, os três de outubro do eixo de roteamento; `analise-ajv/`, o bloco
de controle da biblioteca de validação, que é o único em POST e exige recorte
próprio. Nenhuma figura cruza sessões.

---

## Execução

O passo 1 escreve `bench.env`; os demais leem dele, então nenhum número é copiado
à mão.

```bash
bash scripts/1-setup.sh --apply     # topologia, pinagem, preparo do host
bash scripts/2-verify.sh            # pré-voo: falha se o experimento for inválido
bash scripts/3-bench.sh             # coleta
python3 scripts/4-analyze.py results/run-<stamp>   # um bloco
python3 scripts/5-consolidar.py                    # entre blocos
```

### Primeira execução em uma máquina nova

```bash
sudo apt install -y git curl util-linux python3-matplotlib tmux
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER   # relogar
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
cd load && npm install && cd ..

bash scripts/1-setup.sh --apply
cat bench.env
bash scripts/2-verify.sh

# piloto de 4 min antes de qualquer bloco longo
ulimit -n 65535
REPS=1 DURATION=20 CONCURRENCIES=100 ENDPOINTS=get bash scripts/3-bench.sh
```

Rode a coleta dentro do `tmux` e com `ulimit -n 65535` na mesma sessão. O `ulimit`
é por sessão e não persiste entre conexões SSH.

Blocos piloto declaram `reps=1` na linha `matriz` do `environment.txt`, e o
`5-consolidar.py` os descarta nominalmente para que não contaminem as médias dos
blocos de produção.

### Blocos de coleta

```bash
# eixo de payload — três matrizes completas (~3h30 cada)
for it in 1 50 200; do ITEMS=$it REPS=5 bash scripts/3-bench.sh; done

# eixo de I/O — apenas em c=100 (~1h30)
IO_DELAYS="5 50" CONCURRENCIES=100 REPS=5 ITEMS=50 bash scripts/3-bench.sh

# eixo de roteamento — três níveis, dois arms (~50 min)
bash coleta-rotas.sh

# controle da biblioteca de validação — 5 arms, só POST (~42 min)
bash coleta-ajv.sh
```

O `coleta-rotas.sh` verifica, antes de começar, que as rotas de enchimento chegaram
ao contêiner: sobe os dois servidores com `EXTRA_ROUTES=200`, requisita
`/api/v1/filler-199/abc` e aborta se a resposta não for 204. Sem essa checagem, uma
variável que não chegasse ao contêiner produziria um resultado nulo indistinguível
de "o roteamento não importa".

O `coleta-ajv.sh` faz verificação equivalente para o braço de controle: confirma que
o Ajv rejeita um payload inválido e aceita um válido antes de iniciar a coleta.

### Análise

```bash
# eixos de payload e de I/O
python3 scripts/5-consolidar.py \
  results/run-20260923-231432 results/run-20260924-023922 \
  results/run-20260924-085547 results/run-20260924-122041 --out analise

# eixo de roteamento
python3 scripts/5-consolidar.py results/run-20261005-162638 \
  results/run-20261005-164343 results/run-20261005-170050 --out analise-rotas

# arm de controle da biblioteca de validação
python3 scripts/5-consolidar.py results/run-20261005-193604 --out analise-ajv
```

---

## Parâmetros desta execução

| Parâmetro        | Valor          | Origem                                                          |
| ----------------- | -------------- | --------------------------------------------------------------- |
| `SERVER_CPU`    | 1              | `1-setup.sh` CCD `0-7,16-23`, irmão SMT `[1,17]` ocioso |
| `LOAD_CPUS`     | `8-15,24-31` | `1-setup.sh`CCD oposto, sem disputa de L3                     |
| `ITEMS`         | 1, 50, 200     | eixo de payload                                                 |
| `IO_DELAYS`     | 0, 5, 50       | eixo de latência de dependência                               |
| `EXTRA_ROUTES`  | 0, 50, 200     | eixo de roteamento                                              |
| `CONCURRENCIES` | 10, 100, 500   | conexões concorrentes                                          |
| `WORKERS`       | 4              | processos de trabalho do autocannon                             |
| `REPS`          | 5              | repetições por célula                                        |

**Hardware:** AMD Ryzen 9 9950X (16 núcleos, 32 threads, dois CCDs de 32 MB de L3),
64 GB de RAM, dos quais 60,4 GB disponíveis ao sistema. Todos os núcleos reportam o
mesmo teto de frequência (5.756.452 kHz), de modo que a escolha do núcleo do alvo
foi arbitrária dentro de um CCD.

---

## Desenho experimental

### Fatorial 2x2 — de onde vem a diferença

|                   | sem otimização de schema | com otimização de schema                         |
| ----------------- | -------------------------- | -------------------------------------------------- |
| **Express** | `express-plain`          | `express-zod`                                    |
| **Fastify** | `fastify-plain`          | `fastify-schema` (Ajv JIT + fast-json-stringify) |

O desenho permite decompor em vez de apenas constatar:

- `fastify-plain` vs `express-plain` isola o overhead de framework;
- `fastify-schema` vs `fastify-plain` mede o efeito do schema;
- `express-zod` vs `express-plain` mede o custo da validação tradicional;
- `fastify-schema` vs `express-zod` representa o cenário realista de mercado.

`WITH_AJV_ARM=1` acrescenta `express-ajv`, que usa o **mesmo Ajv 8.20.0** do Fastify.

### Como a decomposição funciona

A resposta do POST tem 455 bytes fixos em todas as configurações; o parâmetro
`ITEMS` dimensiona apenas a coleção devolvida pelo GET. Isso separa os dois efeitos
que o schema ativa simultaneamente:

- **GET** — resposta cresce, não há corpo a validar → isola a serialização;
- **POST** — resposta fixa, há corpo a validar → isola a validação.

O resultado é inequívoco: no POST o efeito permanece estável entre 8,0% e 9,2% nos
três tamanhos; no GET cresce em módulo de 5,2% para 42,8%, um fator de oito.

### Como o eixo de roteamento funciona

`EXTRA_ROUTES=N` registra N rotas de enchimento **antes** das rotas medidas, com
parâmetro de caminho (`/api/v1/filler-i/:id`) para que nenhum dos dois frameworks
caia no atalho de rota estática. Elas nunca são requisitadas; existem apenas para
dimensionar a tabela de rotas.

O Express percorre um arranjo de camadas em ordem, testando uma expressão regular
por vez, com custo O(n) no número de rotas registradas antes da que casa. O Fastify
usa uma árvore radix, cujo custo depende do comprimento da URL e não da quantidade
de rotas irmãs. O ajuste linear confirma a previsão: 0,0688 µs por rota no Express
(R² = 0,998, custos marginais de 0,055 e 0,072 nos dois intervalos) contra
0,0007 µs no Fastify razão de 93×.

A consequência é específica. Com as cinco rotas da montagem mínima, o roteamento
responde por 0,34 µs dos 38,5 µs de diferença, menos de 1%: **não é ele que explica
a vantagem de base**. Mas é o único componente identificado que escala com o tamanho
da aplicação com duzentas rotas acrescenta 13,8 µs e eleva a diferença para
52,1 µs. Uma medição com tabela de rotas mínima subestima a vantagem do Fastify em
aplicações de porte realista.

### Isolamento da biblioteca de validação

A comparação `fastify-schema` vs `express-zod` confunde dois efeitos: o framework e a
biblioteca de validação, já que um lado usa Zod e o outro Ajv compilado. O arm de
controle `express-ajv` (`WITH_AJV_ARM=1`) usa o **mesmo Ajv 8.20.0** que o Fastify,
mantendo a biblioteca constante.

Medido num bloco dedicado, só em POST, onde a validação age:

| comparação                                | vantagem do Fastify | razão de vazão |
| ------------------------------------------- | ------------------: | ---------------: |
| contra `express-zod`bibliotecas distintas |            +197,46% |           2,9746 |
| contra `express-ajv` mesma biblioteca    |  **+184,23%** |           2,8423 |

A razão entre os dois fatores, 2,8423 / 2,9746, indica que **95,6% do efeito é do
framework e 4,4% era da biblioteca**. Ambas as comparações com p = 0,0079 e delta de
Cliff = 1,00.

Em custo absoluto de CPU por requisição o quadro é mais direto: o mesmo Ajv custa
1,69 µs no Fastify e 1,74 µs no Express diferença de 3%, dentro do ruído
enquanto o Zod custa 4,53 µs, 2,6 vezes mais. A validação, portanto, custa o mesmo
nos dois frameworks quando a biblioteca é a mesma, e toda a diferença entre eles
reside no caminho que não é validação.

Consistência interna do bloco: a razão `fastify-schema`/`express-plain` calculada por
dois caminhos independentes via `express-zod` e via `fastify-plain` converge para
2,7580 com divergência de 0,00%.

### Controle interno

O endpoint GET não recebe corpo e, portanto, não exerce validação. Nas nove células
GET testadas, `express-zod` vs `express-plain` não apresentou p < 0,05 em nenhuma
(menor p = 0,5476; efeito médio +0,281%). Nas mesmas células do POST, todas deram
p = 0,0079.

Detectar ausência de efeito onde a teoria prevê ausência, e efeito onde a teoria
prevê efeito, é evidência de que o instrumento mede a grandeza pretendida.

---

## Qualidade dos dados

| Critério                             | Limite           | Observado                                                 |
| ------------------------------------- | ---------------- | --------------------------------------------------------- |
| Coeficiente de variação da vazão   | < 5%             | mediana 0,81%; máximo 3,57%; nenhuma célula acima de 5% |
| Respostas fora da faixa de sucesso    | 0                | 0 em 819.998.831 requisições                            |
| Erros de transporte                   | —               | 754 timeouts, 0,0001% do total                            |
| Saturação do contêiner (io=0)      | próxima de 100% | 98,3% a 101,5% por execução                             |
| Deriva entre metades das repetições | < 3% acima do CV | 2 células no limiar, sem padrão                         |

A saturação de CPU entre 98,3% e 101,5% confirma que o gargalo residiu no núcleo do
contêiner, e não no gerador de carga nem no caminho de rede.

Nenhuma requisição retornou código de estado fora da faixa de sucesso em qualquer
execução. Os 754 erros registrados são timeouts de transporte e concentraram-se
integralmente na condição mais extrema GET de 49,1 KB com 500 conexões, onde
representam 0,0083% das 9.085.701 requisições daquele subconjunto.

Três células ultrapassaram 3% de coeficiente de variação, todas em POST:
`fastify-plain` com 1 item (3,28%), `express-plain` com 200 itens (3,19%) e
`fastify-plain` com 200 itens (3,57%). Todas permanecem dentro do limiar de
aceitação de 5% definido no Capítulo 4.

### Reprodutibilidade entre sessões

A condição `ITEMS=50`, GET, c=100 foi medida em 24/09 e novamente em 05/10, em
sessões térmicas distintas e com o script de coleta alterado no intervalo. A razão
de vazão entre os frameworks foi 2,4160 e 2,3723 uma diferença de **1,8%**, dentro
da dispersão interna das próprias células.

A razão de vazão é a grandeza que reproduz; expressa como ganho percentual, a mesma
diferença aparece como 4,4 pontos (+141,6% contra +137,2%), porque o ganho amplifica
variações na razão quando esta é alta.

---

## Ameaças à validade

1. **Tráfego por loopback**, não por interface física. Loopback opera com MTU de
   64 KB e sem custo de driver, de modo que os valores absolutos excedem os de
   implantação real. Como os quatro arms percorreram o mesmo caminho, a limitação
   compromete a generalização dos absolutos, não a validade das comparações.
2. **Latência de dependência simulada** com `setTimeout`, que reproduz a *espera*
   por uma dependência e não o *trabalho* associado a ela não há chamada de
   sistema, uso do pool de threads nem análise de resposta de banco.
3. **Uma única arquitetura de hardware** (Zen 5, um núcleo, 512 MB). Não se sabe se
   os achados se mantêm em ARM ou com hierarquia de cache distinta.
4. **Modelo fechado de carga.** `autocannon -c N` é closed-loop: quando o servidor
   degrada, o cliente reduz a emissão na mesma proporção, produzindo percentis
   otimistas (*coordinated omission*). As medidas aqui obtidas correspondem à
   capacidade máxima de processamento, não à latência sob taxa de chegada fixa;
   afirmações sobre cauda de latência exigiriam um gerador de laço aberto, não
   utilizado neste experimento.
5. **Conexões não são usuários.** Cada conexão emite requisições em sequência, sem
   tempo de reflexão uma única conexão chega a emitir ~520 req/s.
6. **Recursos compartilhados entre CCDs**: controlador de memória, Infinity Fabric e
   o caminho de loopback no kernel.
7. **Escopo dos blocos de controle.** O eixo de roteamento foi medido em três níveis
   (0, 50, 200) apenas em GET, c=100 e `ITEMS=50`; a linearidade observada sustenta
   extrapolação dentro dessa faixa, não fora dela. O arm `express-ajv` foi medido
   apenas em POST, c=100 e `ITEMS=50` suficiente para isolar a biblioteca, já que
   a validação só age no POST, mas não varre as demais condições.
   `EXTRA_MIDDLEWARES` e `EXTRA_HOOKS` existem no aparato e permanecem não
   exercitados.
8. **`µs por requisição` só é interpretável em saturação.** Abaixo dela, a métrica
   divide também o custo de fundo do processo pelas requisições e infla. Por isso o
   eixo de I/O é apresentado em utilização de CPU, não em custo por requisição.

---

## Estrutura

```
common/contract.js        schema + dataset compartilhados (fonte única da verdade)
express-api/              Express 5.2.1
fastify-api/              Fastify 5.12.1
load/                     payload JSON e autocannon 8.0.0 travado por package-lock
scripts/1-setup.sh        topologia, pinagem por CCD, preparo -> bench.env
scripts/2-verify.sh       pré-voo (falha se o experimento estiver inválido)
scripts/3-bench.sh        coleta
scripts/4-analyze.py      estatística, custo, figuras de 16 cm — um bloco
scripts/5-consolidar.py   análise entre blocos, decomposição fixo/marginal
coleta-rotas.sh           eixo de roteamento, com verificação prévia
coleta-ajv.sh             controle da biblioteca de validação, com verificação prévia
results/run-*/            evidência bruta dos oito blocos
analise/                  eixos de carga útil e de latência de dependência
analise-rotas/            eixo de roteamento
analise-ajv/              controle da biblioteca de validação
```

O `common/contract.js` existe para eliminar a principal variável de confusão: ambas
as APIs importam o mesmo schema e o mesmo conjunto de dados. O `2-verify.sh` confirma
que os cinco arms devolvem respostas com MD5 idêntico antes de qualquer coleta.

O `5-consolidar.py` importa as funções estatísticas do `4-analyze.py` em vez de
reimplementá-las, de modo que os números consolidados saem do mesmo código que gera
os resultados por bloco.

---

## Correções aplicadas durante a validação

Registradas porque cada uma produziria dados silenciosamente errados:

| Problema                                                                  | Efeito se não corrigido                                             |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `docker stats` emite sequências ANSI mesmo sem TTY                     | todas as amostras de CPU/RAM descartadas sem erro                    |
| `awk` do Ubuntu é o mawk, que bufferiza a entrada                      | CSV de CPU/RAM vazio a coleta inteira                                |
| `autocannon -b "@arquivo"` é sintaxe de curl                           | 100% de erro no POST, reportado como vazão válida                  |
| `deploy.resources.limits` é sintaxe de Swarm                           | limites de cgroup ignorados em silêncio                             |
| `systemd-detect-virt` sai com código 1 quando não há virtualização | falso positivo bloqueando o passo 1                                  |
| Ausência de `--max-old-space-size`                                     | V8 dimensiona o heap pela RAM do host e o contêiner morre por OOM   |
| `EXTRA_ROUTES` não era repassado ao contêiner pelo `3-bench.sh`     | o eixo de roteamento mediria zero, indistinguível de "não importa" |
| Blocos piloto compartilhavam chave de célula com os de produção        | repetições de 20 s entrando na média de janelas de 60 s           |

---

## Reprodução

Qualquer máquina Linux com Docker reproduz o experimento com os passos acima.
Os `package-lock.json` travam as versões exatas de Express, Fastify, Ajv, Zod e
autocannon; os Dockerfiles fixam a imagem base em `node:22.22.2-alpine`.

A análise não depende do hardware: lê arquivos e faz aritmética, e roda em qualquer
máquina com Python. Apenas a coleta exige o ambiente controlado.

Os valores absolutos dependem do hardware o `environment.txt` de cada execução
registra o necessário para interpretar diferenças entre máquinas.

---

## Citação

```bibtex
@thesis{marques2026benchmark,
  author      = {Jefferson de Souza Marques},
  title       = {Análise Comparativa de Desempenho entre Express.js e Fastify em APIs REST},
  institution = {Universidade Federal do Ceará, Campus Quixadá},
  year        = {2026},
  type        = {Trabalho de Conclusão de Curso (Bacharelado em Ciência da Computação)},
  url         = {https://github.com/jefim9413/express-vs-fastify-benchmark}
}
```

#!/usr/bin/env python3
"""
Consolidação ENTRE blocos de coleta — a análise que sustenta o Cap. 5.

O 4-analyze.py resolve um bloco por vez e não enxerga os fatores que variam
ENTRE blocos: tamanho do payload (ITEMS) e número de rotas (EXTRA_ROUTES).
Esses fatores não entram no nome dos arquivos; ficam registrados na linha
`modo` do environment.txt de cada diretório. Este script lê essa linha, junta
todos os blocos num único conjunto e roda as comparações cruzadas.

Decisões de método que precisam constar do Cap. 4:
  - As funções estatísticas NÃO são reimplementadas aqui: são importadas do
    4-analyze.py. Duas implementações do mesmo teste podem divergir, e aí os
    números do Cap. 5 não fecham com os CSVs por bloco.
  - Comparar blocos coletados em dias/sessões térmicas distintas é mais fraco
    que comparar arms dentro do mesmo bloco. O script registra a data de cada
    bloco e alerta quando uma comparação cruza sessões.
  - n=5 por célula: Mann-Whitney U exato (p mínimo possível com 5x5 = 0,0079)
    e Cliff's delta como tamanho de efeito. Sem teste t.
  - Células com número de repetições diferente não são comparadas em silêncio:
    geram alerta.

Uso:
    python3 scripts/5-consolidar.py                     # todos os results/run-*
    python3 scripts/5-consolidar.py --out analise       # diretório de saída
    python3 scripts/5-consolidar.py --conc-ref 100      # concorrência das figuras
    python3 scripts/5-consolidar.py results/run-A results/run-B   # subconjunto

Saídas em --out (padrão: analise/):
    dataset_consolidado.csv   uma linha por execução, com items e routes
    resumo_consolidado.csv    uma linha por célula experimental
    comparacoes_cruzadas.csv  pares com ganho %, p e delta
    fig1..fig7                figuras vetoriais (PDF) + PNG
"""
import argparse
import csv
import glob
import importlib.util
import os
import re
import statistics as st
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
FIG_W_CM = 16.0          # largura útil do texto no template do TCC


def p_floor(n_a, n_b):
    """
    Menor p bicaudal alcançável pelo Mann-Whitney exato com estes n.

    Com 5 contra 5 são 2/C(10,5) = 0,00794. Um p igual a esse piso significa
    "separação completa entre os grupos", NÃO "evidência fortíssima": o teste
    chegou ao limite do que n=5 permite afirmar. Isso precisa estar explícito
    no Cap. 5, senão o p vira retórica.
    """
    from math import comb
    return 2 / comb(n_a + n_b, n_a)


# --------------------------------------------------------------- utilidades ---
def load_analyze():
    """
    Carrega o 4-analyze.py como módulo. O nome começa com dígito, então um
    `import` normal não funciona — daí o importlib. O arquivo tem guarda
    __main__, então carregar não dispara a análise.
    """
    path = os.path.join(_HERE, "4-analyze.py")
    if not os.path.exists(path):
        sys.exit(f"ERRO: {path} nao encontrado. Rode este script de dentro do repositorio.")
    spec = importlib.util.spec_from_file_location("bench_analyze", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fn in ("parse_dir", "mann_whitney_exact", "cliffs_delta", "agg", "cost_metrics"):
        if not hasattr(mod, fn):
            sys.exit(f"ERRO: 4-analyze.py nao expoe {fn}() — versoes incompativeis.")
    return mod


def parse_env(d):
    """
    Extrai os fatores que só existem no environment.txt.

    Os padrões usam \\b...=(\\d+) e NÃO dependem da ordem dos campos na linha
    `modo`. Isso é deliberado: blocos antigos não têm `routes=`, e um parser
    posicional quebraria ao acrescentá-lo.
    """
    info = {"bloco": os.path.basename(d.rstrip(os.sep)), "items": None,
            "routes": 0, "data": "", "sessao": ""}
    p = os.path.join(d, "environment.txt")
    if not os.path.exists(p):
        return info
    txt = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r"\bitems=(\d+)", txt)
    if m:
        info["items"] = int(m.group(1))
    m = re.search(r"\broutes=(\d+)", txt)      # ausente nos blocos pré-patch => 0
    if m:
        info["routes"] = int(m.group(1))
    m = re.search(r"^data\s*:\s*(\S+)", txt, re.M)
    if m:
        info["data"] = m.group(1)
        info["sessao"] = m.group(1)[:10]        # AAAA-MM-DD: proxy de sessão térmica
    return info


def pct(b, a):
    """Ganho relativo de b sobre a, em %. None quando a base é zero."""
    return None if not a else (b / a - 1) * 100


# ------------------------------------------------------------------ coleta ---
def collect(dirs, A):
    rows, blocos, warnings = [], [], []
    for d in dirs:
        if not os.path.isdir(os.path.join(d, "raw")):
            warnings.append(f"{d}: sem subdiretorio raw/ — ignorado.")
            continue
        env = parse_env(d)
        try:
            rs = A.parse_dir(d)
        except Exception as exc:                      # bloco corrompido não derruba o resto
            warnings.append(f"{d}: falha ao ler ({exc.__class__.__name__}: {exc}) — ignorado.")
            continue
        if not rs:
            warnings.append(f"{d}: nenhuma execucao encontrada — ignorado.")
            continue
        if env["items"] is None:
            warnings.append(
                f"{d}: environment.txt sem 'items=' — bloco nao posicionavel no "
                f"eixo de payload. Registrado como items=-1.")
            env["items"] = -1
        for r in rs:
            r["items"] = env["items"]
            r["routes"] = env["routes"]
            r["bloco"] = env["bloco"]
            r["sessao"] = env["sessao"]
            rows.append(r)
        blocos.append(dict(env, execucoes=len(rs)))
    return rows, blocos, warnings


# --------------------------------------------------------------- agregação ---
CELL = ("items", "routes", "endpoint", "io_ms", "concurrency", "arm")


def summarize(rows, A, expected_reps):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in CELL)].append(r)

    out, warnings = [], []
    for key, rs in sorted(groups.items()):
        items, routes, endpoint, io_ms, conc, arm = key
        cel = f"items{items}/rt{routes}/{arm}/{endpoint}/io{io_ms}/c{conc}"
        rps = A.agg([r["rps_mean"] for r in rs])
        cpu = A.agg([r["cpu_mean_pct"] for r in rs])
        p99 = [r["lat_p99_ms"] for r in rs]
        errs = sum(r["non2xx"] + r["errors"] + r["timeouts"] for r in rs)
        tot = sum(r["total_requests"] for r in rs)
        err_pct = (errs / tot * 100) if tot else 0.0

        row = {
            "items": items, "routes": routes, "endpoint": endpoint, "io_ms": io_ms,
            "concorrencia": conc, "arm": arm,
            "framework": rs[0]["framework"], "variant": rs[0]["variant"],
            "bloco": rs[0]["bloco"], "sessao": rs[0]["sessao"], "reps": len(rs),
            "rps_media": round(rps.get("mean", 0) or 0, 1),
            "rps_dp": round(rps.get("sd", 0) or 0, 1),
            "rps_cv_pct": round(rps.get("cv_pct", 0) or 0, 2),
            "lat_media_ms": round(A.agg([r["lat_mean_ms"] for r in rs]).get("mean", 0) or 0, 3),
            "p99_mediana_ms": round(st.median(p99), 3) if p99 else "",
            "p99_pior_ms": round(max(p99), 3) if p99 else "",
            "cpu_media_pct": round(cpu.get("mean", 0) or 0, 1),
            "mem_media_mb": round(A.agg([r["mem_mean_mb"] for r in rs]).get("mean", 0) or 0, 1),
            "loop_p99_ms": round(A.agg([r["loop_p99_ms"] for r in rs]).get("mean", 0) or 0, 3),
            "vazao_mb_s": round(A.agg([r["throughput_mb"] for r in rs]).get("mean", 0) or 0, 2),
            "erro_pct": round(err_pct, 3),
            "enlace_pct": round(A.agg([r["link_util_pct"] for r in rs]).get("mean", 0) or 0, 1),
        }
        cm = A.cost_metrics(rps.get("mean"), cpu.get("mean"))
        row["req_por_vcpu_hora"] = round(cm["req_por_vcpu_hora"]) if cm else ""
        row["usd_por_milhao_req"] = round(cm["usd_por_milhao_req"], 4) if cm else ""

        # Custo por requisição em microssegundos de CPU: é esta coluna que
        # sustenta a leitura de "custo fixo por requisição" do Cap. 5.
        if rps.get("mean") and cpu.get("mean"):
            row["us_cpu_por_req"] = round((cpu["mean"] / 100.0) / rps["mean"] * 1e6, 2)
        else:
            row["us_cpu_por_req"] = ""

        out.append(row)

        if len(rs) != expected_reps:
            warnings.append(f"{cel}: {len(rs)} repeticoes (esperado {expected_reps}) — celula incompleta.")
        if (rps.get("cv_pct") or 0) > 5:
            warnings.append(f"{cel}: CV {rps['cv_pct']:.1f}% — ambiente ruidoso.")
        if err_pct > 0.1:
            warnings.append(f"{cel}: erro {err_pct:.2f}% — vazao nao comparavel.")
        if not any(r["cpu_samples"] for r in rs):
            warnings.append(f"{cel}: nenhuma amostra de CPU/RAM — custo indisponivel.")
    return out, warnings


# ------------------------------------------------------------- comparações ---
PAIRS = [
    ("express-plain", "fastify-plain",  "efeito puro do framework"),
    ("express-zod",   "fastify-schema", "cenario realista de mercado"),
    ("express-plain", "express-zod",    "custo da validacao no Express"),
    ("fastify-plain", "fastify-schema", "efeito do schema no Fastify"),
    ("express-ajv",   "fastify-schema", "controle: mesmo Ajv dos dois lados"),
]


def compare(rows, A):
    """Comparações par a par DENTRO de cada célula (mesmo items, routes, io, conc)."""
    byarm = defaultdict(list)
    for r in rows:
        byarm[(r["items"], r["routes"], r["endpoint"], r["io_ms"],
               r["concurrency"], r["arm"])].append(r)

    ctx = sorted({(r["items"], r["routes"], r["endpoint"], r["io_ms"], r["concurrency"])
                  for r in rows})
    comps, warnings = [], []
    for items, routes, endpoint, io_ms, conc in ctx:
        for a_name, b_name, rotulo in PAIRS:
            ra = byarm.get((items, routes, endpoint, io_ms, conc, a_name), [])
            rb = byarm.get((items, routes, endpoint, io_ms, conc, b_name), [])
            if not ra or not rb:
                continue
            A_v = [r["rps_mean"] for r in ra]
            B_v = [r["rps_mean"] for r in rb]
            ma = st.mean(A_v)
            if not ma:
                warnings.append(f"items{items}/rt{routes}/{a_name}/{endpoint}/io{io_ms}/c{conc}: "
                                f"vazao media zero — ganho nao calculavel.")
                continue
            if len(A_v) != len(B_v):
                warnings.append(f"items{items}/rt{routes}/{endpoint}/io{io_ms}/c{conc} "
                                f"{b_name} vs {a_name}: n diferente ({len(B_v)} vs {len(A_v)}).")
            d, mag = A.cliffs_delta(B_v, A_v)
            p = A.mann_whitney_exact(A_v, B_v)
            sa, sb = ra[0]["sessao"], rb[0]["sessao"]
            comps.append({
                "items": items, "routes": routes, "endpoint": endpoint, "io_ms": io_ms,
                "concorrencia": conc, "comparacao": f"{b_name} vs {a_name}",
                "interpretacao": rotulo,
                "ganho_pct": round(pct(st.mean(B_v), ma), 2),
                "p_mann_whitney": round(p, 5) if p is not None else "",
                "p_no_piso": ("sim" if p is not None
                              and p <= p_floor(len(A_v), len(B_v)) * 1.0001 else "nao"),
                "cliffs_delta": round(d, 3), "magnitude": mag,
                "n_a": len(A_v), "n_b": len(B_v),
                "mesma_sessao": "sim" if sa and sa == sb else "nao",
            })
    return comps, warnings


def cross_factor(summary):
    """
    Efeitos ao longo dos fatores que variam ENTRE blocos.

    Cada linha compara o mesmo par de arms em dois níveis distintos do mesmo
    fator (payload ou rotas). Comparações assim cruzam sessões de coleta — por
    isso saem rotuladas, e não misturadas com as comparações intra-célula.
    """
    def cell(arm, items, routes, endpoint, io_ms, conc):
        for r in summary:
            if (r["arm"] == arm and r["items"] == items and r["routes"] == routes
                    and r["endpoint"] == endpoint and r["io_ms"] == io_ms
                    and r["concorrencia"] == conc):
                return r
        return None

    out = []
    for fator, chave in (("payload (ITEMS)", "items"), ("rotas (EXTRA_ROUTES)", "routes")):
        niveis = sorted({r[chave] for r in summary if r[chave] not in (None, -1)})
        if len(niveis) < 2:
            continue
        for endpoint in sorted({r["endpoint"] for r in summary}):
            for conc in sorted({r["concorrencia"] for r in summary}):
                for a_name, b_name, rotulo in PAIRS:
                    serie = []
                    for niv in niveis:
                        kw = {"items": niv, "routes": 0} if chave == "items" else {"items": 50, "routes": niv}
                        ca = cell(a_name, endpoint=endpoint, io_ms=0, conc=conc, **kw)
                        cb = cell(b_name, endpoint=endpoint, io_ms=0, conc=conc, **kw)
                        if not ca or not cb or not ca["rps_media"]:
                            continue
                        serie.append((niv, pct(cb["rps_media"], ca["rps_media"]),
                                      ca["sessao"], cb["sessao"]))
                    if len(serie) < 2:
                        continue
                    ganhos = [s[1] for s in serie]
                    sessoes = {s[2] for s in serie} | {s[3] for s in serie}
                    out.append({
                        "fator": fator, "endpoint": endpoint, "concorrencia": conc,
                        "comparacao": f"{b_name} vs {a_name}", "interpretacao": rotulo,
                        "niveis": " ".join(str(s[0]) for s in serie),
                        "ganho_por_nivel_pct": " ".join(f"{g:+.2f}" for g in ganhos),
                        "amplitude_pp": round(max(ganhos) - min(ganhos), 2),
                        "tendencia": ("cresce" if ganhos[-1] - ganhos[0] > 1 else
                                      "cai" if ganhos[-1] - ganhos[0] < -1 else "estavel"),
                        "sessoes": len(sessoes),
                    })
    return out


def custo_decomposto(summary, conc_ref):
    """
    Separa o custo de CPU por requisição em duas parcelas, por arm e endpoint:

        us_por_req(items) ~= custo_fixo + custo_por_item * items

    O custo_fixo (intercepto) é o que a requisição paga independentemente do
    tamanho da resposta: parsing de cabeçalhos, resolução de rota, montagem do
    objeto de requisição, escrita no socket. O custo_por_item (inclinação) é o
    trabalho de serializar cada elemento.

    Por que isso importa para o Cap. 5: a diferença entre os frameworks NÃO é um
    valor único. Dizer "o Fastify economiza 36us por requisicao" só vale no
    intercepto; a inclinação pode inverter o sinal da vantagem em payload
    grande, e foi o que aconteceu com o fast-json-stringify.

    Controle embutido: no POST a resposta tem tamanho FIXO, logo a inclinação
    deve ser ~0. Se não for, a atribuição do efeito a serialização está errada.

    Ajuste por mínimos quadrados comum. Com 3 níveis de ITEMS o R2 é pouco
    informativo, então reporta-se também o custo marginal de CADA intervalo:
    valores próximos entre intervalos são a evidência de linearidade, e isso é
    mais honesto que um R2 sobre 3 pontos.

    ATENÇÃO ao ler o R2 das linhas de POST: quando a inclinação é ~0, não existe
    variação para o modelo explicar, então o R2 cai para perto de zero medindo
    apenas ruído. R2 baixo ali CONFIRMA a série plana — não indica ajuste ruim.
    Quem decide é a coluna custo_por_item_us, não o R2.
    """
    out = []
    for endpoint in sorted({r["endpoint"] for r in summary}):
        for arm in sorted({r["arm"] for r in summary}):
            pts = []
            for r in summary:
                if (r["arm"] == arm and r["endpoint"] == endpoint and r["io_ms"] == 0
                        and r["concorrencia"] == conc_ref and r["routes"] == 0
                        and r["items"] not in (None, -1) and r["us_cpu_por_req"] != ""):
                    pts.append((r["items"], float(r["us_cpu_por_req"])))
            pts.sort()
            if len(pts) < 2:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            n = len(xs)
            mx, my = st.mean(xs), st.mean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            if not sxx:
                continue
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            inter = my - slope * mx
            sst = sum((y - my) ** 2 for y in ys)
            ssr = sum((y - (inter + slope * x)) ** 2 for x, y in zip(xs, ys))
            r2 = (1 - ssr / sst) if sst else ""
            marg = [f"{(ys[i+1]-ys[i])/(xs[i+1]-xs[i]):.3f}" for i in range(n - 1)]
            out.append({
                "endpoint": endpoint, "arm": arm, "concorrencia": conc_ref,
                "niveis_items": " ".join(str(x) for x in xs),
                "us_por_req": " ".join(f"{y:.2f}" for y in ys),
                "custo_fixo_us": round(inter, 2),
                "custo_por_item_us": round(slope, 4),
                "r2": round(r2, 4) if r2 != "" else "",
                "marginal_por_intervalo": " ".join(marg),
                "pontos": n,
            })
    return out


# ------------------------------------------------------------------ figuras ---
def make_figures(summary, outdir, conc_ref):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib ausente: pip install matplotlib --break-system-packages)")
        return []

    inch = FIG_W_CM / 2.54
    feitas = []

    def save(fig, nome):
        base = os.path.join(outdir, nome)
        fig.tight_layout()
        fig.savefig(base + ".pdf")                 # vetorial: exigência do LaTeX
        fig.savefig(base + ".png", dpi=300)
        plt.close(fig)
        feitas.append(nome)

    def val(arm, **kw):
        for r in summary:
            if r["arm"] == arm and all(r[k] == v for k, v in kw.items()):
                return r
        return None

    def serie_items(a_name, b_name, endpoint, campo="rps_media"):
        xs, ys = [], []
        for it in sorted({r["items"] for r in summary if r["items"] != -1}):
            ca = val(a_name, items=it, routes=0, endpoint=endpoint, io_ms=0, concorrencia=conc_ref)
            cb = val(b_name, items=it, routes=0, endpoint=endpoint, io_ms=0, concorrencia=conc_ref)
            if ca and cb and ca[campo]:
                xs.append(str(it))
                ys.append(pct(cb[campo], ca[campo]))
        return xs, ys

    # fig1 — vantagem do framework vs tamanho do payload
    fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
    algo = False
    for endpoint in ("get", "post"):
        xs, ys = serie_items("express-plain", "fastify-plain", endpoint)
        if xs:
            ax.plot(xs, ys, marker="o", linewidth=1.4, label=endpoint.upper())
            algo = True
    if algo:
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Itens no corpo da resposta (ITEMS)")
        ax.set_ylabel("Vantagem do Fastify (%)")
        ax.grid(True, linestyle=":", linewidth=0.6)
        ax.legend(fontsize=7)
        save(fig, "fig1_vantagem_payload")
    else:
        plt.close(fig)

    # fig2 — decomposição do efeito do schema (figura-chave)
    # POST tem resposta de tamanho FIXO: o efeito do schema ali isola a
    # VALIDAÇÃO. GET cresce com ITEMS: ali o efeito isola a SERIALIZAÇÃO.
    fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
    algo = False
    for endpoint, rot in (("post", "POST — isola validação (resposta fixa)"),
                          ("get", "GET — isola serialização (resposta cresce)")):
        xs, ys = serie_items("fastify-plain", "fastify-schema", endpoint)
        if xs:
            ax.plot(xs, ys, marker="s" if endpoint == "post" else "o",
                    linewidth=1.4, label=rot)
            algo = True
    if algo:
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Itens no corpo da resposta (ITEMS)")
        ax.set_ylabel("Efeito do schema no Fastify (%)")
        ax.grid(True, linestyle=":", linewidth=0.6)
        ax.legend(fontsize=7)
        save(fig, "fig2_decomposicao_schema")
    else:
        plt.close(fig)

    # fig3 — ganho vs latência de dependência simulada
    ios = sorted({r["io_ms"] for r in summary})
    if len(ios) > 1:
        fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
        algo = False
        for endpoint in ("get", "post"):
            xs, ys = [], []
            for io_ms in ios:
                ca = val("express-zod", items=50, routes=0, endpoint=endpoint,
                         io_ms=io_ms, concorrencia=conc_ref)
                cb = val("fastify-schema", items=50, routes=0, endpoint=endpoint,
                         io_ms=io_ms, concorrencia=conc_ref)
                if ca and cb and ca["rps_media"]:
                    xs.append(str(io_ms))
                    ys.append(pct(cb["rps_media"], ca["rps_media"]))
            if xs:
                ax.plot(xs, ys, marker="o", linewidth=1.4, label=endpoint.upper())
                algo = True
        if algo:
            ax.axhline(0, color="black", linewidth=0.8)
            ax.set_xlabel("Latência de dependência simulada (ms)")
            ax.set_ylabel("Ganho do Fastify sobre o Express (%)")
            ax.grid(True, linestyle=":", linewidth=0.6)
            ax.legend(fontsize=7)
            save(fig, "fig3_ganho_vs_io")
        else:
            plt.close(fig)

        # fig4 — CPU por requisição vs latência de dependência
        fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
        algo = False
        for arm in ("express-zod", "fastify-schema"):
            xs, ys = [], []
            for io_ms in ios:
                c = val(arm, items=50, routes=0, endpoint="get", io_ms=io_ms,
                        concorrencia=conc_ref)
                if c and c["us_cpu_por_req"] != "":
                    xs.append(str(io_ms))
                    ys.append(c["us_cpu_por_req"])
            if xs:
                ax.plot(xs, ys, marker="o", linewidth=1.4, label=arm)
                algo = True
        if algo:
            ax.set_xlabel("Latência de dependência simulada (ms)")
            ax.set_ylabel("CPU por requisição (µs)")
            ax.grid(True, linestyle=":", linewidth=0.6)
            ax.legend(fontsize=7)
            save(fig, "fig4_cpu_vs_io")
        else:
            plt.close(fig)

    # fig5 — vazão por concorrência, um painel por endpoint
    for endpoint in sorted({r["endpoint"] for r in summary}):
        itens = sorted({r["items"] for r in summary if r["items"] != -1})
        it_ref = 50 if 50 in itens else (itens[0] if itens else None)
        if it_ref is None:
            continue
        fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
        algo = False
        for arm in sorted({r["arm"] for r in summary}):
            pts = sorted((r["concorrencia"], r["rps_media"], r["rps_dp"]) for r in summary
                         if r["arm"] == arm and r["items"] == it_ref and r["routes"] == 0
                         and r["endpoint"] == endpoint and r["io_ms"] == 0)
            if len(pts) < 2:
                continue
            ax.errorbar([str(p[0]) for p in pts], [p[1] for p in pts],
                        yerr=[p[2] for p in pts], marker="o", capsize=3,
                        linewidth=1.4, label=arm)
            algo = True
        if algo:
            ax.set_xlabel("Carga (conexões simultâneas)")
            ax.set_ylabel("Vazão (req/s)")
            ax.set_title(f"{endpoint.upper()} — ITEMS={it_ref}", fontsize=9)
            ax.grid(True, linestyle=":", linewidth=0.6)
            ax.legend(fontsize=7)
            save(fig, f"fig5_vazao_{endpoint}")
        else:
            plt.close(fig)

    # fig6 — custo: USD por milhão de requisições
    fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
    arms = sorted({r["arm"] for r in summary})
    itens = sorted({r["items"] for r in summary if r["items"] != -1})
    largura = 0.8 / max(len(itens), 1)
    algo = False
    for i, it in enumerate(itens):
        ys, xs = [], []
        for j, arm in enumerate(arms):
            c = val(arm, items=it, routes=0, endpoint="get", io_ms=0, concorrencia=conc_ref)
            if c and c["usd_por_milhao_req"] != "":
                xs.append(j + i * largura - 0.4 + largura / 2)
                ys.append(c["usd_por_milhao_req"])
        if xs:
            ax.bar(xs, ys, width=largura, label=f"ITEMS={it}")
            algo = True
    if algo:
        ax.set_xticks(range(len(arms)))
        ax.set_xticklabels(arms, fontsize=7, rotation=15)
        ax.set_ylabel("USD por milhão de requisições (GET)")
        ax.grid(True, axis="y", linestyle=":", linewidth=0.6)
        ax.legend(fontsize=7)
        save(fig, "fig6_custo")
    else:
        plt.close(fig)

    # fig7 — ganho vs número de rotas (só existe depois da coleta de roteamento)
    rotas = sorted({r["routes"] for r in summary})
    if len(rotas) > 1:
        fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
        algo = False
        for endpoint in sorted({r["endpoint"] for r in summary}):
            xs, ys = [], []
            for rt in rotas:
                ca = val("express-plain", items=50, routes=rt, endpoint=endpoint,
                         io_ms=0, concorrencia=conc_ref)
                cb = val("fastify-plain", items=50, routes=rt, endpoint=endpoint,
                         io_ms=0, concorrencia=conc_ref)
                if ca and cb and ca["rps_media"]:
                    xs.append(str(rt))
                    ys.append(pct(cb["rps_media"], ca["rps_media"]))
            if len(xs) > 1:
                ax.plot(xs, ys, marker="o", linewidth=1.4, label=endpoint.upper())
                algo = True
        if algo:
            ax.axhline(0, color="black", linewidth=0.8)
            ax.set_xlabel("Rotas registradas antes da rota medida")
            ax.set_ylabel("Vantagem do Fastify (%)")
            ax.grid(True, linestyle=":", linewidth=0.6)
            ax.legend(fontsize=7)
            save(fig, "fig7_ganho_vs_rotas")
        else:
            plt.close(fig)

    return feitas


# -------------------------------------------------------------------- saída ---
def write_csv(path, rows):
    if not rows:
        return False
    campos = list(rows[0].keys())
    for r in rows:                       # união das chaves: blocos heterogêneos
        for k in r:
            if k not in campos:
                campos.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in campos})
    return True


def main():
    ap = argparse.ArgumentParser(description="Consolida todos os blocos de coleta.")
    ap.add_argument("dirs", nargs="*", help="diretorios de bloco (padrao: results/run-*)")
    ap.add_argument("--out", default="analise", help="diretorio de saida (padrao: analise)")
    ap.add_argument("--conc-ref", type=int, default=100,
                    help="concorrencia usada nas figuras de sintese (padrao: 100)")
    ap.add_argument("--expected-reps", type=int,
                    default=int(os.environ.get("EXPECTED_REPS", "5")))
    args = ap.parse_args()

    A = load_analyze()
    dirs = args.dirs or sorted(glob.glob(os.path.join("results", "run-*")))
    if not dirs:
        sys.exit("nenhum bloco encontrado. Rode de dentro do repositorio, "
                 "ou passe os diretorios como argumento.")

    rows, blocos, w1 = collect(dirs, A)
    if not rows:
        sys.exit("nenhuma execucao lida.\n" + "\n".join("  ! " + w for w in w1))

    summary, w2 = summarize(rows, A, args.expected_reps)
    comps, w3 = compare(rows, A)
    cross = cross_factor(summary)
    custo = custo_decomposto(summary, args.conc_ref)
    warnings = list(dict.fromkeys(w1 + w2 + w3))

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "dataset_consolidado.csv"), rows)
    write_csv(os.path.join(args.out, "resumo_consolidado.csv"), summary)
    write_csv(os.path.join(args.out, "comparacoes_cruzadas.csv"), comps)
    if cross:
        write_csv(os.path.join(args.out, "efeitos_por_fator.csv"), cross)
    if custo:
        write_csv(os.path.join(args.out, "custo_decomposto.csv"), custo)

    # ------------------------------------------------------------- relatório ---
    print(f"blocos lidos: {len(blocos)}   execucoes: {len(rows)}   celulas: {len(summary)}")
    print(f"{'bloco':<24} {'data':<12} {'items':>6} {'rotas':>6} {'exec':>5}")
    for b in blocos:
        print(f"{b['bloco']:<24} {b['sessao']:<12} {b['items']:>6} {b['routes']:>6} {b['execucoes']:>5}")

    fatores = {
        "items": sorted({r["items"] for r in rows}),
        "routes": sorted({r["routes"] for r in rows}),
        "io_ms": sorted({r["io_ms"] for r in rows}),
        "concurrency": sorted({r["concurrency"] for r in rows}),
        "arms": sorted({r["arm"] for r in rows}),
    }
    print("\nfatores no conjunto:")
    for k, v in fatores.items():
        print(f"  {k:<12} {v}")

    if cross:
        print("\nefeitos ao longo dos fatores entre blocos:")
        print(f"  {'fator':<22} {'ep':<5} {'comparacao':<34} {'niveis':<12} "
              f"{'ganho por nivel':<26} {'ampl.':>7} {'tend.':<9}")
        for c in cross:
            if c["concorrencia"] != args.conc_ref:
                continue
            print(f"  {c['fator']:<22} {c['endpoint']:<5} {c['comparacao']:<34} "
                  f"{c['niveis']:<12} {c['ganho_por_nivel_pct']:<26} "
                  f"{c['amplitude_pp']:>6.2f}p {c['tendencia']:<9}")

    print(f"\ncomparacoes intra-celula (c={args.conc_ref}, io=0):")
    print(f"  {'items':>5} {'rt':>4} {'ep':<5} {'comparacao':<34} {'ganho':>9} "
          f"{'p':>8} {'delta':>7} {'magnitude':<12} {'piso p':<7}")
    for c in comps:
        if c["concorrencia"] != args.conc_ref or c["io_ms"] != 0:
            continue
        print(f"  {c['items']:>5} {c['routes']:>4} {c['endpoint']:<5} {c['comparacao']:<34} "
              f"{c['ganho_pct']:>+8.2f}% {str(c['p_mann_whitney']):>8} "
              f"{c['cliffs_delta']:>+7.2f} {c['magnitude']:<12} {c['p_no_piso']:<7}")

    if custo:
        print(f"\ndecomposicao do custo de CPU por requisicao (c={args.conc_ref}, io=0):")
        print(f"  {'ep':<5} {'arm':<16} {'items':<12} {'us/req':<22} "
              f"{'fixo(us)':>9} {'/item(us)':>10} {'R2':>7} {'marginal/intervalo':<18}")
        for c in custo:
            print(f"  {c['endpoint']:<5} {c['arm']:<16} {c['niveis_items']:<12} "
                  f"{c['us_por_req']:<22} {c['custo_fixo_us']:>9.2f} "
                  f"{c['custo_por_item_us']:>10.4f} {str(c['r2']):>7} "
                  f"{c['marginal_por_intervalo']:<18}")

    if warnings:
        print(f"\nALERTAS ({len(warnings)}):")
        for wmsg in warnings:
            print("  ! " + wmsg)

    feitas = make_figures(summary, args.out, args.conc_ref)
    if feitas:
        print(f"\nfiguras: {', '.join(feitas)}")
    print(f"\nsaida em {args.out}/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Consolidação estatística do experimento Express.js x Fastify.

Decisões que precisam aparecer no Cap. 4 do TCC:
  - Média SEM dispersão não é resultado. Reporta-se média, desvio-padrão e
    coeficiente de variação (CV). CV > 5% entre repetições = ambiente ruidoso,
    resultado não publicável sem justificativa.
  - Percentis de execuções distintas NÃO são somáveis: a média de P99 não é o
    P99 do conjunto. Aqui usa-se a MEDIANA dos P99 por repetição + o pior caso.
  - n=5 exige teste não-paramétrico exato (Mann-Whitney U) e tamanho de efeito
    (Cliff's delta), não teste t. Diferença "visível no gráfico" não é evidência.

Uso:  python3 scripts/4-analyze.py results/run-AAAAMMDD-HHMMSS
"""
import csv
import json
import os
import statistics as st
import sys
from itertools import combinations
from collections import defaultdict

FIG_W_CM = 16.0  # largura útil do texto exigida pelo template do TCC

# Preço de referência de 1 vCPU por hora, em USD. Ajuste para o provedor que
# você for citar e DECLARE a fonte e a data no Cap. 4 — preço de nuvem muda.
PRICE_PER_VCPU_HOUR = float(os.environ.get("PRICE_PER_VCPU_HOUR", "0.0425"))

# Quantas repetições cada célula deveria ter. Células incompletas são
# reportadas: uma média de 2 execuções ao lado de outra de 5 não é comparável.
EXPECTED_REPS = int(os.environ.get("EXPECTED_REPS", "5"))


# ----------------------------------------------------------------- coleta ---
def parse_dir(outdir):
    raw = os.path.join(outdir, "raw")
    stats_dir = os.path.join(outdir, "stats")
    rows = []
    for fname in sorted(os.listdir(raw)):
        if not fname.endswith(".autocannon.json"):
            continue
        tag = fname[: -len(".autocannon.json")]
        parts = tag.split("__")
        arm, endpoint = parts[0], parts[1]
        io_ms, conc, rep = 0, 0, 0
        for tok in parts[2:]:
            if tok.startswith("io"):
                io_ms = int(tok[2:])
            elif tok.startswith("c"):
                conc = int(tok[1:])
            elif tok.startswith("r"):
                rep = int(tok[1:])
        with open(os.path.join(raw, fname)) as fh:
            ac = json.load(fh)

        metrics_path = os.path.join(raw, f"{tag}.metrics.json")
        loop = {}
        if os.path.exists(metrics_path):
            with open(metrics_path) as fh:
                loop = json.load(fh)

        cpu_vals, mem_vals = [], []
        stats_path = os.path.join(stats_dir, f"{tag}.csv")
        if os.path.exists(stats_path):
            with open(stats_path) as fh:
                for r in csv.DictReader(fh):
                    try:
                        cpu_vals.append(float(r["cpu_percent"]))
                        mem_vals.append(_to_mb(r["mem_usage_mb"]))
                    except (ValueError, KeyError):
                        pass

        link = {}
        link_path = os.path.join(raw, f"{tag}.link.json")
        if os.path.exists(link_path):
            with open(link_path) as fh:
                link = json.load(fh)

        lat = ac.get("latency", {})
        req = ac.get("requests", {})
        rows.append({
            "arm": arm,
            "framework": arm.split("-")[0],
            "variant": arm.split("-", 1)[1],
            "endpoint": endpoint,
            "concurrency": conc,
            "rep": rep,
            "io_ms": io_ms,
            "rps_mean": req.get("mean", 0.0),
            "rps_stddev": req.get("stddev", 0.0),
            "total_requests": ac.get("requests", {}).get("total", 0),
            "lat_mean_ms": lat.get("mean", 0.0),
            "lat_p50_ms": lat.get("p50", 0.0),
            "lat_p95_ms": lat.get("p97_5", lat.get("p95", 0.0)),
            "lat_p99_ms": lat.get("p99", 0.0),
            "lat_max_ms": lat.get("max", 0.0),
            "non2xx": ac.get("non2xx", 0),
            "errors": ac.get("errors", 0),
            "timeouts": ac.get("timeouts", 0),
            "throughput_mb": ac.get("throughput", {}).get("mean", 0.0) / 1048576,
            "cpu_samples": len(cpu_vals),
            "cpu_mean_pct": st.mean(cpu_vals) if cpu_vals else None,
            "cpu_max_pct": max(cpu_vals) if cpu_vals else None,
            "mem_mean_mb": st.mean(mem_vals) if mem_vals else None,
            "mem_max_mb": max(mem_vals) if mem_vals else None,
            "loop_p99_ms": (loop.get("eventLoopDelay") or {}).get("p99Ms"),
            "loop_max_ms": (loop.get("eventLoopDelay") or {}).get("maxMs"),
            "rss_final_mb": (loop.get("memoryMb") or {}).get("rss"),
            "link_util_pct": link.get("util_pct"),
            "link_speed_mbps": link.get("speed_mbps"),
        })
    return rows


def _to_mb(txt):
    txt = txt.strip()
    for suf, mult in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
        if txt.endswith(suf):
            return float(txt[: -len(suf)]) * mult
    return float(txt)


# ------------------------------------------------------------ estatística ---
def mann_whitney_exact(a, b):
    """p bicaudal exato. Válido e recomendado para n pequeno (5 x 5)."""
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return None
    u = sum(1 for x in a for y in b if x > y) + 0.5 * sum(1 for x in a for y in b if x == y)
    u = min(u, n1 * n2 - u)
    combined = sorted(range(n1 + n2))
    count = total = 0
    pooled = list(a) + list(b)
    for idx in combinations(range(n1 + n2), n1):
        g1 = [pooled[i] for i in idx]
        g2 = [pooled[i] for i in combined if i not in idx]
        uu = sum(1 for x in g1 for y in g2 if x > y) + 0.5 * sum(1 for x in g1 for y in g2 if x == y)
        uu = min(uu, n1 * n2 - uu)
        total += 1
        if uu <= u:
            count += 1
    return count / total


def cliffs_delta(a, b):
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    d = (gt - lt) / (len(a) * len(b))
    mag = "desprezivel" if abs(d) < 0.147 else "pequeno" if abs(d) < 0.33 else "medio" if abs(d) < 0.474 else "grande"
    return d, mag


def cost_metrics(rps, cpu_pct):
    """
    Converte desempenho em custo — a promessa que a introdução faz citando
    Armbrust e que o Cap. 4 não cumpria.

    O container tem teto de 1 vCPU, então cpu_pct é percentual DE UM vCPU.
    Duas saídas tangíveis: requisições por vCPU-hora, e quantos vCPUs seriam
    necessários para sustentar 10.000 req/s.
    """
    if not rps or not cpu_pct:
        return {}
    vcpu = cpu_pct / 100.0
    req_per_vcpu_h = rps * 3600 / vcpu
    return {
        "req_por_vcpu_hora": req_per_vcpu_h,
        "usd_por_milhao_req": PRICE_PER_VCPU_HOUR / (req_per_vcpu_h / 1e6),
        "vcpus_para_10k_rps": 10000 / rps * vcpu,
    }


def thermal_drift(rs):
    """
    Tendência de queda da vazão ao longo das repetições.

    Compara a média da PRIMEIRA metade das repetições com a da ÚLTIMA metade.
    Comparar só a 1a com a última repetição (versão anterior) confundia ruído
    com deriva: em simulação com ruído de +-3% e SEM throttling algum, aquele
    critério acusava queda em ~12% dos casos. Por médias de metades o falso
    positivo cai para ~4%, e a detecção de throttling real de 3% por repetição
    continua em 100%.

    Devolve (variação_pct, cv_pct); quem consome exige |variação| > cv para
    tratar como deriva real, e não como dispersão normal.
    """
    ordered = sorted(rs, key=lambda r: r["rep"])
    vals = [r["rps_mean"] for r in ordered]
    if len(vals) < 4:
        return None, None
    h = len(vals) // 2
    prim, ult = st.mean(vals[:h]), st.mean(vals[-h:])
    if not prim:
        return None, None
    media = st.mean(vals)
    cv = (st.stdev(vals) / media * 100) if len(vals) > 1 and media else 0.0
    return (ult / prim - 1) * 100, cv


def agg(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    m = st.mean(vals)
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return {"n": len(vals), "mean": m, "sd": sd, "cv_pct": (sd / m * 100) if m else 0.0,
            "median": st.median(vals), "min": min(vals), "max": max(vals)}


# ------------------------------------------------------------------ saída ---
def main(outdir):
    rows = parse_dir(outdir)
    if not rows:
        sys.exit("nenhum resultado encontrado em " + outdir)

    with open(os.path.join(outdir, "resultados_brutos.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    groups = defaultdict(list)
    for r in rows:
        groups[(r["endpoint"], r["io_ms"], r["concurrency"], r["arm"])].append(r)

    summary = []
    warnings = []
    for (endpoint, io_ms, conc, arm), rs in sorted(groups.items()):
        cel = f"{arm}/{endpoint}/io{io_ms}/c{conc}"
        errs = sum(r["non2xx"] + r["errors"] + r["timeouts"] for r in rs)
        tot = sum(r["total_requests"] for r in rs)
        err_pct = (errs / tot * 100) if tot else 0.0
        rps = agg([r["rps_mean"] for r in rs])
        cpu = agg([r["cpu_mean_pct"] for r in rs])
        p99 = [r["lat_p99_ms"] for r in rs]
        drift, drift_cv = thermal_drift(rs)
        row = {
            "endpoint": endpoint, "io_ms": io_ms, "concorrencia": conc, "arm": arm, "reps": len(rs),
            "rps_media": round(rps["mean"], 1), "rps_dp": round(rps["sd"], 1),
            "rps_cv_pct": round(rps["cv_pct"], 2),
            "lat_media_ms": round(agg([r["lat_mean_ms"] for r in rs])["mean"], 3),
            "p95_mediana_ms": round(st.median([r["lat_p95_ms"] for r in rs]), 3),
            "p99_mediana_ms": round(st.median(p99), 3),
            "p99_pior_ms": round(max(p99), 3),
            "cpu_media_pct": round(cpu.get("mean", 0) or 0, 1),
            "mem_media_mb": round(agg([r["mem_mean_mb"] for r in rs]).get("mean", 0) or 0, 1),
            "loop_p99_ms": round(agg([r["loop_p99_ms"] for r in rs]).get("mean", 0) or 0, 3),
            "erro_pct": round(err_pct, 3),
            "deriva_pct": round(drift, 2) if drift is not None else "",
            "enlace_pct": round(agg([r["link_util_pct"] for r in rs]).get("mean", 0) or 0, 1),
        }
        cm = cost_metrics(rps["mean"], cpu.get("mean"))
        # Sem amostras de CPU não há custo: deixar 0 sugeriria "de graça".
        row["req_por_vcpu_hora"] = round(cm["req_por_vcpu_hora"]) if cm else ""
        row["usd_por_milhao_req"] = round(cm["usd_por_milhao_req"], 4) if cm else ""
        row["vcpus_para_10k_rps"] = round(cm["vcpus_para_10k_rps"], 2) if cm else ""
        summary.append(row)

        # --- critérios de aceitação do dado ---
        if len(rs) != EXPECTED_REPS:
            warnings.append(f"{cel}: {len(rs)} repeticoes, esperado {EXPECTED_REPS} — celula incompleta.")
        if not any(r["cpu_samples"] for r in rs):
            warnings.append(f"{cel}: nenhuma amostra de CPU/RAM — sampler falhou, metricas de hardware ausentes.")
        if rps["cv_pct"] > 5:
            warnings.append(f"CV alto ({rps['cv_pct']:.1f}%) em {cel}: ambiente ruidoso.")
        if err_pct > 0.1:
            warnings.append(f"Taxa de erro {err_pct:.2f}% em {cel}: vazao nao comparavel.")
        util = agg([r["link_util_pct"] for r in rs]).get("mean")
        if util and util > 70:
            warnings.append(
                f"Enlace a {util:.0f}% da capacidade em {cel}: a rede vira co-gargalo "
                f"e comprime a diferenca entre os frameworks. Reduza ITEMS.")
        # Deriva só conta se superar a dispersão da própria célula.
        if drift is not None and drift < -3.0 and abs(drift) > (drift_cv or 0):
            warnings.append(
                f"Queda de {abs(drift):.1f}% da 1a para a ultima metade das repeticoes em {cel} "
                f"(CV {drift_cv:.1f}%): suspeita de throttling termico ou deriva de clock.")

    with open(os.path.join(outdir, "resumo.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    # comparações par a par dentro de cada (endpoint, io, concorrência)
    comps = []
    pairs = [("express-plain", "fastify-plain"),   # efeito puro do framework
             ("express-zod", "fastify-schema"),    # cenário realista de mercado
             ("express-plain", "express-zod"),     # custo da validação no Express
             ("fastify-plain", "fastify-schema")]  # custo/benefício do schema no Fastify

    def sel(name, endpoint, io_ms, conc):
        return [r["rps_mean"] for r in rows if r["arm"] == name
                and r["endpoint"] == endpoint and r["io_ms"] == io_ms
                and r["concurrency"] == conc]

    for endpoint in sorted({r["endpoint"] for r in rows}):
        for io_ms in sorted({r["io_ms"] for r in rows}):
            for conc in sorted({r["concurrency"] for r in rows}):
                for a_name, b_name in pairs:
                    A, B = sel(a_name, endpoint, io_ms, conc), sel(b_name, endpoint, io_ms, conc)
                    if not A or not B:
                        continue
                    ma, mb = st.mean(A), st.mean(B)
                    # Sem a guarda, um arm que não respondeu (media 0) derruba
                    # o script inteiro com ZeroDivisionError no fim da coleta.
                    if not ma:
                        warnings.append(f"{a_name}/{endpoint}/io{io_ms}/c{conc}: vazao media zero — ganho nao calculavel.")
                        continue
                    d, mag = cliffs_delta(B, A)
                    comps.append({
                        "endpoint": endpoint, "io_ms": io_ms, "concorrencia": conc,
                        "comparacao": f"{b_name} vs {a_name}",
                        "ganho_pct": round((mb / ma - 1) * 100, 2),
                        "p_mann_whitney": round(mann_whitney_exact(A, B), 5),
                        "cliffs_delta": round(d, 3), "magnitude": mag,
                    })
    if comps:
        with open(os.path.join(outdir, "comparacoes.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(comps[0].keys()))
            w.writeheader()
            w.writerows(comps)

    print(f"{'endpt':>6} {'io':>4} {'conc':>5} {'arm':>16} {'rps':>10} {'CV%':>6} "
          f"{'p99(ms)':>9} {'cpu%':>6} {'USD/Mreq':>9} {'erro%':>7}")
    for r in summary:
        print(f"{r['endpoint']:>6} {r['io_ms']:>4} {r['concorrencia']:>5} {r['arm']:>16} "
              f"{r['rps_media']:>10.1f} {r['rps_cv_pct']:>6.2f} {r['p99_mediana_ms']:>9.2f} "
              f"{r['cpu_media_pct']:>6.1f} {str(r['usd_por_milhao_req']):>9} {r['erro_pct']:>7.3f}")
    if comps:
        print("\ncomparações (p exato, n pequeno):")
        for c in comps:
            print(f"  {c['endpoint']:>4} io{c['io_ms']:<3} c{c['concorrencia']:<4} {c['comparacao']:<34} "
                  f"{c['ganho_pct']:+8.2f}%  p={c['p_mann_whitney']:.4f}  delta={c['cliffs_delta']:+.2f} ({c['magnitude']})")
    if warnings:
        print("\nALERTAS DE QUALIDADE DO DADO:")
        for wmsg in dict.fromkeys(warnings):
            print("  ! " + wmsg)

    make_charts(summary, outdir)
    print(f"\narquivos gerados em {outdir}")


def make_charts(summary, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib ausente: `pip install matplotlib` para gerar as figuras)")
        return

    inch = FIG_W_CM / 2.54

    # Figura-chave do trabalho: ganho relativo do Fastify vs. latência de
    # dependência. É ela que responde "a partir de quando deixa de importar".
    ios = sorted({r["io_ms"] for r in summary})
    if len(ios) > 1:
        fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
        for endpoint in sorted({r["endpoint"] for r in summary}):
            xs, ys = [], []
            for io_ms in ios:
                a = [r["rps_media"] for r in summary if r["arm"] == "express-zod"
                     and r["io_ms"] == io_ms and r["endpoint"] == endpoint]
                b = [r["rps_media"] for r in summary if r["arm"] == "fastify-schema"
                     and r["io_ms"] == io_ms and r["endpoint"] == endpoint]
                if a and b and st.mean(a):
                    xs.append(str(io_ms))
                    ys.append((st.mean(b) / st.mean(a) - 1) * 100)
            if xs:
                ax.plot(xs, ys, marker="o", label=f"{endpoint.upper()}", linewidth=1.4)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Latência de dependência simulada (ms)")
        ax.set_ylabel("Ganho do Fastify sobre o Express (%)")
        ax.grid(True, linestyle=":", linewidth=0.6)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "fig_ganho_vs_io.pdf"))
        fig.savefig(os.path.join(outdir, "fig_ganho_vs_io.png"), dpi=300)
        plt.close(fig)

    for endpoint in sorted({r["endpoint"] for r in summary}):
        for io_ms in sorted({r["io_ms"] for r in summary}):
            for metric, label, fname in (
                ("rps_media", "Vazão (requisições por segundo)", "vazao"),
                ("p99_mediana_ms", "Latência P99 (ms)", "p99"),
            ):
                fig, ax = plt.subplots(figsize=(inch, inch * 0.55))
                for arm in sorted({r["arm"] for r in summary}):
                    pts = sorted([(r["concorrencia"], r[metric], r.get("rps_dp", 0))
                                  for r in summary if r["arm"] == arm
                                  and r["endpoint"] == endpoint and r["io_ms"] == io_ms])
                    if not pts:
                        continue
                    xs = [str(p[0]) for p in pts]
                    ys = [p[1] for p in pts]
                    errs = [p[2] for p in pts] if metric == "rps_media" else None
                    ax.errorbar(xs, ys, yerr=errs, marker="o", capsize=3, label=arm, linewidth=1.4)
                ax.set_xlabel("Carga (usuários virtuais simultâneos)")
                ax.set_ylabel(label)
                ax.grid(True, linestyle=":", linewidth=0.6)
                ax.legend(fontsize=7)
                fig.tight_layout()
                out = os.path.join(outdir, f"fig_{fname}_{endpoint}_io{io_ms}.pdf")
                fig.savefig(out)          # PDF vetorial: exigência de qualidade em LaTeX
                fig.savefig(out.replace(".pdf", ".png"), dpi=300)
                plt.close(fig)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
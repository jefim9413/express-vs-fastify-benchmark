# =============================================================================
# 1-setup.sh — prepara o hospedeiro e decide a pinagem de cores.
#
# Escreve bench.env com SERVER_CPU e LOAD_CPUS. Os passos 2 e 3 leem esse
# arquivo, então os números não precisam ser copiados à mão em lugar nenhum.
#
#   bash scripts/1-setup.sh            só diagnostica
#   bash scripts/1-setup.sh --apply    aplica o que é reversível (governor, sysctl)
# =============================================================================
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
SYS=${SYS_OVERRIDE:-/sys/devices/system/cpu}
FAIL=0

ok()   { echo "  [ok]   $*"; }
bad()  { echo "  [!!]   $*"; FAIL=$((FAIL+1)); }
todo() { echo "  [ ]    $*"; }
say()  { echo "         $*"; }

expand() { # "0-3,8" -> lista
  python3 -c '
import sys
o=[]
for p in sys.argv[1].replace(" ","").split(","):
    if "-" in p:
        a,b=p.split("-"); o+=list(range(int(a),int(b)+1))
    elif p: o.append(int(p))
print(" ".join(map(str,sorted(o))))' "$1"
}

echo "=============================================================="
echo " 1. PREPARAÇÃO DO HOSPEDEIRO"
echo "=============================================================="

# ---------------------------------------------------------------- plataforma
echo
echo "-- plataforma --"
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  bad "WSL2: o pinning não chega ao hardware e a rede é virtualizada. Use Linux em metal."
else
  ok "kernel nativo $(uname -r)"
fi
VIRT=$(systemd-detect-virt 2>/dev/null || echo none)
[ "$VIRT" = "none" ] && ok "sem virtualização" || bad "virtualização detectada ($VIRT)"

CG=$(stat -fc %T /sys/fs/cgroup 2>/dev/null)
[ "$CG" = "cgroup2fs" ] && ok "cgroup v2" || bad "cgroup=$CG"

if command -v docker >/dev/null; then
  if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi 'docker desktop'; then
    bad "backend Docker Desktop — use Docker Engine nativo"
  else
    ok "$(docker --version)"
  fi
else
  bad "docker ausente:  curl -fsSL https://get.docker.com | sh"
fi

# ---------------------------------------------------------------- topologia
echo
echo "-- topologia de CPU --"
grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ */         /'

declare -A CCD=()
declare -A FREQ=()
declare -A L3=()
for d in "$SYS"/cpu[0-9]*; do
  c=$(basename "$d" | tr -d 'cpu')
  [ "$(cat "$d/online" 2>/dev/null || echo 1)" = "1" ] || continue
  g=$(cat "$d/cache/index3/shared_cpu_list" 2>/dev/null || echo all)
  CCD[$g]="${CCD[$g]:-} $c"
  FREQ[$c]=$(cat "$d/cpufreq/cpuinfo_max_freq" 2>/dev/null || echo 0)
  L3[$c]=$(cat "$d/cache/index3/size" 2>/dev/null || echo "?")
done

if [ "${#FREQ[@]}" -eq 0 ]; then
  bad "nenhum core encontrado em $SYS — caminho inválido ou /sys inacessível"
  exit 1
fi

NGRP=${#CCD[@]}
NL3=$(printf '%s\n' "${L3[@]}" | sort -u | wc -l)
NFQ=$(printf '%s\n' "${FREQ[@]}" | sort -u | wc -l)

if [ "$NL3" -gt 1 ]; then
  bad "L3 assimétrico — CPU tipo X3D de dois CCDs. O CCD escolhido muda o resultado;"
  say "declare qual usou e rode a análise de sensibilidade (ver README)."
elif [ "$NFQ" -gt 1 ] && [ "$NGRP" -eq 1 ]; then
  say "frequências diferentes com L3 único: arquitetura híbrida (P-core / E-core)."
  say "o core escolhido abaixo é um P-core."
else
  ok "cores homogêneos em L3"
fi

# ------------------------------------------------- escolha do core e do split
# Alvo: core de maior boost, fora do cpu0 (timers e IRQ caem lá por padrão).
TCPU=""; TF=0; TGRP=""
for g in "${!CCD[@]}"; do
  for c in ${CCD[$g]}; do
    [ "$c" = "0" ] && continue
    if [ "${FREQ[$c]}" -gt "$TF" ]; then TF=${FREQ[$c]}; TCPU=$c; TGRP=$g; fi
  done
done
[ -z "$TCPU" ] && TCPU=$(printf '%s\n' "${!FREQ[@]}" | sort -n | tail -1)
SIB=$(cat "$SYS/cpu$TCPU/topology/thread_siblings_list" 2>/dev/null || echo "$TCPU")

echo
echo "-- pinagem --"
if [ "$NGRP" -ge 2 ]; then
  # Gerador nos CCDs restantes: L3 fisicamente separado do alvo.
  LOAD=""
  for g in "${!CCD[@]}"; do [ "$g" = "$TGRP" ] || LOAD="$LOAD,$g"; done
  LOAD="${LOAD#,}"
  ok "$NGRP domínios de L3 — alvo e gerador ficam em CCDs distintos"
  say "alvo    : cpu $TCPU  (CCD [$TGRP], irmão SMT [$SIB] fica ocioso)"
  say "gerador : cpus [$LOAD]  (CCD separado, sem disputa de L3)"
else
  # Um só domínio de L3: sobra excluir o core do alvo e seu irmão SMT.
  ALL=$(expand "0-$(( $(nproc) - 1 ))")
  EXC=" $(expand "$SIB") "
  LOAD=$(for c in $ALL; do case "$EXC" in *" $c "*) ;; *) printf '%s,' "$c";; esac; done)
  LOAD="${LOAD%,}"
  echo "  [!]    um único domínio de L3: alvo e gerador compartilham cache."
  say "é contenção real e não controlada. Declare como limitação, ou use"
  say "duas máquinas. Split possível: alvo cpu $TCPU, gerador [$LOAD]."
fi

# ---------------------------------------------------------------- frequência
echo
echo "-- estabilidade de clock --"
GOV=$(cat "$SYS/cpu$TCPU/cpufreq/scaling_governor" 2>/dev/null || echo "?")
if [ "$GOV" = "performance" ]; then
  ok "governor = performance"
elif [ "$APPLY" = "1" ]; then
  if command -v cpupower >/dev/null; then sudo cpupower frequency-set -g performance >/dev/null 2>&1
  else for f in "$SYS"/cpu*/cpufreq/scaling_governor; do echo performance | sudo tee "$f" >/dev/null 2>&1; done; fi
  ok "governor -> performance"
else
  echo "  [!]    governor = $GOV — o clock oscila entre repetições e infla o CV"
  todo "sudo apt install linux-cpupower && sudo cpupower frequency-set -g performance"
fi
if AC=$(cat /sys/class/power_supply/A*/online 2>/dev/null | head -1); then
  [ "$AC" = "1" ] && ok "na tomada" || bad "na bateria — conecte a fonte"
fi

# ------------------------------------------------------------------- sistema
echo
echo "-- sistema --"
NOF=$(ulimit -n)
[ "$NOF" -ge 65535 ] && ok "ulimit -n = $NOF" || {
  echo "  [!]    ulimit -n = $NOF, insuficiente para 500 conexões"; todo "ulimit -n 65535"; }
if [ "$APPLY" = "1" ]; then
  sudo sysctl -qw net.ipv4.tcp_tw_reuse=1 2>/dev/null && ok "tcp_tw_reuse=1"
else
  todo "sudo sysctl -w net.ipv4.tcp_tw_reuse=1"
fi
LOAD1=$(cut -d' ' -f1 /proc/loadavg)
awk -v l="$LOAD1" 'BEGIN{exit !(l<0.5)}' && ok "load average $LOAD1" \
  || echo "  [!]    load average $LOAD1 — feche tudo antes de coletar"
todo "colete sem ambiente gráfico:  sudo systemctl isolate multi-user.target"
todo "pause atualizações:  sudo systemctl stop unattended-upgrades apt-daily.timer"

# --------------------------------------------------------------- bench.env
cat > bench.env <<EOF
# Gerado por scripts/1-setup.sh em $(date -Is). Não edite à mão sem motivo.
SERVER_CPU=$TCPU
LOAD_CPUS=$LOAD
EOF

echo
echo "-- para o Quadro 3 do Cap. 4 --"
say "CPU    : $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//')"
say "RAM    : $(awk '/MemTotal/{printf "%.1f GB", $2/1048576}' /proc/meminfo)"
say "SO     : $( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -s)"
say "kernel : $(uname -r)"
say "docker : $(docker --version 2>/dev/null | cut -d' ' -f3 | tr -d ,)"

echo
if [ "$FAIL" -gt 0 ]; then
  echo ">> $FAIL problema(s) bloqueante(s). Corrija antes de seguir."
  exit 1
fi
echo ">> bench.env escrito. Próximo: bash scripts/2-verify.sh"
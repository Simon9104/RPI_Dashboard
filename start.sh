#!/bin/bash
# ╔══════════════════════════════════════════════╗
# ║     RPi5 Dashboard — Hlavný script           ║
# ║     Použitie: ./start.sh [príkaz]            ║
# ╚══════════════════════════════════════════════╝

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"
VENV="$SCRIPT_DIR/.venv"
BACKEND_PID="$SCRIPT_DIR/.backend.pid"
FRONTEND_PID="$SCRIPT_DIR/.frontend.pid"
BACKEND_LOG="$SCRIPT_DIR/backend.log"
FRONTEND_LOG="$SCRIPT_DIR/frontend.log"

# ── Farby ──────────────────────────────────────
G='\033[0;32m'; B='\033[0;34m'; A='\033[0;33m'; R='\033[0;31m'; NC='\033[0m'; BOLD='\033[1m'
ok()   { echo -e "${G}  ✓  $1${NC}"; }
info() { echo -e "${B}  ▶  $1${NC}"; }
warn() { echo -e "${A}  ⚠  $1${NC}"; }
err()  { echo -e "${R}  ✕  $1${NC}"; }
h1()   { echo -e "\n${BOLD}${B}$1${NC}"; }

# ── Config helpers ─────────────────────────────
cfg_get() {
  python3 -c "import json,sys; d=json.load(open('$CONFIG')); print(d.get('$1',''))" 2>/dev/null || echo ""
}

cfg_set() {
  python3 - <<EOF
import json, os
path = '$CONFIG'
d = json.load(open(path)) if os.path.exists(path) else {}
d['$1'] = '$2'
json.dump(d, open(path, 'w'), indent=2)
EOF
}

# ── PID helpers ────────────────────────────────
is_running() {
  local f="$1"
  [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null
}

stop_proc() {
  local f="$1" name="$2"
  if is_running "$f"; then
    kill "$(cat "$f")" 2>/dev/null && ok "$name zastavený"
    rm -f "$f"
  else
    warn "$name nebeží"
  fi
}

port_free() {
  ! lsof -i ":$1" &>/dev/null 2>&1
}

get_ip() { hostname -I | awk '{print $1}'; }

# ═══════════════════════════════════════════════
#  PRVÉ SPUSTENIE — SETUP WIZARD
# ═══════════════════════════════════════════════
first_run_setup() {
  clear
  echo -e "${BOLD}"
  echo "  ╔════════════════════════════════════════╗"
  echo "  ║         RPi5 Dashboard Setup           ║"
  echo "  ║         Prvé spustenie                 ║"
  echo "  ╚════════════════════════════════════════╝"
  echo -e "${NC}"
  echo -e "  Vitaj! Nakonfigurujeme dashboard za pár sekúnd.\n"

  # ── Backend port ─────────────────────────────
  while true; do
    read -p "  Port pre backend API    [predvolený: 5001]: " BPORT
    BPORT="${BPORT:-5001}"
    if [[ "$BPORT" =~ ^[0-9]+$ ]] && (( BPORT > 1024 && BPORT < 65535 )); then
      if port_free "$BPORT"; then
        ok "Backend port: $BPORT"; break
      else
        warn "Port $BPORT je obsadený, skús iný."
      fi
    else
      warn "Zadaj platné číslo portu (1025–65534)."
    fi
  done

  # ── Frontend port ─────────────────────────────
  while true; do
    read -p "  Port pre frontend web   [predvolený: 8080]: " FPORT
    FPORT="${FPORT:-8080}"
    if [[ "$FPORT" =~ ^[0-9]+$ ]] && (( FPORT > 1024 && FPORT < 65535 )); then
      if [[ "$FPORT" == "$BPORT" ]]; then
        warn "Frontend a backend nemôžu mať rovnaký port."; continue
      fi
      if port_free "$FPORT"; then
        ok "Frontend port: $FPORT"; break
      else
        warn "Port $FPORT je obsadený, skús iný."
      fi
    else
      warn "Zadaj platné číslo portu (1025–65534)."
    fi
  done

  # ── Anthropic API kľúč (voliteľné) ───────────
  echo ""
  echo -e "  ${A}AI Chat (voliteľné)${NC}"
  echo -e "  Získaj kľúč na: https://console.anthropic.com"
  read -p "  Anthropic API kľúč [Enter = preskočiť]: " AKEY

  # ── Ulož config ───────────────────────────────
  python3 - <<EOF
import json
cfg = {
  "backend_port":  int("$BPORT"),
  "frontend_port": int("$FPORT"),
  "anthropic_key": "$AKEY",
  "setup_done":    True
}
json.dump(cfg, open("$CONFIG", "w"), indent=2)
EOF

  # ── Vlož port do HTML ─────────────────────────
  sed -i "s|window\.__BACKEND_PORT__ || 5001|window.__BACKEND_PORT__ = $BPORT; //|g" \
    "$SCRIPT_DIR/dashboard.html" 2>/dev/null || true
  # Priamy inject
  sed -i "s|const BACKEND_PORT = window.__BACKEND_PORT__ || 5001;|const BACKEND_PORT = $BPORT;|g" \
    "$SCRIPT_DIR/dashboard.html"

  echo ""
  ok "Konfigurácia uložená do config.json"
  echo ""
}

# ═══════════════════════════════════════════════
#  INŠTALÁCIA ZÁVISLOSTÍ
# ═══════════════════════════════════════════════
install_deps() {
  h1 "Inštalácia závislostí"

  # Python venv
  if [[ ! -d "$VENV" ]]; then
    info "Vytváram Python virtual environment..."
    python3 -m venv "$VENV"
    ok "venv vytvorený"
  else
    ok "venv existuje"
  fi

  info "Inštalujem Python balíky..."
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet flask flask-cors psutil
  ok "flask, flask-cors, psutil nainštalované"

  # speedtest voliteľný
  "$VENV/bin/pip" install --quiet speedtest-cli 2>/dev/null \
    && ok "speedtest-cli nainštalovaný" \
    || warn "speedtest-cli sa nepodarilo (voliteľné)"

  # Sudo pravidlá
  info "Nastavujem sudo pravidlá..."
  local USER=$(whoami)
  local RULE="$USER ALL=(ALL) NOPASSWD: /usr/bin/wg, /usr/bin/wg-quick, /usr/sbin/ufw, /usr/bin/nmcli, /usr/bin/systemctl, /sbin/reboot, /sbin/shutdown"
  local SUDO_FILE="/etc/sudoers.d/rpi5-dashboard"
  if [[ ! -f "$SUDO_FILE" ]]; then
    echo "$RULE" | sudo tee "$SUDO_FILE" > /dev/null
    sudo chmod 440 "$SUDO_FILE"
    ok "Sudo pravidlá nastavené"
  else
    ok "Sudo pravidlá existujú"
  fi

  touch "$SCRIPT_DIR/.installed"
  ok "Inštalácia hotová"
}

# ═══════════════════════════════════════════════
#  SPUSTENIE
# ═══════════════════════════════════════════════
start_dashboard() {
  local BPORT=$(cfg_get backend_port)
  local FPORT=$(cfg_get frontend_port)
  BPORT="${BPORT:-5001}"
  FPORT="${FPORT:-8080}"
  local IP=$(get_ip)

  h1 "Spúšťam RPi5 Dashboard"

  # Zastav ak beží
  if is_running "$BACKEND_PID" || is_running "$FRONTEND_PID"; then
    warn "Dashboard beží, reštartujem..."
    stop_proc "$BACKEND_PID" "Backend"
    stop_proc "$FRONTEND_PID" "Frontend"
    sleep 1
  fi

  # Skontroluj porty
  for p in "$BPORT" "$FPORT"; do
    if ! port_free "$p"; then
      err "Port $p je obsadený!"
      warn "Spusti './start.sh setup' pre zmenu portov."
      exit 1
    fi
  done

  # Backend
  info "Spúšťam backend (port $BPORT)..."
  cd "$SCRIPT_DIR"
  nohup "$VENV/bin/python3" backend.py > "$BACKEND_LOG" 2>&1 &
  echo $! > "$BACKEND_PID"
  sleep 2

  if is_running "$BACKEND_PID"; then
    ok "Backend beží (PID $(cat "$BACKEND_PID"))"
  else
    err "Backend sa nepodarilo spustiť! Logy:"
    tail -20 "$BACKEND_LOG"
    exit 1
  fi

  # Frontend
  info "Spúšťam frontend HTTP server (port $FPORT)..."
  nohup python3 -m http.server "$FPORT" > "$FRONTEND_LOG" 2>&1 &
  echo $! > "$FRONTEND_PID"
  sleep 1

  if is_running "$FRONTEND_PID"; then
    ok "Frontend beží (PID $(cat "$FRONTEND_PID"))"
  else
    err "Frontend sa nepodarilo spustiť!"
    exit 1
  fi

  # Výsledok
  echo ""
  echo -e "${BOLD}${G}"
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║          Dashboard beží!                     ║"
  echo "  ╠══════════════════════════════════════════════╣"
  printf "  ║  Otvor:  http://%-28s║\n" "${IP}:${FPORT}/dashboard.html"
  printf "  ║  API:    http://%-28s║\n" "${IP}:${BPORT}/health"
  echo "  ╚══════════════════════════════════════════════╝"
  echo -e "${NC}"
  echo -e "  Zastaviť:    ${A}./start.sh stop${NC}"
  echo -e "  Logy:        ${A}./start.sh logs${NC}"
  echo -e "  Autoštart:   ${A}./start.sh autostart${NC}"
  echo ""
}

# ═══════════════════════════════════════════════
#  AUTOŠTART (systemd)
# ═══════════════════════════════════════════════
setup_autostart() {
  local BPORT=$(cfg_get backend_port); BPORT="${BPORT:-5001}"
  local FPORT=$(cfg_get frontend_port); FPORT="${FPORT:-8080}"
  local USER=$(whoami)

  h1 "Nastavujem autoštart (systemd)"

  sudo tee /etc/systemd/system/rpi5-backend.service > /dev/null <<EOF
[Unit]
Description=RPi5 Dashboard Backend
After=network.target

[Service]
ExecStart=$VENV/bin/python3 $SCRIPT_DIR/backend.py
WorkingDirectory=$SCRIPT_DIR
User=$USER
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo tee /etc/systemd/system/rpi5-frontend.service > /dev/null <<EOF
[Unit]
Description=RPi5 Dashboard Frontend
After=network.target

[Service]
ExecStart=/usr/bin/python3 -m http.server $FPORT
WorkingDirectory=$SCRIPT_DIR
User=$USER
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable rpi5-backend rpi5-frontend
  sudo systemctl start  rpi5-backend rpi5-frontend

  ok "Autoštart nastavený"
  ok "Dashboard sa spustí automaticky pri každom boote"
}

# ═══════════════════════════════════════════════
#  STATUS
# ═══════════════════════════════════════════════
show_status() {
  local BPORT=$(cfg_get backend_port); BPORT="${BPORT:-5001}"
  local FPORT=$(cfg_get frontend_port); FPORT="${FPORT:-8080}"
  local IP=$(get_ip)
  echo ""
  echo -e "${BOLD}  RPi5 Dashboard — Stav${NC}"
  echo "  ─────────────────────────────"
  if is_running "$BACKEND_PID"; then
    echo -e "  Backend:   ${G}● beží${NC}  (PID $(cat "$BACKEND_PID"), :$BPORT)"
  else
    echo -e "  Backend:   ${R}● zastavený${NC}"
  fi
  if is_running "$FRONTEND_PID"; then
    echo -e "  Frontend:  ${G}● beží${NC}  (PID $(cat "$FRONTEND_PID"), :$FPORT)"
    echo -e "  URL:       ${G}http://${IP}:${FPORT}/dashboard.html${NC}"
  else
    echo -e "  Frontend:  ${R}● zastavený${NC}"
  fi
  echo ""
}

# ═══════════════════════════════════════════════
#  UNINSTALL
# ═══════════════════════════════════════════════
do_uninstall() {
  warn "Odinštalovávam RPi5 Dashboard..."
  stop_proc "$BACKEND_PID" "Backend"
  stop_proc "$FRONTEND_PID" "Frontend"
  sudo systemctl disable rpi5-backend rpi5-frontend 2>/dev/null || true
  sudo rm -f /etc/systemd/system/rpi5-backend.service \
             /etc/systemd/system/rpi5-frontend.service \
             /etc/sudoers.d/rpi5-dashboard
  sudo systemctl daemon-reload
  rm -rf "$VENV" "$SCRIPT_DIR/.installed"
  ok "Odinštalované. Súbory v $SCRIPT_DIR zostali zachované."
}

# ═══════════════════════════════════════════════
#  HELP
# ═══════════════════════════════════════════════
show_help() {
  echo ""
  echo -e "${BOLD}  RPi5 Dashboard${NC}  —  Použitie: ./start.sh [príkaz]"
  echo ""
  echo "  (bez príkazu)   Spustí dashboard (inštaluje ak treba)"
  echo "  setup           Znova spustí setup wizard (zmena portov)"
  echo "  start           Spustí dashboard"
  echo "  stop            Zastaví dashboard"
  echo "  restart         Reštartuje dashboard"
  echo "  status          Zobrazí stav"
  echo "  logs            Zobrazí logy"
  echo "  autostart       Nastaví autoštart pri boote"
  echo "  uninstall       Odinštaluje dashboard"
  echo "  help            Táto nápoveda"
  echo ""
}

# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════
case "${1:-}" in
  setup)
    first_run_setup
    ;;
  install)
    install_deps
    ;;
  start)
    start_dashboard
    ;;
  stop)
    h1 "Zastavujem dashboard"
    stop_proc "$BACKEND_PID"  "Backend"
    stop_proc "$FRONTEND_PID" "Frontend"
    ;;
  restart)
    h1 "Reštartujem dashboard"
    stop_proc "$BACKEND_PID"  "Backend"
    stop_proc "$FRONTEND_PID" "Frontend"
    sleep 1
    start_dashboard
    ;;
  status)
    show_status
    ;;
  logs)
    echo -e "${B}── Backend logy (posledných 30 riadkov) ──${NC}"
    tail -30 "$BACKEND_LOG"  2>/dev/null || warn "Žiadne logy"
    echo -e "${B}── Frontend logy ──${NC}"
    tail -10 "$FRONTEND_LOG" 2>/dev/null || warn "Žiadne logy"
    ;;
  autostart)
    setup_autostart
    ;;
  uninstall)
    do_uninstall
    ;;
  help|--help|-h)
    show_help
    ;;
  *)
    # Prvé spustenie — spýtaj sa na porty
    if [[ ! -f "$CONFIG" ]] || ! python3 -c "import json; d=json.load(open('$CONFIG')); assert d.get('setup_done')" 2>/dev/null; then
      first_run_setup
      install_deps
    elif [[ ! -f "$SCRIPT_DIR/.installed" ]]; then
      install_deps
    fi
    start_dashboard
    ;;
esac

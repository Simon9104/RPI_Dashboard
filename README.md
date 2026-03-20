# RPi5 Dashboard

Moderný webový dashboard pre Raspberry Pi 5 — monitoring, sieť, VPN, AI asistent.

![Dashboard Preview](https://img.shields.io/badge/RPi5-Dashboard-green?style=for-the-badge&logo=raspberry-pi)
![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=for-the-badge)

## Funkcie

- **Prehľad** — CPU teplota, záťaž, RAM, disk, živý graf, top procesy
- **Sieť** — konfigurácia eth0 (DHCP / statická IP), firewall (UFW), ping, traceroute
- **Speed Test** — meranie rýchlosti internetu cez speedtest-cli
- **VPN** — správa WireGuard (connect/disconnect, kill switch, live štatistiky)
- **AI Chat** — Claude AI asistent s kontextom aktuálneho stavu RPi
- **Systém** — CPU jadrá, správa systemd služieb, reštart / vypnutie

## Rýchla inštalácia

```bash
git clone https://github.com/TVOJE_MENO/rpi5-dashboard.git
cd rpi5-dashboard
chmod +x start.sh
./start.sh
```

Pri prvom spustení sa spustí setup wizard — spýta sa na porty a API kľúč.

## Požiadavky

- Raspberry Pi 5 (alebo iný Linux)
- Python 3.11+
- Internetové pripojenie (pre inštaláciu závislostí)

## Súbory

```
rpi5-dashboard/
├── start.sh          ← Hlavný script (spustenie, setup, autoštart)
├── backend.py        ← Flask REST API (čítanie systémových dát)
├── dashboard.html    ← Frontend (HTML/CSS/JS, bez frameworku)
├── config.json       ← Generuje sa pri prvom spustení (porty, API kľúč)
└── README.md
```

## Príkazy

```bash
./start.sh            # Spustí dashboard (prvýkrát spustí setup)
./start.sh setup      # Znova nakonfiguruje porty a API kľúč
./start.sh stop       # Zastaví dashboard
./start.sh restart    # Reštartuje dashboard
./start.sh status     # Zobrazí stav a URL
./start.sh logs       # Zobrazí logy backendu
./start.sh autostart  # Nastaví autoštart pri boote (systemd)
./start.sh uninstall  # Odinštaluje dashboard
```

## AI Chat

AI Chat využíva [Claude API od Anthropic](https://console.anthropic.com).  
API kľúč zadáš pri prvom spustení (setup wizard) alebo neskôr v `config.json`:

```json
{
  "backend_port": 5001,
  "frontend_port": 8080,
  "anthropic_key": "sk-ant-..."
}
```

> ⚠️ Dashboard je určený pre lokálnu sieť. Nevystavuj ho priamo na internet bez autentifikácie.

## Licencia

MIT

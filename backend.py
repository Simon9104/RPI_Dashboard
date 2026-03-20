#!/usr/bin/env python3
"""
RPi5 Dashboard — Backend
Načíta konfiguráciu z config.json (generuje sa pri prvom spustení cez start.sh)
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import psutil, subprocess, os, re, json, time
from datetime import datetime, timedelta

app = Flask(__name__)

# ── Načítaj config ─────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"backend_port": 5001, "frontend_port": 8080}

config = load_config()
CORS(app, origins=[f"http://localhost:{config.get('frontend_port', 8080)}",
                   f"http://127.0.0.1:{config.get('frontend_port', 8080)}",
                   "*"])

# ── Helpers ────────────────────────────────────
def run_cmd(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def cpu_temp():
    try:
        out = run_cmd(["vcgencmd", "measure_temp"])
        m = re.search(r"temp=([\d.]+)", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return 0.0

def cpu_freq_mhz():
    try:
        freq = psutil.cpu_freq()
        if freq:
            return int(freq.current)
    except Exception:
        pass
    try:
        out = run_cmd(["vcgencmd", "measure_clock", "arm"])
        m = re.search(r"=(\d+)", out)
        if m:
            return int(int(m.group(1)) / 1_000_000)
    except Exception:
        pass
    return 0

def get_disk_info():
    try:
        u = psutil.disk_usage("/")
        return {
            "total_gb": round(u.total / 1e9, 1),
            "used_gb":  round(u.used  / 1e9, 1),
            "free_gb":  round(u.free  / 1e9, 1),
            "percent":  u.percent,
        }
    except Exception:
        return {}

_prev_net = {}
def get_net_speed():
    global _prev_net
    cur = psutil.net_io_counters(pernic=True)
    now = time.time()
    result = {}
    for iface, s in cur.items():
        if iface in _prev_net:
            dt = now - _prev_net[iface]["t"]
            rx = round((s.bytes_recv - _prev_net[iface]["rx"]) / dt / 1024, 1)
            tx = round((s.bytes_sent - _prev_net[iface]["tx"]) / dt / 1024, 1)
            result[iface] = {"rx_kbs": max(rx, 0), "tx_kbs": max(tx, 0)}
        else:
            result[iface] = {"rx_kbs": 0, "tx_kbs": 0}
        _prev_net[iface] = {"rx": s.bytes_recv, "tx": s.bytes_sent, "t": now}
    return result

def get_ip_info():
    result = {}
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for iface, addr_list in addrs.items():
            ipv4 = next((a.address for a in addr_list if a.family.name == "AF_INET"), None)
            up    = stats[iface].isup  if iface in stats else False
            speed = stats[iface].speed if iface in stats else 0
            result[iface] = {"ip": ipv4, "up": up, "speed_mbps": speed}
    except Exception:
        pass
    try:
        gw = run_cmd(["ip", "route", "show", "default"])
        m = re.search(r"default via ([\d.]+)", gw)
        result["_gateway"] = m.group(1) if m else None
    except Exception:
        result["_gateway"] = None
    return result

def get_vpn_status():
    try:
        out = run_cmd(["sudo", "wg", "show"])
        if "interface:" in out:
            connected = "latest handshake" in out
            m_ep = re.search(r"endpoint: ([\S]+)", out)
            m_rx = re.search(r"transfer: ([\d.]+) \w+ received", out)
            m_tx = re.search(r"([\d.]+) \w+ sent", out)
            return {
                "connected": connected,
                "endpoint":  m_ep.group(1) if m_ep else None,
                "rx_mb":     float(m_rx.group(1)) if m_rx else 0,
                "tx_mb":     float(m_tx.group(1)) if m_tx else 0,
                "raw":       out,
            }
        return {"connected": False, "endpoint": None, "rx_mb": 0, "tx_mb": 0}
    except Exception as e:
        return {"connected": False, "error": str(e)}

def get_top_processes(n=8):
    procs = []
    for p in psutil.process_iter(["pid","name","cpu_percent","memory_percent","status"]):
        try:
            procs.append({
                "pid":    p.info["pid"],
                "name":   p.info["name"],
                "cpu":    round(p.info["cpu_percent"], 1),
                "ram":    round(p.info["memory_percent"], 1),
                "status": p.info["status"],
            })
        except Exception:
            pass
    return sorted(procs, key=lambda x: x["cpu"], reverse=True)[:n]

def get_services():
    names = ["ssh","nginx","cron","wg-quick@wg0","bluetooth","avahi-daemon","ufw"]
    result = []
    for svc in names:
        active = run_cmd(["systemctl","is-active", svc]).strip() == "active"
        pid    = run_cmd(["systemctl","show", svc,"--property=MainPID","--value"]).strip()
        result.append({"name": svc, "active": active, "pid": pid if pid != "0" else "—"})
    return result

# ── Endpoints ──────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat(),
                    "backend_port": config.get("backend_port")})

@app.route("/api/stats")
def stats():
    mem  = psutil.virtual_memory()
    up   = int(time.time() - psutil.boot_time())
    return jsonify({
        "cpu": {
            "temp":          cpu_temp(),
            "load_percent":  psutil.cpu_percent(interval=0.2),
            "load_per_core": psutil.cpu_percent(interval=0.2, percpu=True),
            "freq_mhz":      cpu_freq_mhz(),
            "count":         psutil.cpu_count(),
        },
        "ram": {
            "total_mb": round(mem.total    / 1e6),
            "used_mb":  round(mem.used     / 1e6),
            "free_mb":  round(mem.available/ 1e6),
            "percent":  mem.percent,
        },
        "disk":       get_disk_info(),
        "uptime":     str(timedelta(seconds=up)),
        "uptime_sec": up,
        "timestamp":  datetime.now().isoformat(),
    })

@app.route("/api/network")
def network():
    return jsonify({
        "interfaces": get_ip_info(),
        "speed":      get_net_speed(),
    })

@app.route("/api/network/config", methods=["POST"])
def network_config():
    d     = request.get_json() or {}
    iface = d.get("iface", "eth0")
    mode  = d.get("mode", "dhcp")
    if mode == "dhcp":
        cmds = [["sudo","nmcli","con","mod", iface,"ipv4.method","auto"],
                ["sudo","nmcli","con","up",  iface]]
    else:
        ip, mask = d.get("ip",""), d.get("mask","24")
        gw,  dns = d.get("gw",""), d.get("dns","1.1.1.1")
        cmds = [["sudo","nmcli","con","mod", iface,
                  "ipv4.method","manual",
                  "ipv4.addresses", f"{ip}/{mask}",
                  "ipv4.gateway",   gw,
                  "ipv4.dns",       dns],
                ["sudo","nmcli","con","up", iface]]
    out = "\n".join(run_cmd(c) for c in cmds)
    return jsonify({"ok": True, "output": out})

@app.route("/api/vpn/status")
def vpn_status():
    return jsonify(get_vpn_status())

@app.route("/api/vpn/connect", methods=["POST"])
def vpn_connect():
    iface = (request.get_json() or {}).get("iface","wg0")
    out   = run_cmd(["sudo","wg-quick","up", iface], timeout=15)
    return jsonify({"ok": "ERROR" not in out, "output": out})

@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    iface = (request.get_json() or {}).get("iface","wg0")
    out   = run_cmd(["sudo","wg-quick","down", iface], timeout=10)
    return jsonify({"ok": "ERROR" not in out, "output": out})

@app.route("/api/vpn/killswitch", methods=["POST"])
def vpn_killswitch():
    cmds = [
        ["sudo","ufw","default","deny","outgoing"],
        ["sudo","ufw","allow","out","on","wg0"],
        ["sudo","ufw","allow","51820/udp"],
        ["sudo","ufw","reload"],
    ]
    out = "\n".join(run_cmd(c) for c in cmds)
    return jsonify({"ok": True, "output": out})

@app.route("/api/processes")
def processes():
    return jsonify(get_top_processes())

@app.route("/api/services")
def services():
    return jsonify(get_services())

@app.route("/api/service/<name>/<action>", methods=["POST"])
def service_action(name, action):
    if action not in ("start","stop","restart","enable","disable"):
        return jsonify({"ok": False, "error": "Neplatná akcia"}), 400
    allowed = {"ssh","nginx","cron","wg-quick@wg0","bluetooth","avahi-daemon","ufw"}
    if name not in allowed:
        return jsonify({"ok": False, "error": "Služba nie je povolená"}), 403
    out = run_cmd(["sudo","systemctl", action, name])
    return jsonify({"ok": True, "output": out})

@app.route("/api/ping", methods=["POST"])
def ping():
    d    = request.get_json() or {}
    host = d.get("host","8.8.8.8")
    cnt  = min(int(d.get("count",4)), 10)
    if not re.match(r'^[\w.\-]+$', host):
        return jsonify({"ok": False, "error": "Neplatný hostiteľ"}), 400
    out = run_cmd(["ping","-c",str(cnt),"-W","2", host], timeout=30)
    return jsonify({"ok": True, "output": out})

@app.route("/api/traceroute", methods=["POST"])
def traceroute():
    host = (request.get_json() or {}).get("host","8.8.8.8")
    if not re.match(r'^[\w.\-]+$', host):
        return jsonify({"ok": False, "error": "Neplatný hostiteľ"}), 400
    out = run_cmd(["traceroute","-m","15", host], timeout=30)
    return jsonify({"ok": True, "output": out})

@app.route("/api/speedtest", methods=["POST"])
def speedtest():
    try:
        import speedtest as st
        s = st.Speedtest()
        s.get_best_server()
        return jsonify({
            "ok":       True,
            "ping_ms":  round(s.results.ping, 1),
            "down_mbps": round(s.download() / 1e6, 2),
            "up_mbps":  round(s.upload()   / 1e6, 2),
        })
    except ImportError:
        return jsonify({"ok": False, "error": "Spusti: pip install speedtest-cli"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/firewall/status")
def firewall_status():
    out    = run_cmd(["sudo","ufw","status","verbose"])
    active = "Status: active" in out
    return jsonify({"active": active, "output": out})

@app.route("/api/firewall/toggle", methods=["POST"])
def firewall_toggle():
    enable = (request.get_json() or {}).get("enable", True)
    out    = run_cmd(["sudo","ufw","--force","enable" if enable else "disable"])
    return jsonify({"ok": True, "output": out})

@app.route("/api/system/reboot", methods=["POST"])
def reboot():
    if not (request.get_json() or {}).get("confirm"):
        return jsonify({"ok": False, "error": "Vyžaduje sa confirm: true"}), 400
    subprocess.Popen(["sudo","reboot"])
    return jsonify({"ok": True})

@app.route("/api/system/shutdown", methods=["POST"])
def shutdown():
    if not (request.get_json() or {}).get("confirm"):
        return jsonify({"ok": False, "error": "Vyžaduje sa confirm: true"}), 400
    subprocess.Popen(["sudo","shutdown","-h","now"])
    return jsonify({"ok": True})

# ── Run ────────────────────────────────────────
if __name__ == "__main__":
    port = config.get("backend_port", 5001)
    print(f"  Backend beží na porte {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

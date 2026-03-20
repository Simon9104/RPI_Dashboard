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
    """
    Meranie rýchlosti cez stiahnutie testovacieho súboru z Cloudflare CDN.
    Nevyžaduje speedtest-cli, funguje spoľahlivo bez 403.
    """
    import urllib.request, time, threading

    results = {"ok": False, "ping_ms": 0, "down_mbps": 0, "up_mbps": 0}

    # ── Ping ──────────────────────────────────────
    try:
        t0 = time.time()
        urllib.request.urlopen("https://1.1.1.1", timeout=3)
        results["ping_ms"] = round((time.time() - t0) * 1000, 1)
    except Exception:
        try:
            out = run_cmd(["ping", "-c", "3", "-W", "1", "1.1.1.1"])
            m = re.search(r"avg.*?([\d.]+)", out)
            results["ping_ms"] = float(m.group(1)) if m else 0
        except Exception:
            results["ping_ms"] = 0

    # ── Download — Cloudflare 100MB test súbor ────
    try:
        url = "https://speed.cloudflare.com/__down?bytes=10000000"  # 10 MB
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; RPi5Dashboard/1.0)"
        })
        t0    = time.time()
        total = 0
        with urllib.request.urlopen(req, timeout=20) as r:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
        elapsed = time.time() - t0
        results["down_mbps"] = round((total * 8) / elapsed / 1e6, 2)
    except Exception as e:
        results["down_mbps"] = 0

    # ── Upload — POST na Cloudflare speed endpoint ─
    try:
        data = b"x" * 2_000_000  # 2 MB
        req  = urllib.request.Request(
            "https://speed.cloudflare.com/__up",
            data=data,
            headers={
                "Content-Type":   "application/octet-stream",
                "User-Agent":     "Mozilla/5.0 (compatible; RPi5Dashboard/1.0)",
                "Content-Length": str(len(data)),
            },
            method="POST"
        )
        t0 = time.time()
        urllib.request.urlopen(req, timeout=20)
        elapsed = time.time() - t0
        results["up_mbps"] = round((len(data) * 8) / elapsed / 1e6, 2)
    except Exception:
        results["up_mbps"] = 0

    results["ok"] = results["down_mbps"] > 0
    if not results["ok"]:
        results["error"] = "Nepodarilo sa zmerať rýchlosť. Skontroluj internetové pripojenie."
    return jsonify(results)

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

# ── Logy ───────────────────────────────────────

@app.route("/api/logs/<source>")
def get_logs(source):
    """Živé logy. source: system | nginx | auth | syslog"""
    sources = {
        "system": ["journalctl", "-n", "80", "--no-pager", "-o", "short"],
        "nginx":  ["journalctl", "-u", "nginx", "-n", "60", "--no-pager"],
        "auth":   ["journalctl", "-u", "ssh",   "-n", "60", "--no-pager"],
        "syslog": ["tail", "-n", "80", "/var/log/syslog"],
    }
    if source not in sources:
        return jsonify({"ok": False, "error": "Neznámy zdroj"}), 400
    out = run_cmd(sources[source], timeout=8)
    lines = out.split("\n")
    return jsonify({"ok": True, "source": source, "lines": lines})

# ── Docker ─────────────────────────────────────

@app.route("/api/docker/containers")
def docker_containers():
    out = run_cmd(["docker", "ps", "-a",
                   "--format", "{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}"],
                  timeout=8)
    if "ERROR" in out or "command not found" in out:
        return jsonify({"ok": False, "error": "Docker nie je nainštalovaný alebo nebeží", "containers": []})
    containers = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            containers.append({
                "id":     parts[0][:12],
                "name":   parts[1],
                "status": parts[2],
                "image":  parts[3],
                "ports":  parts[4] if len(parts) > 4 else "",
                "running": "Up" in parts[2],
            })
    return jsonify({"ok": True, "containers": containers})

@app.route("/api/docker/<action>/<container_id>", methods=["POST"])
def docker_action(action, container_id):
    if action not in ("start", "stop", "restart", "remove"):
        return jsonify({"ok": False, "error": "Neplatná akcia"}), 400
    cmd = ["docker", action, container_id]
    out = run_cmd(cmd, timeout=15)
    return jsonify({"ok": "ERROR" not in out, "output": out})

# ── vcgencmd rozšírenie (RPi-špecifické) ────────

@app.route("/api/rpi/hardware")
def rpi_hardware():
    """Podrobné RPi5 hardvérové info cez vcgencmd."""
    def vcg(args):
        return run_cmd(["vcgencmd"] + args)

    throttled_raw = vcg(["get_throttled"])
    throttled_val = 0
    m = re.search(r"0x([0-9a-fA-F]+)", throttled_raw)
    if m:
        throttled_val = int(m.group(1), 16)

    throttle_flags = {
        "undervoltage_now":       bool(throttled_val & 0x1),
        "freq_capped_now":        bool(throttled_val & 0x2),
        "throttled_now":          bool(throttled_val & 0x4),
        "soft_temp_limit_now":    bool(throttled_val & 0x8),
        "undervoltage_occurred":  bool(throttled_val & 0x10000),
        "freq_capped_occurred":   bool(throttled_val & 0x20000),
        "throttled_occurred":     bool(throttled_val & 0x40000),
    }

    def parse_volt(s):
        m = re.search(r"([\d.]+)V", s)
        return float(m.group(1)) if m else 0.0

    def parse_clock(s):
        m = re.search(r"=(\d+)", s)
        return int(int(m.group(1)) / 1_000_000) if m else 0

    return jsonify({
        "temp_cpu":       cpu_temp(),
        "temp_pmic":      float(re.search(r"temp=([\d.]+)", vcg(["measure_temp", "pmic"])).group(1)) if re.search(r"temp=([\d.]+)", vcg(["measure_temp", "pmic"])) else 0,
        "volt_core":      parse_volt(vcg(["measure_volts", "core"])),
        "volt_sdram":     parse_volt(vcg(["measure_volts", "sdram_c"])),
        "clock_arm_mhz":  parse_clock(vcg(["measure_clock", "arm"])),
        "clock_core_mhz": parse_clock(vcg(["measure_clock", "core"])),
        "clock_v3d_mhz":  parse_clock(vcg(["measure_clock", "v3d"])),
        "throttle_flags": throttle_flags,
        "throttled_hex":  throttled_raw,
    })

# ── Uptime monitoring ───────────────────────────

_uptime_targets = []
_uptime_results = {}

@app.route("/api/uptime/targets", methods=["GET"])
def uptime_targets():
    return jsonify({"targets": _uptime_targets, "results": _uptime_results})

@app.route("/api/uptime/targets", methods=["POST"])
def add_uptime_target():
    d    = request.get_json() or {}
    name = d.get("name", "")
    url  = d.get("url", "")
    if not name or not url:
        return jsonify({"ok": False, "error": "Chýba name alebo url"}), 400
    if not any(t["url"] == url for t in _uptime_targets):
        _uptime_targets.append({"name": name, "url": url})
    return jsonify({"ok": True})

@app.route("/api/uptime/targets/<int:idx>", methods=["DELETE"])
def delete_uptime_target(idx):
    if 0 <= idx < len(_uptime_targets):
        removed = _uptime_targets.pop(idx)
        _uptime_results.pop(removed["url"], None)
    return jsonify({"ok": True})

@app.route("/api/uptime/check")
def uptime_check():
    """Skontroluje všetky monitorované URL."""
    import urllib.request as ur
    results = {}
    for t in _uptime_targets:
        url = t["url"]
        try:
            req = ur.Request(url, headers={"User-Agent": "RPi5Dashboard/1.0"})
            t0  = time.time()
            with ur.urlopen(req, timeout=5) as r:
                code    = r.status
                latency = round((time.time() - t0) * 1000, 1)
                results[url] = {"up": True,  "code": code, "latency_ms": latency}
        except Exception as e:
            results[url] = {"up": False, "code": 0,    "latency_ms": 0, "error": str(e)}
    _uptime_results.update(results)
    return jsonify({"ok": True, "results": results})

# ── Záložky (Service Launcher) ──────────────────

_bookmarks = []

@app.route("/api/bookmarks", methods=["GET"])
def get_bookmarks():
    return jsonify({"bookmarks": _bookmarks})

@app.route("/api/bookmarks", methods=["POST"])
def add_bookmark():
    d = request.get_json() or {}
    _bookmarks.append({
        "name": d.get("name", "Nová záložka"),
        "url":  d.get("url", ""),
        "icon": d.get("icon", "◈"),
        "color": d.get("color", "green"),
    })
    return jsonify({"ok": True, "bookmarks": _bookmarks})

@app.route("/api/bookmarks/<int:idx>", methods=["DELETE"])
def delete_bookmark(idx):
    if 0 <= idx < len(_bookmarks):
        _bookmarks.pop(idx)
    return jsonify({"ok": True})

# ── Výstrahy (Alerts) ───────────────────────────

_alert_config = {
    "cpu_temp_warn":  70.0,
    "cpu_temp_crit":  80.0,
    "cpu_load_warn":  80.0,
    "ram_warn":       85.0,
    "disk_warn":      90.0,
}

@app.route("/api/alerts/config", methods=["GET"])
def get_alert_config():
    return jsonify(_alert_config)

@app.route("/api/alerts/config", methods=["POST"])
def set_alert_config():
    d = request.get_json() or {}
    _alert_config.update({k: float(v) for k, v in d.items() if k in _alert_config})
    return jsonify({"ok": True, "config": _alert_config})

@app.route("/api/alerts/check")
def check_alerts():
    """Skontroluje aktuálny stav voči prahom."""
    alerts = []
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    temp = cpu_temp()
    load = psutil.cpu_percent(interval=0.1)

    if temp >= _alert_config["cpu_temp_crit"]:
        alerts.append({"level": "critical", "msg": f"CPU teplota kritická: {temp}°C"})
    elif temp >= _alert_config["cpu_temp_warn"]:
        alerts.append({"level": "warning",  "msg": f"CPU teplota vysoká: {temp}°C"})
    if load >= _alert_config["cpu_load_warn"]:
        alerts.append({"level": "warning",  "msg": f"CPU záťaž vysoká: {load:.0f}%"})
    if mem.percent >= _alert_config["ram_warn"]:
        alerts.append({"level": "warning",  "msg": f"RAM takmer plná: {mem.percent:.0f}%"})
    if disk.percent >= _alert_config["disk_warn"]:
        alerts.append({"level": "critical", "msg": f"Disk takmer plný: {disk.percent:.0f}%"})

    return jsonify({"ok": True, "alerts": alerts, "count": len(alerts)})

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

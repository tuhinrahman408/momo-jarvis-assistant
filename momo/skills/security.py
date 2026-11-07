"""Cybersecurity assistant for YOUR OWN computer and YOUR OWN home network.
Everything here is read-only monitoring. The network scan cannot be pointed at any other target:
it only looks at the private /24 subnet that this computer is connected to."""
import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .. import config
from .registry import skill

IS_WIN = sys.platform.startswith("win")
SUSPICIOUS_DIRS = ("\\temp\\", "\\downloads\\", "\\appdata\\local\\temp", "$recycle.bin", "\\users\\public\\", "/tmp/")
SYSTEM_PROCS = {"svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "wininit.exe"}
SCAN_PORTS = (22, 80, 135, 139, 443, 445, 3389, 8080)
LOG_SYSTEM = ("You are a security analyst. Summarize the notable errors, warnings and anything security-relevant in "
              "these logs in at most 6 short plain-text points, and say if action is needed. No markdown.")


def _run(cmd, timeout=20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"(could not run {cmd[0]}: {type(e).__name__})"


def _status() -> str:
    import psutil
    boot = datetime.fromtimestamp(psutil.boot_time())
    procs = sorted(psutil.process_iter(["name", "memory_percent"]),
                   key=lambda p: p.info["memory_percent"] or 0, reverse=True)[:3]
    lines = [f"CPU {psutil.cpu_percent(interval=0.5):.0f}%, memory {psutil.virtual_memory().percent:.0f}%, "
             f"disk {psutil.disk_usage(str(config.WORKSPACE.anchor or '/')).percent:.0f}% used",
             f"Up since {boot:%Y-%m-%d %H:%M}",
             "Top memory users: " + ", ".join(f"{p.info['name']} ({p.info['memory_percent'] or 0:.1f}%)" for p in procs)]
    if IS_WIN:
        states = re.findall(r"State\s+(\w+)", _run(["netsh", "advfirewall", "show", "allprofiles", "state"]))
        if states:
            lines.append("Firewall profiles: " + ", ".join(states))
    return "\n".join(lines)


def _process_scan() -> str:
    import psutil
    flagged = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        name, exe = (p.info["name"] or ""), (p.info["exe"] or "")
        low = exe.lower().replace("/", "\\")
        if exe and any(d in low for d in SUSPICIOUS_DIRS):
            flagged.append(f"{name} (pid {p.info['pid']}) runs from a temp or download folder")
        elif name.lower() in SYSTEM_PROCS and exe and "\\windows\\system32" not in low:
            flagged.append(f"{name} (pid {p.info['pid']}) uses a system name but runs from {exe}")
    if not flagged:
        return "No suspicious processes found by simple checks (this is a heuristic, not antivirus)."
    return "Worth a look: " + "; ".join(flagged[:10])


def _connections() -> str:
    import psutil
    try:
        conns = [c for c in psutil.net_connections(kind="inet") if c.status == "ESTABLISHED" and c.raddr]
    except psutil.AccessDenied:
        return "I need administrator rights to list network connections."
    names = {}
    for c in conns[:60]:
        try:
            names[c.pid] = psutil.Process(c.pid).name() if c.pid else "unknown"
        except psutil.Error:
            names[c.pid] = "unknown"
    rows = sorted({f"{names.get(c.pid, 'unknown')} -> {c.raddr.ip}:{c.raddr.port}" for c in conns[:60]})
    return f"{len(conns)} active connections. " + "; ".join(rows[:10])


def _devices() -> str:
    found = []
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{17})", _run(["arp", "-a"])):
        if ipaddress.ip_address(ip).is_private and not ip.endswith((".255", ".1.255")) and mac.lower() != "ff-ff-ff-ff-ff-ff":
            found.append(f"{ip} ({mac})")
    return f"{len(found)} devices seen on the network: " + "; ".join(found[:15]) if found else "No devices found in the ARP table."


@skill("security_status", doc="Check this PC's health: CPU, memory, disk, firewall and top processes")
def security_status(args, ctx):
    return _status()


@skill("process_scan", doc="Look for suspicious running processes on this PC")
def process_scan(args, ctx):
    return _process_scan()


@skill("net_connections", doc="Show which programs on this PC are connected to the internet")
def net_connections(args, ctx):
    return _connections()


@skill("net_devices", doc="List devices that were recently seen on your local network")
def net_devices(args, ctx):
    return _devices()


def _own_network() -> ipaddress.IPv4Network:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))  # no packet is sent; this only selects the outgoing interface
        ip = s.getsockname()[0]
    net = ipaddress.ip_network(f"{ip}/24", strict=False)
    if not net.is_private:
        raise ValueError("Your network is not a private network, so I will not scan it.")
    return net


def _probe(ip: str) -> tuple:
    open_ports = []
    for port in SCAN_PORTS:
        try:
            with socket.create_connection((ip, port), timeout=0.4):
                open_ports.append(port)
        except OSError:
            pass
    return ip, open_ports


@skill("net_scan", risky=True, doc="Scan your own home network (the private /24 you are connected to) for devices and open common ports")
def net_scan(args, ctx):
    net = _own_network()
    with ThreadPoolExecutor(max_workers=64) as pool:
        results = [r for r in pool.map(_probe, [str(h) for h in net.hosts()]) if r[1]]
    if not results:
        return f"Scanned {net}: no devices with common open ports answered."
    return f"Scanned {net}. " + "; ".join(f"{ip} open ports {ports}" for ip, ports in results)


@skill("log_analyze", optional=("path",), doc="Analyze system error logs (or a log file in your home folder) and summarize them")
def log_analyze(args, ctx):
    if args.get("path"):
        from .paths import user_path
        text = user_path(args["path"]).read_text(encoding="utf-8", errors="replace")[-12000:]
    elif IS_WIN:
        text = _run(["wevtutil", "qe", "System", "/c:40", "/rd:true", "/f:text",
                     "/q:*[System[(Level=1 or Level=2)]]"], timeout=30)[:12000]
        failed = _run(["wevtutil", "qe", "Security", "/c:30", "/rd:true", "/f:text",
                       "/q:*[System[(EventID=4625)]]"], timeout=30)
        text += "\n--- Failed sign-in events ---\n" + (failed[:4000] if "denied" not in failed.lower() else "(needs administrator rights)")
    else:
        text = _run(["journalctl", "-p", "err", "-n", "60", "--no-pager"], timeout=30)[:12000]
    return ctx.llm(LOG_SYSTEM, text)


@skill("security_report", doc="Create a full security report file for this PC and your network")
def security_report(args, ctx):
    sections = [("System status", _status), ("Suspicious processes", _process_scan),
                ("Internet connections", _connections), ("Network devices", _devices)]
    out = [f"Security report - {datetime.now():%Y-%m-%d %H:%M}\n"]
    for title, fn in sections:
        try:
            out.append(f"== {title} ==\n{fn()}\n")
        except Exception as e:
            out.append(f"== {title} ==\n(failed: {type(e).__name__})\n")
    folder = config.WORKSPACE / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"security_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text("\n".join(out), encoding="utf-8")
    return f"Security report saved: {path}"

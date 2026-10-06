"""Immediate-problem detector (launchd com.altcoin.healthalert, every 5 min). Read-only checks, paper system untouched.
Raises an alert when: a baseline launchd service disappears, a log gains NEW Traceback lines, free disk < 4 GB,
the F15 hourly log is stale (> 2.5 h) or the F18/F19 daily output is stale (> 26 h).
New alerts -> data/alerts/active.json + history.log + macOS notification; optional push via ntfy when the file
data/alerts/ntfy_topic exists (only after 유리 approves). Claude's check-ins read active.json and message 유리."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "data/alerts"
A.mkdir(parents=True, exist_ok=True)
STATE, ACTIVE, HIST = A / "state.json", A / "active.json", A / "history.log"
LOGS = list((ROOT / "logs").glob("*.err")) + list((ROOT / "data/logs").glob("*.log"))


def services():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    return sorted({ln.split()[-1] for ln in out.splitlines() if "com.altcoin." in ln})


def tb_counts():
    c = {}
    for f in LOGS:
        try:
            if f.stat().st_size < 50_000_000:
                b = f.read_bytes()      # ignore tracebacks right after a logged auto-reconnect (websocket drops)
                c[str(f.relative_to(ROOT))] = b.count(b"Traceback") - len(re.findall(rb"reconnecting[^\n]*\nTraceback", b))
        except OSError:
            pass
    return c


def age_h(p):
    p = ROOT / p
    return (time.time() - p.stat().st_mtime) / 3600 if p.exists() else 999.0


def notify(msg):
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "AltCoin alert"'],
                   capture_output=True)
    topic = A / "ntfy_topic"
    if topic.exists():
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic.read_text().strip()}", data=msg.encode(),
                                         headers={"Title": "AltCoin alert", "Priority": "high"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:  # noqa: BLE001
            pass


def main():
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    svc, tb = services(), tb_counts()
    if "services" not in st:                       # first run: record baseline, no alerts
        st = {"services": svc, "tb": tb}
        STATE.write_text(json.dumps(st, indent=1)); ACTIVE.write_text("[]")
        print("baseline", len(svc), "services,", len(tb), "logs"); return
    alerts = []
    missing = [s for s in st["services"] if s not in svc]
    if missing:
        alerts.append(("services", "launchd services missing: " + ", ".join(m[12:] for m in missing)))
    grew = [f for f, n in tb.items() if n > st["tb"].get(f, 0)]
    if grew:
        alerts.append(("tb:" + ",".join(sorted(grew)), "new Traceback in " + ", ".join(Path(g).name for g in grew)))
    free = shutil.disk_usage(ROOT).free / 1e9
    if free < 4:
        alerts.append(("disk", f"free disk {free:.1f} GB (< 4 GB)"))
    if age_h("data/logs/f15trend.out.log") > 2.5:
        alerts.append(("f15stale", f"F15/F17 hourly log stale {age_h('data/logs/f15trend.out.log'):.1f} h"))
    if age_h("data/forward/f18_holdings.parquet") > 26:
        alerts.append(("f18stale", f"F18/F19 daily output stale {age_h('data/forward/f18_holdings.parquet'):.0f} h"))
    prev = {a["key"]: a for a in json.loads(ACTIVE.read_text() or "[]")} if ACTIVE.exists() else {}
    now = time.strftime("%F %T")
    act = []
    for key, msg in alerts:
        a = prev.get(key) or {"key": key, "msg": msg, "first_seen": now, "reported": False}
        a["msg"], a["last_seen"] = msg, now
        if key not in prev:
            with open(HIST, "a") as fh:
                fh.write(f"{now} NEW {msg}\n")
            notify(msg)
        act.append(a)
    for key in set(prev) - {k for k, _ in alerts}:
        with open(HIST, "a") as fh:
            fh.write(f"{now} CLEARED {prev[key]['msg']}\n")
    ACTIVE.write_text(json.dumps(act, indent=1, ensure_ascii=False))
    st["tb"] = tb                                   # a traceback alerts once; services baseline stays fixed
    STATE.write_text(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()

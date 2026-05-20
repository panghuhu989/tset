import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psutil

from guardian.alert_store import AlertStore
from guardian.common import expand_path
from guardian.network_diagnostics import DEFAULT_NETWORK_CONFIG, NetworkDiagnostics

def default_workspace_dir() -> str:
    return expand_path("~/.nanobot/workspace/guardian")


WORKSPACE_DIR = Path(expand_path(os.environ.get("GUARDIAN_WORKSPACE_DIR", default_workspace_dir())))
STORE = AlertStore(WORKSPACE_DIR)
LOG_PATH = WORKSPACE_DIR / "actions.log"
HARDWARE_METRICS = {
    "cpu_temperature",
    "fan_speed",
    "voltage",
}


def reset_store(workspace: str) -> None:
    global WORKSPACE_DIR, STORE, LOG_PATH

    WORKSPACE_DIR = Path(expand_path(workspace))
    STORE = AlertStore(WORKSPACE_DIR)
    LOG_PATH = WORKSPACE_DIR / "actions.log"


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, file=sys.stderr)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def list_pending() -> int:
    files = STORE.list_pending_files()

    if not files:
        print("No pending alerts.")
        return 0

    for file in files:
        alert = json.loads(file.read_text(encoding="utf-8"))
        print(
            f"{alert['id']} | {alert['metric']} {alert['value']}% >= {alert['threshold']}% | {alert['host']} | {alert['createdAt']}"
        )

    return 0


def show_alert(alert_id: str) -> int:
    _, alert = STORE.load_alert(alert_id)
    print(json.dumps(alert, ensure_ascii=False, indent=2))
    return 0


def top_processes(sort_key: str, limit: int, whitelist: List[str]) -> List[psutil.Process]:
    procs: List[psutil.Process] = []

    for proc in psutil.process_iter(["pid", "name", sort_key, "username"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in whitelist:
                continue
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return sorted(
        procs,
        key=lambda p: float(p.info.get(sort_key) or 0),
        reverse=True,
    )[:limit]


def set_lower_priority(proc: psutil.Process, cpu_nice: int, windows_priority: str) -> str:
    try:
        if os.name == "nt":
            target = getattr(psutil, f"{windows_priority.upper()}_PRIORITY_CLASS", None)
            if target is None:
                target = psutil.BELOW_NORMAL_PRIORITY_CLASS

            proc.nice(target)
            return f"lowered priority for pid={proc.pid} name={proc.name()} to {windows_priority}"

        proc.nice(cpu_nice)
        return f"reniced pid={proc.pid} name={proc.name()} to {cpu_nice}"

    except Exception as exc:
        return f"failed to change priority pid={proc.pid}: {exc}"


def cleanup_temp_paths(paths: List[str]) -> List[str]:
    messages: List[str] = []
    seen = set()
    actual_paths = [tempfile.gettempdir(), *paths]

    for raw in actual_paths:
        path = expand_path(raw)

        if path in seen:
            continue

        seen.add(path)
        p = Path(path)

        if not p.exists():
            messages.append(f"skip missing temp path: {path}")
            continue

        removed_files = 0
        removed_dirs = 0

        for child in p.iterdir():
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                    removed_files += 1
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed_dirs += 1
            except Exception as exc:
                messages.append(f"failed removing {child}: {exc}")

        messages.append(f"cleaned {path}: files={removed_files} dirs={removed_dirs}")

    return messages


def run_command(args: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def flush_dns_cache() -> str:
    if os.name == "nt":
        code, out, err = run_command(["ipconfig", "/flushdns"])
        if code == 0:
            return "flushed DNS cache"
        return f"failed to flush DNS cache: {err or out or 'unknown error'}"

    if sys.platform == "darwin":
        code, out, err = run_command(["dscacheutil", "-flushcache"])
        if code == 0:
            return "flushed DNS cache"
        return f"failed to flush DNS cache: {err or out or 'unknown error'}"

    commands = [
        ["resolvectl", "flush-caches"],
        ["systemd-resolve", "--flush-caches"],
    ]

    errors = []

    for cmd in commands:
        code, out, err = run_command(cmd)
        if code == 0:
            return "flushed DNS cache"
        errors.append(err or out or "unknown error")

    return f"failed to flush DNS cache: {'; '.join(errors)}"


def set_windows_balanced_power_plan() -> str:
    if os.name != "nt":
        return "set Windows balanced power plan skipped: not Windows"

    code, out, err = run_command(["powercfg", "/setactive", "SCHEME_BALANCED"])

    if code == 0:
        return "set Windows balanced power plan success"

    return f"set Windows balanced power plan failed: {err or out or 'unknown error'}"


def preview_actions(alert: Dict[str, Any]) -> List[str]:
    metric = alert["metric"]
    strategy = alert.get("strategy", {})
    whitelist = [str(x).lower() for x in strategy.get("processWhitelist", [])]

    if metric == "cpu":
        procs = top_processes("cpu_percent", int(strategy.get("cpuTopN", 2)), whitelist)
        actions = [
            f"lower priority of pid={p.pid} name={p.info.get('name')} cpu={p.info.get('cpu_percent')}%"
            for p in procs
        ]
        return actions or ["no eligible high-cpu processes found"]

    if metric == "memory":
        procs = top_processes("memory_percent", int(strategy.get("memoryTopN", 1)), whitelist)
        actions = [
            f"lower priority of pid={p.pid} name={p.info.get('name')} mem={round(float(p.info.get('memory_percent') or 0), 2)}%"
            for p in procs
        ]
        actions.append("clean temp directories")
        return actions

    if metric == "disk":
        return ["clean temp directories"]

    if metric == "gpu":
        return ["notify only; no default GPU action configured"]

    if metric == "network":
        return [
            "flush DNS cache",
            "re-diagnose network",
            "no destructive network reset",
        ]

    if metric in HARDWARE_METRICS:
        procs = top_processes(
            "cpu_percent",
            int(strategy.get("hardwareTopN", 3)),
            whitelist,
        )

        actions = [
            f"hardware reduce priority pid={p.pid} name={p.info.get('name')} cpu={p.info.get('cpu_percent')}%"
            for p in procs
        ]

        if not actions:
            actions.append("hardware no heavy process found")

        if strategy.get("setBalancedPowerPlanOnHardwareAlert", False):
            actions.append("set balanced power plan")

        actions.append(f"hardware advice {metric}")
        actions.append("no destructive hardware action")

        return actions

    return ["no default action configured"]


def approve_alert(alert_id: str) -> int:
    src, alert = STORE.load_alert(alert_id)

    metric = alert["metric"]
    strategy = alert.get("strategy", {})
    whitelist = [str(x).lower() for x in strategy.get("processWhitelist", [])]
    results: List[str] = []

    if metric == "cpu":
        for proc in top_processes("cpu_percent", int(strategy.get("cpuTopN", 2)), whitelist):
            results.append(
                set_lower_priority(
                    proc,
                    int(strategy.get("cpuNice", 10)),
                    str(strategy.get("windowsPriority", "below_normal")),
                )
            )

    elif metric == "memory":
        for proc in top_processes("memory_percent", int(strategy.get("memoryTopN", 1)), whitelist):
            results.append(
                set_lower_priority(
                    proc,
                    int(strategy.get("cpuNice", 10)),
                    str(strategy.get("windowsPriority", "below_normal")),
                )
            )
        results.extend(cleanup_temp_paths(strategy.get("diskTempPaths", [])))

    elif metric == "disk":
        results.extend(cleanup_temp_paths(strategy.get("diskTempPaths", [])))

    elif metric == "gpu":
        results.append("no automatic GPU action configured")

    elif metric == "network":
        results.append(flush_dns_cache())

        network_config = alert.get("networkConfig") or DEFAULT_NETWORK_CONFIG
        _, diagnosis = NetworkDiagnostics(network_config).run()

        results.append(
            "network diagnosis: "
            f"{diagnosis.get('title')}；"
            f"{diagnosis.get('reason')}；"
            f"成功率 {round(float(diagnosis.get('success_rate') or 0) * 100, 2)}%；"
            f"平均延迟 {diagnosis.get('avg_latency_ms')}ms；"
            f"抖动 {diagnosis.get('jitter_ms')}ms"
        )

        results.append("no destructive network reset")
        alert["networkDiagnosisAfterAction"] = diagnosis
    
    elif metric in HARDWARE_METRICS:
        procs = top_processes(
            "cpu_percent",
            int(strategy.get("hardwareTopN", 3)),
            whitelist,
        )

        if procs:
            for proc in procs:
                result = set_lower_priority(
                    proc,
                    int(strategy.get("cpuNice", 10)),
                    str(strategy.get("windowsPriority", "below_normal")),
                )
                results.append(f"hardware lowered priority: {result}")
        else:
            results.append("hardware no heavy process found")

        if strategy.get("setBalancedPowerPlanOnHardwareAlert", False):
            results.append(set_windows_balanced_power_plan())

        results.append(f"hardware advice {metric}")
        results.append("no destructive hardware action")

    else:
        results.append("unknown metric; nothing executed")

    alert["status"] = "approved"
    alert["approvedAt"] = datetime.now().isoformat(timespec="seconds")
    alert["results"] = results

    dst = STORE.archive_alert(alert, src)

    log(f"approved {alert_id}: {results}")

    print(
        json.dumps(
            {
                "id": alert_id,
                "status": "approved",
                "history": str(dst),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def reject_alert(alert_id: str) -> int:
    src, alert = STORE.load_alert(alert_id)

    alert["status"] = "rejected"
    alert["rejectedAt"] = datetime.now().isoformat(timespec="seconds")

    dst = STORE.archive_alert(alert, src)

    log(f"rejected {alert_id}")

    print(
        json.dumps(
            {
                "id": alert_id,
                "status": "rejected",
                "history": str(dst),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def preview_alert(alert_id: str) -> int:
    _, alert = STORE.load_alert(alert_id)
    actions = preview_actions(alert)

    print(
        json.dumps(
            {
                "id": alert_id,
                "status": alert["status"],
                "actions": actions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Computer Guardian action runner")
    parser.add_argument("--workspace", default=None, help="Override workspace dir")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    show_p = sub.add_parser("show")
    show_p.add_argument("id")

    preview_p = sub.add_parser("preview")
    preview_p.add_argument("id")

    approve_p = sub.add_parser("approve")
    approve_p.add_argument("id")

    reject_p = sub.add_parser("reject")
    reject_p.add_argument("id")

    args = parser.parse_args()

    if args.workspace:
        reset_store(args.workspace)

    if args.cmd == "list":
        return list_pending()

    if args.cmd == "show":
        return show_alert(args.id)

    if args.cmd == "preview":
        return preview_alert(args.id)

    if args.cmd == "approve":
        return approve_alert(args.id)

    if args.cmd == "reject":
        return reject_alert(args.id)

    parser.print_help()
    return 1
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException

from guardian.alert_store import AlertStore
from guardian.bridge_models import GuardianAlertEvent, GuardianCommandEvent, model_to_dict
from guardian.common import expand_path
from guardian.feishu_client import FeishuClient
from guardian.message_builder import build_feishu_text, format_strategy_reply


app = FastAPI(title="Guardian Bridge")


STATE_DIR = Path(os.environ.get("GUARDIAN_STATE_DIR", "./workspace/guardian_alerts"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

GUARDIAN_WORKSPACE_DIR = expand_path(
    os.environ.get("GUARDIAN_WORKSPACE_DIR", "~/.nanobot/workspace/guardian")
)

STRATEGY_RUNNER = Path(
    os.environ.get(
        "GUARDIAN_STRATEGY_RUNNER",
        str(Path(__file__).resolve().parent.parent / "strategy_runner.py"),
    )
)

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()

feishu_client = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
store = AlertStore(Path(GUARDIAN_WORKSPACE_DIR))


def save_alert_event(event: GuardianAlertEvent) -> Path:
    path = STATE_DIR / f"{event.alert.id}.json"
    path.write_text(
        json.dumps(model_to_dict(event), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def extract_alert_id(text: str) -> Optional[str]:
    match = re.search(r"([a-zA-Z]+-\d{8}-\d{6})", text)
    if not match:
        return None
    return match.group(1)


def resolve_alert_id(alert_id: Optional[str]) -> Optional[str]:
    if alert_id:
        return alert_id
    return store.latest_pending_alert_id()


def normalize_command(text: str) -> Tuple[str, Optional[str]]:
    raw = text.strip()
    raw = re.sub(r"\s+", " ", raw)

    if not raw:
        raise ValueError("命令为空")

    lower = raw.lower()
    alert_id = extract_alert_id(raw)

    english_match = re.match(
        r"^guardian\s+(list|show|preview|approve|reject)(?:\s+([a-zA-Z]+-\d{8}-\d{6}))?$",
        raw,
        re.IGNORECASE,
    )

    if english_match:
        cmd = english_match.group(1).lower()
        parsed_alert_id = english_match.group(2) or alert_id

        if cmd == "list":
            return "list", None

        resolved = resolve_alert_id(parsed_alert_id)
        if not resolved:
            raise ValueError("当前没有可处理的待处理告警。")
        return cmd, resolved

    if raw in {"列表", "告警列表", "待处理", "待处理告警"}:
        return "list", None

    if raw == "1":
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可预览的待处理告警。")
        return "preview", resolved

    if raw == "2":
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可处理的待处理告警。")
        return "approve", resolved

    if raw == "3":
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可忽略的待处理告警。")
        return "reject", resolved

    if raw == "4":
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可查看的待处理告警。")
        return "show", resolved

    if raw in {"忽略", "暂不处理", "不处理", "拒绝"} or "暂不处理" in raw or "不处理" in raw:
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可忽略的待处理告警。")
        return "reject", resolved

    if raw in {"预览", "看看", "看一下", "先看看"} or "预览" in raw:
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可预览的待处理告警。")
        return "preview", resolved

    if raw in {"处理", "立即处理", "执行", "确认", "批准"} or "处理" in raw or "approve" in lower:
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可处理的待处理告警。")
        return "approve", resolved

    if raw in {"详情", "查看", "查看详情"} or "详情" in raw:
        resolved = resolve_alert_id(alert_id)
        if not resolved:
            raise ValueError("当前没有可查看的待处理告警。")
        return "show", resolved

    raise ValueError("没看懂你的回复。请回复：预览、处理、忽略、详情，或回复“列表”。")


def run_strategy_command(cmd: str, alert_id: Optional[str]):
    allowed = {"list", "show", "preview", "approve", "reject"}

    if cmd not in allowed:
        raise ValueError(f"unsupported command: {cmd}")

    if not STRATEGY_RUNNER.exists():
        raise FileNotFoundError(f"strategy_runner.py not found: {STRATEGY_RUNNER}")

    args = [
        sys.executable,
        str(STRATEGY_RUNNER),
        "--workspace",
        GUARDIAN_WORKSPACE_DIR,
        cmd,
    ]

    if cmd != "list":
        if not alert_id:
            raise ValueError(f"{cmd} 命令需要告警 ID")
        args.append(alert_id)

    proc = subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=60,
        cwd=str(STRATEGY_RUNNER.parent),
    )

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "args": args,
    }


@app.post("/guardian/alert")
def guardian_alert(event: GuardianAlertEvent):
    if event.type != "guardian_alert":
        raise HTTPException(status_code=400, detail="invalid event type")

    if event.notify.channel != "feishu":
        raise HTTPException(status_code=400, detail="unsupported channel")

    chat_id = event.notify.chat_id.strip()
    if not chat_id or chat_id == "REPLACE_ME":
        raise HTTPException(status_code=400, detail="invalid chat_id")

    save_alert_event(event)
    text = build_feishu_text(event)

    try:
        result = feishu_client.send_text(chat_id, text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "ack": f"ACK:{event.alert.id}",
        "feishu": {
            "sent": True,
            "message_id": result.get("data", {}).get("message_id"),
        },
    }


@app.post("/guardian/command")
def guardian_command(event: GuardianCommandEvent):
    try:
        cmd, alert_id = normalize_command(event.text)
        result = run_strategy_command(cmd, alert_id)
        reply = format_strategy_reply(cmd, result)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="策略执行超时")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    feishu_result = None

    if event.send_reply:
        chat_id = (event.chat_id or "").strip()
        if not chat_id:
            raise HTTPException(
                status_code=400,
                detail="send_reply=true 时必须提供 chat_id",
            )

        try:
            feishu_result = feishu_client.send_text(chat_id, reply)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    return {
        "ok": result["returncode"] == 0,
        "cmd": cmd,
        "alert_id": alert_id,
        "reply": reply,
        "strategy": {
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        },
        "feishu": feishu_result,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "state_dir": str(STATE_DIR),
        "guardian_workspace_dir": GUARDIAN_WORKSPACE_DIR,
        "pending_dir": str(store.pending_dir),
        "latest_pending_alert_id": store.latest_pending_alert_id(),
        "strategy_runner": str(STRATEGY_RUNNER),
        "strategy_runner_exists": STRATEGY_RUNNER.exists(),
        "feishu_app_id_configured": bool(FEISHU_APP_ID),
        "feishu_app_secret_configured": bool(FEISHU_APP_SECRET),
    }


@app.get("/guardian/metrics")
def guardian_metrics():
    metrics_path = store.state_dir / "latest_metrics.json"
    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail="metrics not available yet")

    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to read metrics: {exc}")

    return data


@app.get("/guardian/alerts")
def guardian_alerts():
    pending_files = store.list_pending_files()
    alerts = []
    for path in pending_files:
        try:
            alert = json.loads(path.read_text(encoding="utf-8"))
            alerts.append(alert)
        except Exception:
            continue

    return {"count": len(alerts), "alerts": alerts}
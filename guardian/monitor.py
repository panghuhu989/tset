import json
import os
import platform
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import requests

from guardian.alert_store import AlertStore
from guardian.common import JsonLogger, expand_path
from guardian.network_diagnostics import NetworkDiagnostics
from guardian.hardware_diagnostics import HARDWARE_METRICS, HardwareDiagnostics

try:
    import GPUtil  # type: ignore
except Exception:
    GPUtil = None


class GuardianMonitor:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

        self.workspace_dir = Path(expand_path(config["workspaceDir"]))
        self.store = AlertStore(self.workspace_dir)
        self.logger = JsonLogger(self.workspace_dir / "watchdog.log")

        self.hit_counts: Dict[str, int] = defaultdict(int)
        self.cooldowns = self.store.load_cooldowns()
        self.metric_details: Dict[str, Any] = {}

    def log(self, message: str) -> None:
        self.logger.log(message)

    def save_latest_metrics(self, metrics: Dict[str, Optional[float]]) -> None:
        path = self.store.state_dir / "latest_metrics.json"
        data = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "metrics": metrics,
            "details": self.metric_details,
        }
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_metrics(self) -> Dict[str, Optional[float]]:
        metrics: Dict[str, Optional[float]] = {
            "cpu": float(psutil.cpu_percent(interval=1)),
            "memory": float(psutil.virtual_memory().percent),
            "disk": float(psutil.disk_usage(self.config["diskTarget"]).percent),
            "gpu": None,
            "network": None,
            "cpu_temperature": None,
            "fan_speed": None,
            "voltage": None
        }

        self.metric_details = {}

        if GPUtil is not None:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    metrics["gpu"] = max(float(g.load * 100) for g in gpus)
            except Exception:
                metrics["gpu"] = None

        try:
            network_value, network_details = NetworkDiagnostics(
                self.config.get("network", {})
            ).run()
            metrics["network"] = network_value
            self.metric_details["network"] = network_details
        except Exception as exc:
            metrics["network"] = 100.0
            self.metric_details["network"] = {
                "status": "error",
                "title": "网络检测出错",
                "reason": f"网络检测过程出错：{exc}",
                "suggestion": "请检查 network 配置或网络诊断模块。",
            }

        try:
            hardware_config = dict(self.config.get("hardware", {}))

            # 让 thresholds 里的值同步控制硬件模块。
            hardware_config.setdefault(
                "cpuTemperatureWarnCelsius",
                self.config.get("thresholds", {}).get("cpu_temperature", 85),
            )
            hardware_config.setdefault(
                "fanMinRpm",
                self.config.get("thresholds", {}).get("fan_speed", 800),
            )

            hardware_results = HardwareDiagnostics(hardware_config).run_all()

            for metric_name, result in hardware_results.items():
                hardware_value, hardware_details = result
                metrics[metric_name] = hardware_value
                self.metric_details[metric_name] = hardware_details

        except Exception as exc:
            for metric_name in HARDWARE_METRICS:
                metrics[metric_name] = None
                self.metric_details[metric_name] = {
                    "status": "error",
                    "title": "硬件检测出错",
                    "reason": f"硬件检测过程出错：{exc}",
                    "suggestion": "请检查 hardware 配置或硬件诊断模块。",
                    "sensors": [],
                }

        return metrics

    def should_fire_metric(self, metric: str, value: Optional[float]) -> bool:
        if value is None:
            self.hit_counts[metric] = 0
            return False

        if metric in HARDWARE_METRICS:
            details = self.metric_details.get(metric, {}) or {}
            status = details.get("status")

            alert_statuses = {
                "hot",
                "critical",
                "fan_low",
                "fan_stopped",
                "voltage_low",
                "voltage_high",
            }

            if status in alert_statuses:
                self.hit_counts[metric] += 1
            else:
                self.hit_counts[metric] = 0

            return self.hit_counts[metric] >= int(self.config["consecutiveHits"])

        threshold = self.config["thresholds"][metric]

        if value >= threshold:
            self.hit_counts[metric] += 1
        else:
            self.hit_counts[metric] = 0

        return self.hit_counts[metric] >= int(self.config["consecutiveHits"])

    def find_alerts(self, metrics: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []

        for metric, value in metrics.items():
            if metric not in self.config["thresholds"]:
                continue

            if not self.should_fire_metric(metric, value):
                continue

            now = time.time()
            last = float(self.cooldowns.get(metric, 0))

            if now - last < int(self.config["cooldownSeconds"]):
                continue

            alert_id = f"{metric}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

            alert = {
                "id": alert_id,
                "metric": metric,
                "value": round(float(value or 0), 2),
                "threshold": self.config["thresholds"][metric],
                "createdAt": datetime.now().isoformat(timespec="seconds"),
                "status": "pending",
                "host": platform.node() or platform.uname().node,
                "platform": platform.platform(),
                "strategy": self.config["strategy"],
                "details": self.metric_details.get(metric, {}),
            }

            if metric == "network":
                alert["networkConfig"] = self.config.get("network", {})

            alerts.append(alert)

        return alerts

    def reply_commands(self, alert_id: str) -> List[str]:
        return [
            "预览",
            "处理",
            "忽略",
            "详情",
            "列表",
            f"guardian preview {alert_id}",
            f"guardian approve {alert_id}",
            f"guardian reject {alert_id}",
            f"guardian show {alert_id}",
        ]

    def build_alert_event(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        notify_cfg = self.config.get("notify", {})

        return {
            "type": "guardian_alert",
            "session_id": self.config["sessionId"],
            "notify": {
                "channel": str(notify_cfg.get("channel") or "").strip(),
                "chat_id": str(notify_cfg.get("chatId") or "").strip(),
            },
            "alert": {
                "id": alert["id"],
                "metric": alert["metric"],
                "value": alert["value"],
                "threshold": alert["threshold"],
                "created_at": alert["createdAt"],
                "status": alert["status"],
                "host": alert["host"],
                "platform": alert["platform"],
                "workspace_dir": str(self.workspace_dir),
                "pending_file": str(self.store.pending_dir / f"{alert['id']}.json"),
                "details": alert.get("details", {}),
            },
            "strategy": self.config["strategy"],
            "reply_commands": self.reply_commands(alert["id"]),
        }

    def resolve_nanobot_url(self) -> str:
        api_base = str(self.config["nanobotApiBase"]).strip()
        if not api_base:
            raise ValueError("nanobotApiBase 未配置")

        if api_base.endswith("/guardian/alert"):
            return api_base

        alert_path = str(self.config.get("nanobotAlertPath") or "/guardian/alert").strip()
        if not alert_path.startswith("/"):
            alert_path = "/" + alert_path

        return api_base.rstrip("/") + alert_path

    def notify_nanobot(self, alert: Dict[str, Any]) -> str:
        url = self.resolve_nanobot_url()
        alert_event = self.build_alert_event(alert)

        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=alert_event,
            timeout=int(self.config["requestTimeoutSeconds"]),
        )

        if not response.ok:
            self.log(
                f"nanobot notify failed body: status={response.status_code}, body={response.text}"
            )
            response.raise_for_status()

        try:
            data = response.json()
        except Exception:
            return response.text.strip()

        if isinstance(data, dict):
            if "ack" in data:
                return str(data["ack"])
            if "message" in data:
                return str(data["message"])

        return json.dumps(data, ensure_ascii=False)

    def notify_recovered_network_alerts(self) -> None:
        current_details = self.metric_details.get("network", {}) or {}
        if current_details.get("status") != "ok":
            return

        recovery_cooldown = int(
            self.config.get("network", {}).get("recoveryCooldownSeconds", 300)
        )

        now = time.time()
        last_recovery = float(self.cooldowns.get("network_recovery", 0))
        if now - last_recovery < recovery_cooldown:
            return

        pending = self.store.pending_network_alerts()
        if not pending:
            return

        for path, alert in pending:
            original_details = alert.get("details", {}) or {}

            recovery_alert = dict(alert)
            recovery_alert["status"] = "recovered"
            recovery_alert["recoveredAt"] = datetime.now().isoformat(timespec="seconds")
            recovery_alert["details"] = {
                "status": "recovered",
                "title": "网络已恢复",
                "reason": "之前检测到网络异常，现在网络检测已恢复正常。",
                "suggestion": "可以继续观察网络是否再次波动。",
                "original_status": original_details.get("status"),
                "original_reason": original_details.get("reason"),
                "original_created_at": alert.get("createdAt"),
                "current": current_details,
            }

            try:
                result = self.notify_nanobot(recovery_alert)
                self.log(f"network recovery notify result: {result}")

                archived = dict(alert)
                archived["status"] = "recovered"
                archived["recoveredAt"] = recovery_alert["recoveredAt"]
                archived["recoveryDetails"] = recovery_alert["details"]
                self.store.archive_alert(archived, path)

                self.cooldowns["network_recovery"] = time.time()
                self.store.save_cooldowns(self.cooldowns)
            except Exception as exc:
                self.log(f"network recovery notify failed: {exc}")

    def run(self) -> None:
        interval = int(self.config["intervalSeconds"])
        self.log("watchdog started")

        while True:
            try:
                metrics = self.get_metrics()
                self.log(f"metrics={json.dumps(metrics, ensure_ascii=False)}")
                self.save_latest_metrics(metrics)

                self.notify_recovered_network_alerts()

                alerts = self.find_alerts(metrics)

                for alert in alerts:
                    path = self.store.persist_alert(alert)
                    self.log(f"alert persisted: {path}")

                    try:
                        result = self.notify_nanobot(alert)
                        self.log(f"nanobot notify result: {result}")

                        self.cooldowns[alert["metric"]] = time.time()
                        self.store.save_cooldowns(self.cooldowns)

                    except Exception as exc:
                        self.log(f"nanobot notify failed: {exc}")

                        if alert.get("metric") == "network":
                            self.log(
                                "network alert saved locally; Feishu may be unavailable while network is abnormal"
                            )

                            self.cooldowns["network"] = time.time()
                            self.store.save_cooldowns(self.cooldowns)

            except KeyboardInterrupt:
                self.log("watchdog stopped by keyboard interrupt")
                raise
            except Exception as exc:
                self.log(f"watchdog error: {exc}")

            time.sleep(interval)
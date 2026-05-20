import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class AlertStore:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.state_dir = workspace_dir / "state"
        self.pending_dir = self.state_dir / "pending"
        self.history_dir = self.state_dir / "history"
        self.cooldown_path = self.state_dir / "cooldowns.json"

        for p in [self.workspace_dir, self.state_dir, self.pending_dir, self.history_dir]:
            p.mkdir(parents=True, exist_ok=True)

    def load_cooldowns(self) -> Dict[str, float]:
        try:
            if self.cooldown_path.exists():
                return json.loads(self.cooldown_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_cooldowns(self, cooldowns: Dict[str, float]) -> None:
        self.cooldown_path.write_text(
            json.dumps(cooldowns, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def persist_alert(self, alert: Dict[str, Any]) -> Path:
        path = self.pending_dir / f"{alert['id']}.json"
        path.write_text(
            json.dumps(alert, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_alert(self, alert_id: str) -> Tuple[Path, Dict[str, Any]]:
        path = self.pending_dir / f"{alert_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"pending alert not found: {path}")

        alert = json.loads(path.read_text(encoding="utf-8"))
        return path, alert

    def archive_alert(self, alert: Dict[str, Any], src: Path) -> Path:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        dst = self.history_dir / f"{alert['id']}.json"
        dst.write_text(
            json.dumps(alert, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if src.exists():
            src.unlink()
        return dst

    def latest_pending_alert_id(self) -> Optional[str]:
        if not self.pending_dir.exists():
            return None

        files = sorted(
            self.pending_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            return None

        return files[0].stem

    def list_pending_files(self) -> List[Path]:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        return sorted(self.pending_dir.glob("*.json"))

    def pending_network_alerts(self) -> List[Tuple[Path, Dict[str, Any]]]:
        alerts: List[Tuple[Path, Dict[str, Any]]] = []

        if not self.pending_dir.exists():
            return alerts

        for path in sorted(self.pending_dir.glob("network-*.json")):
            try:
                alert = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if alert.get("metric") != "network":
                continue

            if alert.get("status") != "pending":
                continue

            details = alert.get("details", {}) or {}
            if details.get("status") in {"ok", "recovered"}:
                continue

            alerts.append((path, alert))

        return alerts
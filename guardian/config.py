import json
from pathlib import Path
from typing import Any, Dict

from guardian.common import (
    deep_merge,
    default_disk_target,
    default_workspace_dir,
    expand_path,
)


DEFAULT_CONFIG: Dict[str, Any] = {
    "nanobotApiBase": "http://127.0.0.1:8910",
    "nanobotAlertPath": "/guardian/alert",
    "sessionId": "computer-guardian-relay",
    "intervalSeconds": 5,
    "cooldownSeconds": 300,
    "consecutiveHits": 3,
    "requestTimeoutSeconds": 30,
    "notify": {
        "channel": "feishu",
        "chatId": "REPLACE_ME",
    },
    "feishu": {
        "appId": "",
        "appSecret": "",
    },
    "thresholds": {
        "cpu": 90,
        "memory": 85,
        "disk": 90,
        "gpu": 90,
        "network": 1,
    },
    "network": {
        "enabled": True,
        "testUrls": [
            "https://www.feishu.cn",
            "https://www.baidu.com",
        ],
        "dnsHosts": [
            "open.feishu.cn",
            "www.baidu.com",
        ],
        "tcpTargets": [
            ["223.5.5.5", 53],
            ["114.114.114.114", 53],
            ["open.feishu.cn", 443],
        ],
        "sampleCount": 3,
        "timeoutSeconds": 3,
        "maxLatencyMs": 1500,
        "maxJitterMs": 800,
        "minSuccessRate": 0.7,
        "recoveryCooldownSeconds": 300,
    },
    "workspaceDir": default_workspace_dir(),
    "diskTarget": default_disk_target(),
    "strategy": {
        "allowTerminate": False,
        "cpuTopN": 2,
        "memoryTopN": 1,
        "cpuNice": 10,
        "windowsPriority": "below_normal",
        "diskTempPaths": [],
        "processWhitelist": [
            "systemd",
            "python",
            "python3",
            "nanobot",
            "code",
            "explorer.exe",
            "finder",
            "windowserver",
        ],
    },
}


def load_config(path: str) -> Dict[str, Any]:
    user_config = json.loads(Path(path).read_text(encoding="utf-8"))
    merged = deep_merge(DEFAULT_CONFIG, user_config)

    merged["workspaceDir"] = expand_path(merged["workspaceDir"])

    disk_target = str(merged.get("diskTarget") or "").strip()
    if not disk_target:
        disk_target = default_disk_target()

    merged["diskTarget"] = expand_path(disk_target)

    return merged
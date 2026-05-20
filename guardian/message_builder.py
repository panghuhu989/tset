import re
from typing import Any, Dict

from guardian.bridge_models import GuardianAlertEvent


def metric_to_short_cn(metric: str) -> Dict[str, str]:
    mapping = {
        "cpu": {
            "emoji": "🔥",
            "name": "CPU 过高",
            "full_name": "CPU 使用率",
            "impact": "可能导致电脑发热、风扇变响、程序卡顿。",
        },
        "memory": {
            "emoji": "🧠",
            "name": "内存过高",
            "full_name": "内存使用率",
            "impact": "可能导致电脑变慢、切换窗口卡顿、程序无响应。",
        },
        "disk": {
            "emoji": "💾",
            "name": "磁盘空间紧张",
            "full_name": "磁盘使用率",
            "impact": "可能导致无法保存文件、软件运行变慢。",
        },
        "gpu": {
            "emoji": "🎮",
            "name": "显卡占用过高",
            "full_name": "GPU 使用率",
            "impact": "可能导致画面卡顿、显卡发热、风扇变响。",
        },
        "network": {
            "emoji": "🌐",
            "name": "网络异常",
            "full_name": "网络连接状态",
            "impact": "可能导致飞书、网页、下载、接口请求或远程服务无法正常访问。",
        },
        "cpu_temperature": {
            "emoji": "🌡️",
            "name": "CPU 温度过高",
            "full_name": "CPU 温度",
            "impact": "可能导致 CPU 降频、风扇高转、程序卡顿，严重时可能自动关机。",
        },
        "fan_speed": {
            "emoji": "🌀",
            "name": "风扇转速异常",
            "full_name": "风扇转速",
            "impact": "可能导致散热不足、温度升高、降频或自动关机。",
        },
        "voltage": {
            "emoji": "⚡",
            "name": "电压异常",
            "full_name": "电压",
            "impact": "可能导致系统不稳定、异常重启、设备掉电或硬件风险。",
        },
    }

    return mapping.get(
        metric,
        {
            "emoji": "⚠️",
            "name": f"{metric} 异常",
            "full_name": metric,
            "impact": "可能影响电脑正常运行。",
        },
    )


def percent_text(value: Any) -> str:
    try:
        return f"{round(float(value) * 100, 2)}%"
    except Exception:
        return "未知"


def build_network_text(event: GuardianAlertEvent) -> str:
    a = event.alert
    details = a.details or {}

    status = str(details.get("status", "unknown"))
    title = str(details.get("title") or "网络异常")
    reason = str(details.get("reason") or "检测到网络连接异常。")
    suggestion = str(details.get("suggestion") or "建议检查网络连接。")

    if status == "recovered":
        original_status = details.get("original_status", "unknown")
        original_reason = details.get("original_reason", "未知")
        original_created_at = details.get("original_created_at", "未知")

        return (
            f"🌐【网络已恢复】\n"
            f"之前检测到网络异常，现在网络检测已恢复正常。\n\n"
            f"原异常类型：{original_status}\n"
            f"原异常原因：{original_reason}\n"
            f"异常时间：{original_created_at}\n"
            f"恢复时间：{a.created_at}\n\n"
            f"ID：{a.id}"
        )

    success_rate = details.get("success_rate")
    avg_latency = details.get("avg_latency_ms")
    jitter = details.get("jitter_ms")

    metric_lines = []

    if success_rate is not None:
        metric_lines.append(f"请求成功率：{percent_text(success_rate)}")

    if avg_latency is not None:
        metric_lines.append(f"平均延迟：{avg_latency}ms")

    if jitter is not None:
        metric_lines.append(f"延迟波动：{jitter}ms")

    metric_block = ""
    if metric_lines:
        metric_block = "\n" + "\n".join(metric_lines) + "\n"

    return (
        f"🌐【{title}】\n"
        f"{reason}\n"
        f"{metric_block}\n"
        f"建议：{suggestion}\n\n"
        f"回复：预览｜处理｜忽略｜详情\n\n"
        f"ID：{a.id}"
    )


def build_hardware_text(event: GuardianAlertEvent) -> str:
    a = event.alert
    details = a.details or {}

    title = str(details.get("title") or "硬件指标异常")
    reason = str(details.get("reason") or "检测到硬件指标异常。")
    suggestion = str(details.get("suggestion") or "建议检查硬件状态。")

    metric_name = details.get("metric_name", a.metric)
    value = details.get("value", a.value)
    unit = details.get("unit", "")

    sensor = (
        details.get("hottest_sensor")
        or details.get("lowest_sensor")
        or details.get("abnormal_sensor")
        or {}
    )

    sensor_name = sensor.get("name", "未知传感器")
    sensor_source = sensor.get("source", "未知来源")

    value_line = ""
    if value is not None:
        value_line = f"{metric_name}：{value}{unit}\n"

    return (
        f"{metric_to_short_cn(a.metric)['emoji']}【{title}】\n"
        f"{reason}\n\n"
        f"{value_line}"
        f"传感器：{sensor_name}\n"
        f"数据来源：{sensor_source}\n\n"
        f"建议：{suggestion}\n\n"
        f"回复：预览｜处理｜忽略｜详情\n\n"
        f"ID：{a.id}"
    )


def build_feishu_text(event: GuardianAlertEvent) -> str:
    a = event.alert
    info = metric_to_short_cn(a.metric)

    if a.metric == "network":
        return build_network_text(event)
    if a.metric in {"cpu_temperature", "fan_speed", "voltage"}:
        return build_hardware_text(event)

    return (
        f"{info['emoji']}【{info['name']}】\n"
        f"{a.value}% ≥ {a.threshold}%\n\n"
        f"{info['impact']}\n\n"
        f"回复：预览｜处理｜忽略｜详情\n\n"
        f"ID：{a.id}"
    )


def metric_name_cn(metric: str) -> str:
    mapping = {
        "cpu": "CPU 使用率",
        "memory": "内存使用率",
        "disk": "磁盘使用率",
        "gpu": "GPU 使用率",
        "network": "网络连接状态",
        "cpu_temperature": "CPU 温度",
        "fan_speed": "风扇转速",
        "voltage": "电压",
    }
    return mapping.get(metric, metric)


def status_name_cn(status: str) -> str:
    mapping = {
        "pending": "待处理",
        "approved": "已处理",
        "rejected": "已忽略",
        "recovered": "已恢复",
    }
    return mapping.get(status, status)


def action_to_cn(action: str) -> str:
    text = action or ""

    m = re.search(r"pid=(\d+)\s+name=([^\s]+)", text)
    proc_desc = ""
    if m:
        proc_desc = f"（{m.group(2)}，PID {m.group(1)}）"

    if text.startswith("lower priority of pid="):
        return f"准备降低高占用程序优先级{proc_desc}。"

    if text.startswith("lowered priority for pid="):
        return f"已降低高占用程序优先级{proc_desc}。"

    if text.startswith("reniced pid="):
        return f"已调整高占用程序优先级{proc_desc}。"

    if text.startswith("failed to change priority"):
        return "调整程序优先级失败，可能是权限不足或程序已退出。"

    if text.startswith("clean temp directories"):
        return "准备清理系统临时文件。"

    if text.startswith("cleaned "):
        files_match = re.search(r"files=(\d+)", text)
        dirs_match = re.search(r"dirs=(\d+)", text)
        files = files_match.group(1) if files_match else "若干"
        dirs = dirs_match.group(1) if dirs_match else "若干"
        return f"已清理临时文件：文件 {files} 个，文件夹 {dirs} 个。"

    if text.startswith("skip missing temp path"):
        return "跳过不存在的临时目录。"

    if text.startswith("failed removing"):
        return "部分临时文件清理失败，可能正在被占用。"

    if "no eligible high-cpu processes found" in text:
        return "没有找到适合处理的高 CPU 占用程序。"

    if "no automatic GPU action configured" in text:
        return "当前没有配置 GPU 自动处理策略。"

    if "flush DNS cache" in text:
        return "刷新 DNS 缓存。"

    if "re-diagnose network" in text:
        return "重新诊断网络状态。"

    if "no destructive network reset" in text:
        return "不执行重置网卡、重置 Winsock 等高风险操作。"

    if "flushed DNS cache" in text:
        return "已刷新 DNS 缓存。"

    if "failed to flush DNS cache" in text:
        return "刷新 DNS 缓存失败，可能是权限不足或系统命令不可用。"

    if "network diagnosis:" in text:
        return text.replace("network diagnosis:", "重新诊断结果：")

    if "notify only" in text:
        return "本次只提醒，不自动处理。"

    if "unknown metric" in text:
        return "该告警类型暂无自动处理策略。"
    
    if "hardware reduce priority" in text:
        return "准备降低高负载程序优先级，减少发热和供电压力。"

    if "hardware lowered priority" in text:
        return "已降低高负载程序优先级，帮助降低温度和功耗。"

    if "hardware no heavy process found" in text:
        return "没有找到明显高负载程序。"

    if "hardware advice cpu_temperature" in text:
        return "建议关闭编译、渲染、虚拟机、游戏等 CPU 高负载任务，并检查散热。"

    if "hardware advice fan_speed" in text:
        return "建议检查风扇是否堵转、积灰、损坏，确认散热模式和电源模式是否正常。"

    if "hardware advice voltage" in text:
        return "建议检查电源适配器、电池、电源线、插座或主板供电，避免继续高负载运行。"

    if "set balanced power plan" in text:
        return "准备切换到平衡电源计划，降低持续高负载。"

    if "set Windows balanced power plan success" in text:
        return "已切换到 Windows 平衡电源计划。"

    if "set Windows balanced power plan failed" in text:
        return "切换 Windows 平衡电源计划失败，可能是权限不足或系统不支持。"

    if "no destructive hardware action" in text:
        return "不执行关机、重启、强制结束程序、修改电压等高风险操作。"

    return text


def format_actions_cn(actions: Any) -> str:
    if not isinstance(actions, list) or not actions:
        return "暂无可执行操作。"

    lines = []
    for idx, action in enumerate(actions, start=1):
        lines.append(f"{idx}. {action_to_cn(str(action))}")

    return "\n".join(lines)


def format_list_reply(stdout: str) -> str:
    if not stdout or "No pending alerts." in stdout:
        return "【无待处理告警】\n当前没有需要处理的电脑告警。"

    lines = stdout.splitlines()
    result_lines = ["【待处理告警】"]

    for idx, line in enumerate(lines, start=1):
        parts = [p.strip() for p in line.split("|")]

        if len(parts) >= 4:
            alert_id = parts[0]
            metric_part = parts[1]
            metric = metric_part.split()[0] if metric_part else ""
            metric_cn = metric_name_cn(metric)

            result_lines.append(
                f"{idx}. {metric_cn}：{metric_part}\n"
                f"   ID：{alert_id}"
            )
        else:
            result_lines.append(f"{idx}. {line}")

    result_lines.append("\n回复：预览｜处理｜忽略｜详情")
    return "\n".join(result_lines)


def format_show_reply(data: Dict[str, Any]) -> str:
    alert_id = data.get("id", "未知")
    metric = data.get("metric", "未知")
    value = data.get("value", "未知")
    threshold = data.get("threshold", "未知")
    status = data.get("status", "未知")
    details = data.get("details", {}) or {}

    if metric == "network":
        title = details.get("title", "网络异常")
        reason = details.get("reason", "未知")
        suggestion = details.get("suggestion", "建议检查网络连接。")
        success_rate = details.get("success_rate")
        avg_latency = details.get("avg_latency_ms")
        jitter = details.get("jitter_ms")

        extra = []
        if success_rate is not None:
            extra.append(f"请求成功率：{percent_text(success_rate)}")
        if avg_latency is not None:
            extra.append(f"平均延迟：{avg_latency}ms")
        if jitter is not None:
            extra.append(f"延迟波动：{jitter}ms")

        extra_text = "\n".join(extra) if extra else "暂无详细指标。"

        return (
            f"【详情】\n"
            f"指标：网络连接状态\n"
            f"类型：{title}\n"
            f"原因：{reason}\n"
            f"{extra_text}\n"
            f"建议：{suggestion}\n"
            f"状态：{status_name_cn(str(status))}\n\n"
            f"回复：预览｜处理｜忽略\n"
            f"ID：{alert_id}"
        )
    
    if metric in {"cpu_temperature", "fan_speed", "voltage"}:
            title = details.get("title", "硬件指标异常")
            reason = details.get("reason", "未知")
            suggestion = details.get("suggestion", "建议检查硬件状态。")
            metric_name = details.get("metric_name", metric_name_cn(str(metric)))
            value = details.get("value", data.get("value"))
            unit = details.get("unit", "")

            sensor = (
                details.get("hottest_sensor")
                or details.get("lowest_sensor")
                or details.get("abnormal_sensor")
                or {}
            )

            sensor_name = sensor.get("name", "未知传感器")
            sensor_source = sensor.get("source", "未知来源")
            sensors = details.get("sensors", []) or []

            sensor_lines = []
            for idx, item in enumerate(sensors[:8], start=1):
                sensor_lines.append(
                    f"{idx}. {item.get('name', '未知')}：{item.get('current', '未知')}{item.get('unit', '')}"
                )

            sensor_text = "\n".join(sensor_lines) if sensor_lines else "暂无传感器明细。"

            return (
                f"【详情】\n"
                f"指标：{metric_name}\n"
                f"类型：{title}\n"
                f"原因：{reason}\n"
                f"当前值：{value}{unit}\n"
                f"传感器：{sensor_name}\n"
                f"数据来源：{sensor_source}\n\n"
                f"传感器明细：\n"
                f"{sensor_text}\n\n"
                f"建议：{suggestion}\n"
                f"状态：{status_name_cn(str(status))}\n\n"
                f"回复：预览｜处理｜忽略\n"
                f"ID：{alert_id}"
            )


    return (
        f"【详情】\n"
        f"指标：{metric_name_cn(str(metric))}\n"
        f"当前：{value}%\n"
        f"阈值：{threshold}%\n"
        f"状态：{status_name_cn(str(status))}\n\n"
        f"回复：预览｜处理｜忽略\n"
        f"ID：{alert_id}"
    )

    
def format_preview_reply(data: Dict[str, Any]) -> str:
    alert_id = data.get("id", "未知")
    actions = data.get("actions", [])
    action_count = len(actions) if isinstance(actions, list) else 0

    return (
        f"【预览】将执行 {action_count} 项处理：\n"
        f"{format_actions_cn(actions)}\n\n"
        f"回复：处理｜忽略\n"
        f"ID：{alert_id}"
    )


def format_approve_reply(data: Dict[str, Any]) -> str:
    alert_id = data.get("id", "未知")
    results = data.get("results", [])

    return (
        f"【已处理】\n"
        f"{format_actions_cn(results)}\n\n"
        f"ID：{alert_id}"
    )


def format_reject_reply(data: Dict[str, Any]) -> str:
    alert_id = data.get("id", "未知")

    return (
        f"【已忽略】本次不会自动处理。\n\n"
        f"ID：{alert_id}"
    )


def format_strategy_reply(cmd: str, result: Dict[str, Any]) -> str:
    import json

    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""
    returncode = int(result.get("returncode", 1))

    if returncode != 0:
        reason = stderr or stdout or "未知错误"
        return (
            "【操作失败】\n"
            f"{reason}\n\n"
            "回复“列表”查看当前待处理告警。"
        )

    if cmd == "list":
        return format_list_reply(stdout)

    data = None
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        data = None

    if data is None:
        return f"【完成】\n{stdout or '操作已完成。'}"

    if cmd == "show":
        return format_show_reply(data)

    if cmd == "preview":
        return format_preview_reply(data)

    if cmd == "approve":
        return format_approve_reply(data)

    if cmd == "reject":
        return format_reject_reply(data)

    return f"【完成】\n{stdout or '操作已完成。'}"
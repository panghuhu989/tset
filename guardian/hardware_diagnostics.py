import json
import os
import subprocess
import re
import requests
from typing import Any, Dict, List, Optional, Tuple

import psutil


HARDWARE_METRICS = {
    "cpu_temperature",
    "fan_speed",
    "voltage",
}


DEFAULT_HARDWARE_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "alertWhenUnavailable": False,

    "windowsUseHardwareMonitor": True,
    "windowsUseAcpiThermalZone": True,
    "windowsUseNvidiaSmiFan": True,

    "cpuTemperatureWarnCelsius": 85,
    "cpuTemperatureCriticalCelsius": 95,

    "useLibreHardwareMonitorWebApi": True,
    "libreHardwareMonitorHost": "127.0.0.1",
    "libreHardwareMonitorPort": 8085,
    "libreHardwareMonitorTimeoutSeconds": 3,

    "fanMinRpm": 800,
    "fanStoppedRpm": 100,
    "fanMinPercent": 20,
    "fanAlertWhenCpuHotOnly": True,
    "fanCpuHotCelsius": 75,

    "voltageRules": [
        {
            "name": "CPU VCore",
            "include": ["vcore", "cpu core", "cpu vcore", "core voltage"],
            "min": 0.60,
            "max": 1.60,
        },
        {
            "name": "+12V",
            "include": ["+12v", "12v"],
            "min": 11.40,
            "max": 12.60,
        },
        {
            "name": "+5V",
            "include": ["+5v", "5v"],
            "min": 4.75,
            "max": 5.25,
        },
        {
            "name": "+3.3V",
            "include": ["+3.3v", "3.3v"],
            "min": 3.135,
            "max": 3.465,
        },
    ],

    "excludeSensorKeywords": [
        "battery",
    ],
}


class HardwareDiagnostics:
    """
    硬件传感器诊断：
    - cpu_temperature：CPU 温度，单位 ℃
    - fan_speed：风扇转速，优先 RPM；没有 RPM 时可使用百分比
    - voltage：电压，单位 V

    Windows 下读取风扇 / 电压通常需要：
    - LibreHardwareMonitor
    - 或 OpenHardwareMonitor

    Windows 原生 WMI 通常只能读到很有限的 ACPI 温度。
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        merged = dict(DEFAULT_HARDWARE_CONFIG)
        merged.update(config or {})
        self.config = merged

    def run_all(self) -> Dict[str, Tuple[Optional[float], Dict[str, Any]]]:
        if not self.config.get("enabled", True):
            return {
                metric: (
                    None,
                    {
                        "status": "disabled",
                        "title": "硬件检测未启用",
                        "reason": "hardware.enabled=false",
                        "suggestion": "无需处理。",
                        "sensors": [],
                    },
                )
                for metric in HARDWARE_METRICS
            }

        sensors = self.collect_sensors()
        sensors = self.remove_excluded_sensors(sensors)

        cpu_value, cpu_details = self.evaluate_cpu_temperature(sensors)
        fan_value, fan_details = self.evaluate_fan_speed(sensors, cpu_value)
        voltage_value, voltage_details = self.evaluate_voltage(sensors)

        return {
            "cpu_temperature": (cpu_value, cpu_details),
            "fan_speed": (fan_value, fan_details),
            "voltage": (voltage_value, voltage_details),
        }

    def collect_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        sensors.extend(self.collect_psutil_temperature_sensors())
        sensors.extend(self.collect_psutil_fan_sensors())

        if os.name == "nt":
            if self.config.get("windowsUseHardwareMonitor", True):
                sensors.extend(self.collect_windows_hardware_monitor_sensors())

            if self.config.get("windowsUseAcpiThermalZone", True):
                sensors.extend(self.collect_windows_acpi_temperature_sensors())

            if self.config.get("useLibreHardwareMonitorWebApi", True):
                sensors.extend(self.collect_libre_hardware_monitor_web_api_sensors())

            if self.config.get("windowsUseNvidiaSmiFan", True):
                sensors.extend(self.collect_nvidia_smi_fan_sensors())
        else:
            if self.config.get("useLibreHardwareMonitorWebApi", False):
                sensors.extend(self.collect_libre_hardware_monitor_web_api_sensors())

            sensors.extend(self.collect_nvidia_smi_fan_sensors())

        return self.deduplicate_sensors(sensors)

    def collect_psutil_temperature_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
        except Exception:
            return sensors

        if not temps:
            return sensors

        for group_name, entries in temps.items():
            for entry in entries:
                current = getattr(entry, "current", None)

                if current is None:
                    continue

                label = getattr(entry, "label", "") or group_name

                sensors.append(
                    {
                        "source": "psutil",
                        "sensor_type": "Temperature",
                        "group": group_name,
                        "name": label,
                        "identifier": f"{group_name}/{label}",
                        "current": round(float(current), 2),
                        "unit": "C",
                        "high": getattr(entry, "high", None),
                        "critical": getattr(entry, "critical", None),
                    }
                )

        return sensors

    def collect_psutil_fan_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        try:
            fans = psutil.sensors_fans()
        except Exception:
            return sensors

        if not fans:
            return sensors

        for group_name, entries in fans.items():
            for entry in entries:
                current = getattr(entry, "current", None)

                if current is None:
                    continue

                label = getattr(entry, "label", "") or group_name

                sensors.append(
                    {
                        "source": "psutil",
                        "sensor_type": "Fan",
                        "group": group_name,
                        "name": label,
                        "identifier": f"{group_name}/{label}",
                        "current": round(float(current), 2),
                        "unit": "RPM",
                    }
                )

        return sensors

    def collect_windows_hardware_monitor_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        namespaces = [
            "root\\LibreHardwareMonitor",
            "root\\OpenHardwareMonitor",
        ]

        for namespace in namespaces:
            command = (
                "Get-CimInstance "
                f"-Namespace '{namespace}' "
                "-ClassName Sensor "
                "-ErrorAction Stop | "
                "Select-Object Name, Value, SensorType, Identifier, Parent | "
                "ConvertTo-Json -Depth 4"
            )

            data = self.run_powershell_json(command)

            if data is None:
                continue

            if isinstance(data, dict):
                data = [data]

            if not isinstance(data, list):
                continue

            for item in data:
                try:
                    sensor_type = str(item.get("SensorType") or "")
                    if sensor_type not in {"Temperature", "Fan", "Voltage"}:
                        continue

                    value = item.get("Value")
                    if value is None:
                        continue

                    unit = ""
                    if sensor_type == "Temperature":
                        unit = "C"
                    elif sensor_type == "Fan":
                        unit = "RPM"
                    elif sensor_type == "Voltage":
                        unit = "V"

                    sensors.append(
                        {
                            "source": namespace,
                            "sensor_type": sensor_type,
                            "group": "hardware_monitor",
                            "name": str(item.get("Name") or sensor_type),
                            "identifier": str(item.get("Identifier") or ""),
                            "parent": str(item.get("Parent") or ""),
                            "current": round(float(value), 3),
                            "unit": unit,
                        }
                    )
                except Exception:
                    continue

        return sensors
    
    def collect_libre_hardware_monitor_web_api_sensors(self) -> List[Dict[str, Any]]:
        """
        从 LibreHardwareMonitor 内置 Web Server 读取传感器数据。

        前提：
        LibreHardwareMonitor 中启用：
        Options -> Remote Web Server -> Run

        默认地址：
        http://127.0.0.1:8085/data.json
        """
        sensors: List[Dict[str, Any]] = []

        if not self.config.get("useLibreHardwareMonitorWebApi", True):
            return sensors

        host = str(self.config.get("libreHardwareMonitorHost", "127.0.0.1"))
        port = int(self.config.get("libreHardwareMonitorPort", 8085))
        timeout = float(self.config.get("libreHardwareMonitorTimeoutSeconds", 3))

        url = f"http://{host}:{port}/data.json"

        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return sensors

        def get_text(node: Dict[str, Any]) -> str:
            return str(
                node.get("Text")
                or node.get("text")
                or node.get("Name")
                or node.get("name")
                or ""
            )

        def get_children(node: Dict[str, Any]) -> List[Dict[str, Any]]:
            children = node.get("Children") or node.get("children") or []
            if isinstance(children, list):
                return [x for x in children if isinstance(x, dict)]
            return []

        def parse_value(raw: Any) -> tuple[Optional[float], str]:
            """
            支持：
            - 65.0
            - "65.0 °C"
            - "1200 RPM"
            - "35.0 %"
            - "1.236 V"
            """
            if raw is None:
                return None, ""

            if isinstance(raw, (int, float)):
                return float(raw), ""

            text = str(raw).strip()
            if not text:
                return None, ""

            match = re.search(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z°%]*)", text)
            if not match:
                return None, ""

            value = float(match.group(1))
            unit = match.group(2).replace("°", "")

            if unit.lower() in {"c", "celsius"}:
                unit = "C"

            return value, unit

        def infer_sensor_type(category: str, value_unit: str) -> Optional[str]:
            c = category.lower()
            u = value_unit.lower()

            if "temperature" in c or "temperatures" in c or u == "c":
                return "Temperature"

            if "fan" in c or "fans" in c or u == "rpm":
                return "Fan"

            if "voltage" in c or "voltages" in c or u == "v":
                return "Voltage"

            return None

        def walk(node: Dict[str, Any], parents: List[str]) -> None:
            text = get_text(node)
            children = get_children(node)

            # Libre/Open Hardware Monitor 的 data.json 通常是树结构：
            # 硬件 -> 分类，例如 Temperatures / Fans / Voltages -> 具体传感器
            category = parents[-1] if parents else ""
            raw_value = node.get("Value") or node.get("value")
            value, unit = parse_value(raw_value)

            sensor_type = infer_sensor_type(category, unit)

            if sensor_type and value is not None:
                sensor_id = (
                    node.get("SensorId")
                    or node.get("sensor_id")
                    or node.get("Identifier")
                    or node.get("identifier")
                    or node.get("id")
                    or "/".join(parents + [text])
                )

                group = parents[-2] if len(parents) >= 2 else "librehardwaremonitor_web"
                parent = " / ".join(parents)

                # 统一单位
                if sensor_type == "Temperature":
                    unit = "C"
                elif sensor_type == "Fan" and not unit:
                    unit = "RPM"
                elif sensor_type == "Voltage":
                    unit = "V"

                sensors.append(
                    {
                        "source": "librehardwaremonitor_web",
                        "sensor_type": sensor_type,
                        "group": group,
                        "name": text or str(sensor_id),
                        "identifier": str(sensor_id),
                        "parent": parent,
                        "current": round(float(value), 3),
                        "unit": unit,
                    }
                )

            next_parents = parents + ([text] if text else [])

            for child in children:
                walk(child, next_parents)

        if isinstance(data, dict):
            walk(data, [])

        return sensors

    def collect_windows_acpi_temperature_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        command = (
            "Get-CimInstance "
            "-Namespace 'root\\wmi' "
            "-ClassName MSAcpi_ThermalZoneTemperature "
            "-ErrorAction Stop | "
            "Select-Object InstanceName, CurrentTemperature | "
            "ConvertTo-Json -Depth 4"
        )

        data = self.run_powershell_json(command)

        if data is None:
            return sensors

        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            return sensors

        for item in data:
            try:
                raw = item.get("CurrentTemperature")
                if raw is None:
                    continue

                # ACPI 返回值通常是 0.1 Kelvin
                celsius = round((float(raw) / 10.0) - 273.15, 2)

                if celsius < 0 or celsius > 130:
                    continue

                sensors.append(
                    {
                        "source": "windows_acpi",
                        "sensor_type": "Temperature",
                        "group": "thermal_zone",
                        "name": str(item.get("InstanceName") or "ACPI Thermal Zone"),
                        "identifier": str(item.get("InstanceName") or ""),
                        "current": celsius,
                        "unit": "C",
                    }
                )
            except Exception:
                continue

        return sensors

    def collect_nvidia_smi_fan_sensors(self) -> List[Dict[str, Any]]:
        sensors: List[Dict[str, Any]] = []

        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,fan.speed",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                shell=False,
            )

            if proc.returncode != 0:
                return sensors

            for index, line in enumerate(proc.stdout.splitlines()):
                line = line.strip()
                if not line:
                    continue

                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue

                name = parts[0]
                fan_percent = parts[1]

                if fan_percent in {"[N/A]", "N/A", ""}:
                    continue

                value = float(fan_percent)

                if value < 0 or value > 100:
                    continue

                sensors.append(
                    {
                        "source": "nvidia-smi",
                        "sensor_type": "Fan",
                        "group": "gpu",
                        "name": f"{name} Fan",
                        "identifier": f"nvidia-smi/gpu/{index}/fan",
                        "current": round(value, 2),
                        "unit": "%",
                    }
                )

        except Exception:
            return sensors

        return sensors

    def evaluate_cpu_temperature(
        self,
        sensors: List[Dict[str, Any]],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        matched = []

        include = [
            "cpu",
            "package",
            "core",
            "tctl",
            "tdie",
            "processor",
            "thermal zone",
            "acpi",
        ]

        exclude = [
            "gpu",
            "nvidia",
            "radeon",
            "ssd",
            "hdd",
            "nvme",
            "disk",
            "drive",
            "battery",
            "fan",
            "voltage",
        ]

        for sensor in sensors:
            if sensor.get("sensor_type") != "Temperature":
                continue

            text = self.sensor_text(sensor)

            if exclude and any(keyword in text for keyword in exclude):
                continue

            if include and not any(keyword in text for keyword in include):
                continue

            value = self.safe_float(sensor.get("current"))
            if value is None or value < 0 or value > 130:
                continue

            matched.append(sensor)

        if not matched:
            details = {
                "status": "unavailable",
                "title": "未读取到 CPU 温度",
                "reason": "当前系统没有返回可用的 CPU 温度传感器数据。",
                "suggestion": "Windows 上建议运行 LibreHardwareMonitor 或 OpenHardwareMonitor，并用管理员权限启动。",
                "metric_name": "CPU 温度",
                "unit": "C",
                "sensors": [],
            }

            if self.config.get("alertWhenUnavailable", False):
                return 100.0, details

            return None, details

        hottest = max(matched, key=lambda x: float(x.get("current") or 0))
        max_temp = round(float(hottest.get("current") or 0), 2)

        warn = float(self.config.get("cpuTemperatureWarnCelsius", 85))
        critical = float(self.config.get("cpuTemperatureCriticalCelsius", 95))

        if max_temp >= critical:
            status = "critical"
            title = "CPU 温度严重过高"
            reason = f"CPU 最高温度 {max_temp}℃，已达到或超过严重阈值 {critical}℃。"
            suggestion = "建议立即停止高负载任务，检查风扇、散热口、硅脂、散热器和环境温度。"
        elif max_temp >= warn:
            status = "hot"
            title = "CPU 温度过高"
            reason = f"CPU 最高温度 {max_temp}℃，已达到或超过告警阈值 {warn}℃。"
            suggestion = "建议降低高 CPU 占用程序优先级，关闭编译、渲染、虚拟机、游戏等高负载任务。"
        else:
            status = "ok"
            title = "CPU 温度正常"
            reason = f"CPU 最高温度 {max_temp}℃，未超过告警阈值 {warn}℃。"
            suggestion = "无需处理。"

        details = {
            "status": status,
            "title": title,
            "reason": reason,
            "suggestion": suggestion,
            "metric_name": "CPU 温度",
            "value": max_temp,
            "unit": "C",
            "max_temperature_celsius": max_temp,
            "hottest_sensor": hottest,
            "warn_celsius": warn,
            "critical_celsius": critical,
            "sensors": matched,
        }

        return max_temp, details

    def evaluate_fan_speed(
        self,
        sensors: List[Dict[str, Any]],
        cpu_temperature: Optional[float],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        matched = []

        for sensor in sensors:
            if sensor.get("sensor_type") != "Fan":
                continue

            value = self.safe_float(sensor.get("current"))
            if value is None or value < 0:
                continue

            matched.append(sensor)

        if not matched:
            details = {
                "status": "unavailable",
                "title": "未读取到风扇转速",
                "reason": "当前系统没有返回可用的风扇转速传感器数据。",
                "suggestion": "Windows 上建议运行 LibreHardwareMonitor 或 OpenHardwareMonitor；部分笔记本不会开放风扇传感器。",
                "metric_name": "风扇转速",
                "unit": "RPM",
                "sensors": [],
            }

            if self.config.get("alertWhenUnavailable", False):
                return 100.0, details

            return None, details

        rpm_sensors = [s for s in matched if str(s.get("unit")) == "RPM"]
        percent_sensors = [s for s in matched if str(s.get("unit")) == "%"]

        if rpm_sensors:
            slowest = min(rpm_sensors, key=lambda x: float(x.get("current") or 0))
            value = round(float(slowest.get("current") or 0), 2)
            unit = "RPM"
            min_value = float(self.config.get("fanMinRpm", 800))
            stopped_value = float(self.config.get("fanStoppedRpm", 100))
        else:
            slowest = min(percent_sensors, key=lambda x: float(x.get("current") or 0))
            value = round(float(slowest.get("current") or 0), 2)
            unit = "%"
            min_value = float(self.config.get("fanMinPercent", 20))
            stopped_value = 1.0

        alert_when_hot_only = bool(self.config.get("fanAlertWhenCpuHotOnly", True))
        cpu_hot_threshold = float(self.config.get("fanCpuHotCelsius", 75))
        cpu_is_hot = cpu_temperature is not None and cpu_temperature >= cpu_hot_threshold

        should_check_low_fan = True
        if alert_when_hot_only and not cpu_is_hot and value > stopped_value:
            should_check_low_fan = False

        if value <= stopped_value:
            status = "fan_stopped"
            title = "风扇疑似停转"
            reason = f"检测到风扇转速约为 {value}{unit}，可能存在风扇停转或传感器异常。"
            suggestion = "建议立即降低负载，检查风扇是否被灰尘堵塞、是否损坏，必要时关机检查。"
        elif should_check_low_fan and value <= min_value:
            status = "fan_low"
            title = "风扇转速偏低"
            reason = f"检测到风扇转速 {value}{unit}，低于阈值 {min_value}{unit}。"
            suggestion = "建议检查风扇策略、散热模式、电源模式和散热口；同时降低高负载任务。"
        else:
            status = "ok"
            title = "风扇转速正常"
            reason = f"检测到风扇转速 {value}{unit}，未发现明显异常。"
            suggestion = "无需处理。"

        details = {
            "status": status,
            "title": title,
            "reason": reason,
            "suggestion": suggestion,
            "metric_name": "风扇转速",
            "value": value,
            "unit": unit,
            "lowest_sensor": slowest,
            "cpu_temperature_celsius": cpu_temperature,
            "fan_min_value": min_value,
            "fan_stopped_value": stopped_value,
            "fan_alert_when_cpu_hot_only": alert_when_hot_only,
            "fan_cpu_hot_celsius": cpu_hot_threshold,
            "sensors": matched,
        }

        return value, details

    def evaluate_voltage(
        self,
        sensors: List[Dict[str, Any]],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        voltage_sensors = []

        for sensor in sensors:
            if sensor.get("sensor_type") != "Voltage":
                continue

            value = self.safe_float(sensor.get("current"))
            if value is None or value < 0 or value > 30:
                continue

            voltage_sensors.append(sensor)

        if not voltage_sensors:
            details = {
                "status": "unavailable",
                "title": "未读取到电压数据",
                "reason": "当前系统没有返回可用的电压传感器数据。",
                "suggestion": "Windows 上通常需要 LibreHardwareMonitor 或 OpenHardwareMonitor 才能读取主板 / CPU 电压。",
                "metric_name": "电压",
                "unit": "V",
                "sensors": [],
            }

            if self.config.get("alertWhenUnavailable", False):
                return 100.0, details

            return None, details

        rules = self.config.get("voltageRules", [])
        checked = []
        abnormal = []

        for sensor in voltage_sensors:
            text = self.sensor_text(sensor)
            value = float(sensor.get("current") or 0)

            for rule in rules:
                include = [str(x).lower() for x in rule.get("include", [])]
                if include and not any(keyword in text for keyword in include):
                    continue

                min_value = float(rule.get("min"))
                max_value = float(rule.get("max"))

                record = {
                    "sensor": sensor,
                    "rule": rule,
                    "value": round(value, 3),
                    "min": min_value,
                    "max": max_value,
                    "ok": min_value <= value <= max_value,
                }

                checked.append(record)

                if value < min_value:
                    record["status"] = "voltage_low"
                    abnormal.append(record)
                elif value > max_value:
                    record["status"] = "voltage_high"
                    abnormal.append(record)

        if abnormal:
            first = abnormal[0]
            value = round(float(first["value"]), 3)
            rule = first["rule"]
            sensor = first["sensor"]

            if first["status"] == "voltage_low":
                title = "电压偏低"
                reason = f"{rule.get('name')} 当前电压 {value}V，低于下限 {first['min']}V。"
                suggestion = "建议检查电源适配器、供电接口、电池、电源线或主板供电；避免继续高负载运行。"
                status = "voltage_low"
            else:
                title = "电压偏高"
                reason = f"{rule.get('name')} 当前电压 {value}V，高于上限 {first['max']}V。"
                suggestion = "建议检查电源适配器、主板供电和电源环境；避免继续高负载运行。"
                status = "voltage_high"

            details = {
                "status": status,
                "title": title,
                "reason": reason,
                "suggestion": suggestion,
                "metric_name": "电压",
                "value": value,
                "unit": "V",
                "abnormal_sensor": sensor,
                "abnormal_rule": rule,
                "checked": checked,
                "sensors": voltage_sensors,
            }

            return value, details

        if checked:
            # 取第一个已匹配规则的电压作为展示值。
            first = checked[0]
            value = round(float(first["value"]), 3)
            details = {
                "status": "ok",
                "title": "电压正常",
                "reason": "已匹配的电压传感器均处于配置的安全范围内。",
                "suggestion": "无需处理。",
                "metric_name": "电压",
                "value": value,
                "unit": "V",
                "checked": checked,
                "sensors": voltage_sensors,
            }
            return value, details

        # 读到了电压，但没有匹配到规则。此时不报警。
        first_sensor = voltage_sensors[0]
        value = round(float(first_sensor.get("current") or 0), 3)

        details = {
            "status": "ok",
            "title": "电压已读取",
            "reason": "系统读取到了电压传感器，但没有匹配到 voltageRules 里的规则，因此不触发告警。",
            "suggestion": "如果需要监控该电压项，请在 hardware.voltageRules 中添加匹配规则。",
            "metric_name": "电压",
            "value": value,
            "unit": "V",
            "sensors": voltage_sensors,
        }

        return value, details

    def run_powershell_json(self, command: str) -> Any:
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                text=True,
                capture_output=True,
                timeout=10,
                shell=False,
            )

            if proc.returncode != 0:
                return None

            stdout = proc.stdout.strip()

            if not stdout:
                return None

            return json.loads(stdout)

        except Exception:
            return None

    def remove_excluded_sensors(self, sensors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        exclude_keywords = [
            str(x).lower()
            for x in self.config.get("excludeSensorKeywords", [])
        ]

        filtered = []

        for sensor in sensors:
            text = self.sensor_text(sensor)

            if exclude_keywords and any(keyword in text for keyword in exclude_keywords):
                continue

            value = self.safe_float(sensor.get("current"))
            if value is None:
                continue

            filtered.append(sensor)

        return filtered

    def sensor_text(self, sensor: Dict[str, Any]) -> str:
        parts = [
            str(sensor.get("name") or ""),
            str(sensor.get("group") or ""),
            str(sensor.get("source") or ""),
            str(sensor.get("sensor_type") or ""),
            str(sensor.get("identifier") or ""),
            str(sensor.get("parent") or ""),
        ]
        return " ".join(parts).lower()

    def deduplicate_sensors(self, sensors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        result = []

        for sensor in sensors:
            key = (
                str(sensor.get("source")),
                str(sensor.get("sensor_type")),
                str(sensor.get("group")),
                str(sensor.get("name")),
                str(sensor.get("identifier")),
                str(sensor.get("current")),
                str(sensor.get("unit")),
            )

            if key in seen:
                continue

            seen.add(key)
            result.append(sensor)

        return result

    def safe_float(self, value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None
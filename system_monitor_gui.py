#!/usr/bin/env python3
import os
import json
import tkinter as tk
from tkinter import ttk
from collections import deque

import matplotlib.pyplot as plt
import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# 解决 matplotlib 中文显示为方块的问题
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "STHeiti", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False

try:
    import requests
except ImportError:
    requests = None

try:
    import psutil
except ImportError:
    psutil = None

API_BASE = "http://127.0.0.1:8910"
STATE_DIR = os.path.expanduser("~/.nanobot/workspace/guardian/state/pending")

# Windows 磁盘目标
if os.name == "nt":
    _DISK_TARGET = os.environ.get("SystemDrive", "C:") + "\\"
else:
    _DISK_TARGET = "/"


class SystemMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Guardian 本机状态监控器")
        self.root.geometry("900x550")

        self.api_available = False

        # ---- 左侧面板 ----
        self.left_frame = ttk.Frame(root)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        # 连接状态
        self.status_label = ttk.Label(
            self.left_frame, text="状态: 检测中...", foreground="gray"
        )
        self.status_label.pack(anchor="w", pady=(0, 8))

        # 指标标签
        self.cpu_label = ttk.Label(self.left_frame, text="CPU: --%")
        self.cpu_label.pack(anchor="w")
        self.memory_label = ttk.Label(self.left_frame, text="内存: --%")
        self.memory_label.pack(anchor="w")
        self.disk_label = ttk.Label(self.left_frame, text="磁盘: --%")
        self.disk_label.pack(anchor="w")
        self.net_label = ttk.Label(self.left_frame, text="网络: --")
        self.net_label.pack(anchor="w")
        self.gpu_label = ttk.Label(self.left_frame, text="GPU: --")
        self.gpu_label.pack(anchor="w")
        self.temp_label = ttk.Label(self.left_frame, text="CPU温度: --")
        self.temp_label.pack(anchor="w")

        # 告警列表
        self.alert_label = ttk.Label(self.left_frame, text="待处理告警:")
        self.alert_label.pack(anchor="w", pady=(12, 0))
        self.alert_list = tk.Listbox(self.left_frame, height=12, width=32)
        self.alert_list.pack(fill=tk.Y, expand=True)

        # ---- 右侧曲线 ----
        self.right_frame = ttk.Frame(root)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.fig, self.ax = plt.subplots(figsize=(6, 4))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # 历史数据
        self.max_points = 60
        self.cpu_history = deque(maxlen=self.max_points)
        self.memory_history = deque(maxlen=self.max_points)
        self.disk_history = deque(maxlen=self.max_points)

        # 本地降级用
        self.last_bytes_sent = 0
        self.last_bytes_recv = 0

        # 告警刷新计数器 (每 5 次 update_loop 刷新一次告警 ≈ 5s)
        self._alert_tick = 0

        # 启动循环
        self.update_loop()

    # ------------------------------------------------------------------
    # 主循环：每 1s 刷新指标，每 5s 刷新告警
    # ------------------------------------------------------------------
    def update_loop(self):
        self.update_metrics()
        self._alert_tick += 1
        if self._alert_tick >= 5:
            self._alert_tick = 0
            self.update_alerts()
        self.update_plot()
        self.root.after(1000, self.update_loop)

    # ------------------------------------------------------------------
    # 指标更新：API 优先，降级 psutil
    # ------------------------------------------------------------------
    def update_metrics(self):
        data = self._fetch_api_metrics()
        if data is not None:
            self._apply_api_metrics(data)
        else:
            self._apply_local_metrics()

    def _fetch_api_metrics(self):
        if requests is None:
            return None
        try:
            r = requests.get(f"{API_BASE}/guardian/metrics", timeout=2)
            if r.status_code == 200:
                self.api_available = True
                self.status_label.config(text="状态: 已连接 Guardian", foreground="green")
                return r.json()
        except Exception:
            pass
        self.api_available = False
        self.status_label.config(text="状态: 本地模式 (API 未连接)", foreground="orange")
        return None

    def _apply_api_metrics(self, data):
        metrics = data.get("metrics", {})

        cpu = metrics.get("cpu")
        memory = metrics.get("memory")
        disk = metrics.get("disk")
        gpu = metrics.get("gpu")
        network = metrics.get("network")
        cpu_temp = metrics.get("cpu_temperature")

        self.cpu_label.config(text=f"CPU: {cpu:.1f}%" if cpu is not None else "CPU: N/A")
        self.memory_label.config(
            text=f"内存: {memory:.1f}%" if memory is not None else "内存: N/A"
        )
        self.disk_label.config(
            text=f"磁盘: {disk:.1f}%" if disk is not None else "磁盘: N/A"
        )
        self.gpu_label.config(
            text=f"GPU: {gpu:.1f}%" if gpu is not None else "GPU: N/A"
        )
        self.temp_label.config(
            text=f"CPU温度: {cpu_temp:.0f}°C" if cpu_temp is not None else "CPU温度: N/A"
        )

        # 网络：值是延迟(ms)，不是流量
        if network is not None:
            self.net_label.config(text=f"网络延迟: {network:.0f} ms")
        else:
            self.net_label.config(text="网络: N/A")

        # 历史 (仅 cpu / memory / disk 用于绘图)
        self.cpu_history.append(cpu if cpu is not None else 0)
        self.memory_history.append(memory if memory is not None else 0)
        self.disk_history.append(disk if disk is not None else 0)

    def _apply_local_metrics(self):
        if psutil is None:
            self.cpu_label.config(text="CPU: N/A (psutil 未安装)")
            return

        cpu = psutil.cpu_percent(interval=0)
        memory = psutil.virtual_memory().percent
        disk = psutil.disk_usage(_DISK_TARGET).percent

        self.cpu_label.config(text=f"CPU: {cpu:.1f}%")
        self.memory_label.config(text=f"内存: {memory:.1f}%")
        self.disk_label.config(text=f"磁盘: {disk:.1f}%")
        self.gpu_label.config(text="GPU: N/A")
        self.temp_label.config(text="CPU温度: N/A")

        net_io = psutil.net_io_counters()
        if self.last_bytes_sent == 0 and self.last_bytes_recv == 0:
            net_speed = 0
        else:
            net_speed = (
                net_io.bytes_sent
                + net_io.bytes_recv
                - self.last_bytes_sent
                - self.last_bytes_recv
            ) / 1024
        self.net_label.config(text=f"网络: {net_speed:.1f} KB/s")
        self.last_bytes_sent = net_io.bytes_sent
        self.last_bytes_recv = net_io.bytes_recv

        self.cpu_history.append(cpu)
        self.memory_history.append(memory)
        self.disk_history.append(disk)

    # ------------------------------------------------------------------
    # 告警更新：API 优先，降级本地文件
    # ------------------------------------------------------------------
    def update_alerts(self):
        alerts = self._fetch_api_alerts()
        if alerts is None:
            alerts = self._load_local_alerts()

        self.alert_list.delete(0, tk.END)
        for a in alerts[-15:]:
            metric = a.get("metric", "unknown")
            value = a.get("value", "?")
            created = a.get("createdAt", "")
            # 取时间部分显示
            time_part = created.split("T")[-1] if "T" in created else created
            self.alert_list.insert(tk.END, f"[{time_part}] {metric}: {value}")

    def _fetch_api_alerts(self):
        if requests is None:
            return None
        try:
            r = requests.get(f"{API_BASE}/guardian/alerts", timeout=2)
            if r.status_code == 200:
                data = r.json()
                return data.get("alerts", [])
        except Exception:
            pass
        return None

    def _load_local_alerts(self):
        alerts = []
        if not os.path.exists(STATE_DIR):
            return alerts
        try:
            files = sorted(os.listdir(STATE_DIR))
        except OSError:
            return alerts
        for f in files[-15:]:
            try:
                path = os.path.join(STATE_DIR, f)
                with open(path, "r", encoding="utf-8") as fp:
                    alerts.append(json.load(fp))
            except Exception:
                continue
        return alerts

    # ------------------------------------------------------------------
    # 绘图
    # ------------------------------------------------------------------
    def update_plot(self):
        self.ax.clear()
        if self.cpu_history:
            self.ax.plot(list(self.cpu_history), label="CPU %", color="#e74c3c")
        if self.memory_history:
            self.ax.plot(list(self.memory_history), label="内存 %", color="#3498db")
        if self.disk_history:
            self.ax.plot(list(self.disk_history), label="磁盘 %", color="#2ecc71")
        self.ax.set_ylim(0, 100)
        self.ax.set_xlabel("时间点")
        self.ax.set_ylabel("百分比")
        self.ax.legend(loc="upper left")
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw()


if __name__ == "__main__":
    root = tk.Tk()
    app = SystemMonitorGUI(root)
    root.mainloop()

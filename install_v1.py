#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
from pathlib import Path
import socket
import time
import requests

# ==============================
# 配置
# ==============================
EXECUTABLES = {
    "guardian": "dist/Guardian.exe",
    "nanobot": "dist/guardian_bridge.exe",
    "strategy_runner": "dist/strategy_runner.exe",
    "gui": "dist/system_monitor_gui.exe"
}
CONFIG_FILE = "guardian_config.json"

def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if is_admin():
    INSTALL_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Guardian"
else:
    INSTALL_DIR = Path.home() / ".guardian"

# ==============================
# 工具函数
# ==============================
def ensure_install_dir():
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] 安装目录: {INSTALL_DIR}")

def copy_files():
    for name, exe in EXECUTABLES.items():
        src = Path(exe).resolve()
        dst = INSTALL_DIR / Path(exe).name
        shutil.copy2(src, dst)
        print(f"[INFO] 拷贝 {exe} 到安装目录: {dst}")

    cfg_src = Path(CONFIG_FILE).resolve()
    cfg_dst = INSTALL_DIR / CONFIG_FILE
    shutil.copy2(cfg_src, cfg_dst)
    print(f"[INFO] 拷贝配置文件到安装目录: {cfg_dst}")

def register_guardian_startup():
    try:
        import winreg
        exe_path = str(INSTALL_DIR / Path(EXECUTABLES["guardian"]).name)
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
            winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "Guardian", 0, winreg.REG_SZ, exe_path)
        key.Close()
        print(f"[INFO] 注册 Guardian.exe 开机启动: {exe_path}")
    except Exception as e:
        print(f"[WARN] 注册开机启动失败: {e}")

def wait_for_port(host, port, timeout=10):
    start = time.time()
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((host, port))
            s.close()
            print(f"[INFO] Nanobot 端口 {port} 已就绪")
            return True
        except ConnectionRefusedError:
            s.close()
            if time.time() - start > timeout:
                print(f"[ERROR] Nanobot 端口 {port} 在 {timeout}s 内未就绪")
                return False
            time.sleep(1)

def start_nanobot():
    nanobot_path = str(INSTALL_DIR / Path(EXECUTABLES["nanobot"]).name)
    config_path = str(INSTALL_DIR / CONFIG_FILE)
    print(f"[INFO] 启动 Nanobot: {nanobot_path}")
    process = subprocess.Popen([nanobot_path, "--config", config_path], cwd=str(INSTALL_DIR))
    wait_for_port("127.0.0.1", 8910, timeout=10)
    return process

def notify_with_retry(alert_url, data, retries=3, delay=2):
    for attempt in range(retries):
        try:
            r = requests.post(alert_url, json=data, timeout=5)
            r.raise_for_status()
            print("[INFO] Nanobot notify success")
            return True
        except requests.RequestException as e:
            print(f"[WARN] Nanobot notify attempt {attempt+1} failed: {e}")
            time.sleep(delay)
    print("[ERROR] Nanobot notify failed after retries")
    return False

def start_guardian():
    guardian_path = str(INSTALL_DIR / Path(EXECUTABLES["guardian"]).name)
    config_path = str(INSTALL_DIR / CONFIG_FILE)
    print(f"[INFO] 启动 GuardianMonitor: {guardian_path}")
    subprocess.Popen([guardian_path, "--config", config_path], cwd=str(INSTALL_DIR))

def start_gui():
    gui_path = str(INSTALL_DIR / Path(EXECUTABLES["gui"]).name)
    print(f"[INFO] 启动 监控界面: {gui_path}")
    subprocess.Popen([gui_path], cwd=str(INSTALL_DIR))

# ==============================
# 主流程
# ==============================
def main():
    print("[INFO] 开始 Guardian 安装器")
    print(f"[INFO] 安装目录: {INSTALL_DIR}")

    ensure_install_dir()
    copy_files()
    register_guardian_startup()

    # 启动 Nanobot
    nanobot_process = start_nanobot()

    # 启动 GuardianMonitor
    start_guardian()

    # 启动监控界面
    start_gui()

    print("[INFO] 安装完成，Guardian + Nanobot + 监控界面 已启动")

if __name__ == "__main__":
    main()
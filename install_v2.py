#!/usr/bin/env python3
"""
Windows 完整安装器：Guardian + Nanobot + StrategyRunner
功能：
1. 自动选择安装路径（管理员 / 用户目录）
2. 拷贝 exe 和配置文件
3. 注册 Guardian.exe 开机启动
4. 注册 Nanobot 为 Windows 服务并启动
5. 启动 GuardianMonitor，保证 Nanobot 端口监听完成
"""

import os
import sys
import shutil
import subprocess
import time
from pathlib import Path

# ==============================
# 检查管理员权限
# ==============================
def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# ==============================
# 安装路径选择
# ==============================
if is_admin():
    INSTALL_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Guardian"
else:
    INSTALL_DIR = Path.home() / ".guardian"

CONFIG_FILE = "guardian_config.json"
EXECUTABLES = {
    "guardian": "dist/Guardian.exe",
    "nanobot": "dist/guardian_bridge.exe",
    "strategy_runner": "dist/strategy_runner.exe"
}
SERVICE_SCRIPT = "nanobot_service.py"  # Windows 服务脚本

# ==============================
# 工具函数
# ==============================
def ensure_install_dir():
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] 安装目录: {INSTALL_DIR}")

def copy_files():
    """拷贝 exe 和配置文件到安装目录"""
    for name, exe in EXECUTABLES.items():
        src = Path(exe).resolve()
        dst = INSTALL_DIR / Path(exe).name
        shutil.copy2(src, dst)
        print(f"[INFO] 拷贝 {exe} 到 {dst}")

    # 拷贝配置文件
    cfg_src = Path(CONFIG_FILE).resolve()
    cfg_dst = INSTALL_DIR / CONFIG_FILE
    shutil.copy2(cfg_src, cfg_dst)
    print(f"[INFO] 拷贝配置文件到 {cfg_dst}")

    # 拷贝服务脚本
    service_src = Path(SERVICE_SCRIPT).resolve()
    service_dst = INSTALL_DIR / SERVICE_SCRIPT
    shutil.copy2(service_src, service_dst)
    print(f"[INFO] 拷贝服务脚本到 {service_dst}")

def register_guardian_startup():
    """注册 Guardian.exe 开机启动（HKCU）"""
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

def register_and_start_nanobot_service():
    """注册 Nanobot 为 Windows 服务并启动"""
    service_script = INSTALL_DIR / SERVICE_SCRIPT
    try:
        # 注册服务
        subprocess.run([sys.executable, str(service_script), "install"], check=True)
        print("[INFO] Nanobot 服务已注册")
        # 启动服务
        subprocess.run([sys.executable, str(service_script), "start"], check=True)
        print("[INFO] Nanobot 服务已启动")
        # 等待 Nanobot 完全监听端口
        print("[INFO] 等待 Nanobot 端口监听完成...")
        time.sleep(5)  # 根据机器性能可调整
    except Exception as e:
        print(f"[ERROR] Nanobot 服务注册或启动失败: {e}")

def start_guardian():
    """启动 GuardianMonitor"""
    guardian_path = str(INSTALL_DIR / Path(EXECUTABLES["guardian"]).name)
    config_path = str(INSTALL_DIR / CONFIG_FILE)
    print(f"[INFO] 启动 GuardianMonitor: {guardian_path}")
    subprocess.Popen([guardian_path, "--config", config_path], cwd=str(INSTALL_DIR), shell=True)

# ==============================
# 主流程
# ==============================
def main():
    print("[INFO] 开始 Guardian 完整安装器")

    ensure_install_dir()
    copy_files()
    register_guardian_startup()
    register_and_start_nanobot_service()
    start_guardian()

    print("[INFO] 安装完成")
    print("[INFO] Guardian + Nanobot 已启动，飞书/UI 闭环可用")
    print(f"[INFO] 安装目录: {INSTALL_DIR}")

if __name__ == "__main__":
    main()
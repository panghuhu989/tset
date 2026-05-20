#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Guardian Bridge (Nanobot)")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "guardian_config.json"),
        help="Path to guardian_config.json",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8910, help="Listen port")
    args = parser.parse_args()

    # 读取配置文件，将飞书凭据注入到环境中供 command_handler 使用
    config_path = Path(args.config)
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        feishu_cfg = config.get("feishu", {})
        import os
        if feishu_cfg.get("appId"):
            os.environ.setdefault("FEISHU_APP_ID", feishu_cfg["appId"])
        if feishu_cfg.get("appSecret"):
            os.environ.setdefault("FEISHU_APP_SECRET", feishu_cfg["appSecret"])

    from guardian.command_handler import app  # noqa: F401 - import after env is set
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
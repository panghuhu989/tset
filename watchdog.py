#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from guardian.config import load_config
from guardian.monitor import GuardianMonitor


def main() -> int:
    parser = argparse.ArgumentParser(description="Computer Guardian watchdog")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "guardian_config.json"),
        help="Path to guardian_config.json",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    monitor = GuardianMonitor(config)
    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""Cross-platform launcher for PulseCare Voice Desk (macOS, Linux, Windows)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def venv_python() -> str:
    win_py = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    unix_py = ROOT_DIR / ".venv" / "bin" / "python"
    if os.name == "nt" and win_py.exists():
        return str(win_py)
    if unix_py.exists():
        return str(unix_py)
    return sys.executable


def ensure_env() -> None:
    env_file = ROOT_DIR / ".env"
    example_file = ROOT_DIR / ".env.example"
    if not env_file.exists() and example_file.exists():
        shutil.copy2(example_file, env_file)
        print(f"Created {env_file} from .env.example — fill in your API keys for live WebRTC voice.")


def main() -> int:
    parser = argparse.ArgumentParser(description="PulseCare Voice Desk cross-platform runner")
    parser.add_argument(
        "command",
        choices=["serve", "agent", "test", "bench", "init-env"],
        help="Target command to run",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8000")
    args = parser.parse_args()

    py = venv_python()
    if args.command == "init-env":
        ensure_env()
        return 0
    if args.command == "serve":
        cmd = [py, "-m", "uvicorn", "backend.main:app", "--host", args.host, "--port", str(args.port)]
    elif args.command == "agent":
        cmd = [py, "-m", "agent.main", "dev"]
    elif args.command == "test":
        cmd = [py, "-m", "pytest", "-v"]
    elif args.command == "bench":
        cmd = [py, str(ROOT_DIR / "scripts" / "benchmark_tools.py")]
    else:
        return 1

    return subprocess.call(cmd, cwd=str(ROOT_DIR))


if __name__ == "__main__":
    raise SystemExit(main())

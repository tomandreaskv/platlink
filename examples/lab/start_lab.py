"""Starter en komplett PlatLink v1.0-labøkt med én kommando.

Starter:
1. Lokal TCP-buss (`bus_broker.py`) i bakgrunnen
2. Simulerte noder (`sim_nodes.py`) i bakgrunnen etter at porten er klar
3. Valgfritt lokalt web-GUI (`web_lab.py`) som åpner nettleseren
4. Interaktiv CLI (`interactive_cli.py`) i forgrunnen

Når du avslutter CLI-en (eller trykker Ctrl+C), ryddes bakgrunnsprosessene opp.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def terminate_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
    print(f"[LAB] Stoppet {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start komplett PlatLink v1.0-labøkt")
    parser.add_argument("--host", default="127.0.0.1", help="TCP-buss host (standard: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=4571, help="TCP-buss port (standard: 4571)")
    parser.add_argument("--interval", type=float, default=2.0, help="Telemetri-intervall i sekunder")
    parser.add_argument("--no-noise", action="store_true", help="Skru av støynoden")
    parser.add_argument("--web", action="store_true", help="Start også lokalt web-GUI (`web_lab.py`)")
    parser.add_argument("--web-port", type=int, default=8080, help="Port for web-GUI (standard: 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Ikke åpne nettleser automatisk med --web")
    args = parser.parse_args()

    python_exe = sys.executable
    broker_script = LAB_DIR / "bus_broker.py"
    nodes_script = LAB_DIR / "sim_nodes.py"
    web_script = LAB_DIR / "web_lab.py"
    cli_script = LAB_DIR / "interactive_cli.py"

    broker_proc: subprocess.Popen | None = None
    nodes_proc: subprocess.Popen | None = None
    web_proc: subprocess.Popen | None = None

    try:
        print(f"[LAB] Starter TCP-buss på {args.host}:{args.port}...")
        broker_proc = subprocess.Popen(
            [python_exe, str(broker_script), "--host", args.host, "--port", str(args.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

        if not wait_for_port(args.host, args.port, timeout=5.0):
            raise RuntimeError(f"TCP-bussen svarte ikke på {args.host}:{args.port} innen tidsfristen.")

        print("[LAB] Starter simulerte noder...")
        node_args = [
            python_exe,
            str(nodes_script),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--interval",
            str(args.interval),
        ]
        if args.no_noise:
            node_args.append("--no-noise")

        nodes_proc = subprocess.Popen(node_args)
        time.sleep(0.3)

        if args.web:
            print(f"[LAB] Starter web-GUI på http://{args.host}:{args.web_port}/...")
            web_args = [
                python_exe,
                str(web_script),
                "--bus-host",
                args.host,
                "--bus-port",
                str(args.port),
                "--web-host",
                args.host,
                "--web-port",
                str(args.web_port),
            ]
            if args.no_browser:
                web_args.append("--no-browser")
            web_proc = subprocess.Popen(web_args)
            time.sleep(0.3)

        print("[LAB] Starter interaktiv CLI...\n")
        subprocess.run(
            [python_exe, str(cli_script), "--host", args.host, "--port", str(args.port)],
            check=False,
        )
    except KeyboardInterrupt:
        print("\n[LAB] Avbrutt av bruker.")
    finally:
        terminate_process(web_proc, "web_lab")
        terminate_process(nodes_proc, "sim_nodes")
        terminate_process(broker_proc, "bus_broker")


if __name__ == "__main__":
    main()
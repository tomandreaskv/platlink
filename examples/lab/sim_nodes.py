"""Simulerte PlatLink v1.0-noder som snakker over TCP med ekte pakker fra `platlink`.

Støtter live veksling mellom MANUELL (rolig buss) og AUTO (periodisk telemetri
og linjestøy) via kontrollpakke på bussen (SYS_DISCOVERY, sub_id=0x10, u32=0|1).
`LoggerNode` reassemblerer chunkede config-/OTA-overføringer, verifiserer CRC32,
oppdaterer tilstand/partisjon, og sender status-/feilpakker tilbake på bussen.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SRC = REPO_ROOT / "python"
if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))

from platlink import (  # noqa: E402
    CAT_EC,
    CAT_PH,
    CAT_SYSTEM,
    CAT_TEMPERATURE,
    CAT_WATER_LEVEL,
    FMT_DIGITAL,
    FMT_RAW32,
    HEADER,
    PACKET_SIZE,
    SYS_DISCOVERY,
    SYS_HEARTBEAT,
    SYS_OTA_CTRL,
    SYS_OTA_DATA,
    SYS_RESET,
    PlatLinkError,
    create_fixed32,
    create_u32,
    from_bytes,
    make_type_id,
)

MODE_CTRL_SUB_ID = 0x10

# Status-SubID-er som LoggerNode sender på SYS_HEARTBEAT / SYS_OTA_CTRL
OTA_STATUS_READY = 0x20
OTA_STATUS_OK = 0x21
OTA_STATUS_ERR_SIZE = 0xE1
OTA_STATUS_ERR_CRC = 0xE2


class StreamFramer:
    """Synkroniserer 8-byte PlatLink-pakker over en rå byte-strøm."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[bytes, object]]:
        self._buf.extend(data)
        out: list[tuple[bytes, object]] = []
        while True:
            idx = self._buf.find(bytes([HEADER]))
            if idx == -1:
                self._buf.clear()
                break
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < PACKET_SIZE:
                break
            candidate = bytes(self._buf[:PACKET_SIZE])
            try:
                pkt = from_bytes(candidate)
            except PlatLinkError:
                del self._buf[0]
                continue
            out.append((candidate, pkt))
            del self._buf[:PACKET_SIZE]
        return out


class BaseTcpNode:
    def __init__(self, name: str, host: str, port: int) -> None:
        self.name = name
        self.host = host
        self.port = port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=5.0)
        sock.settimeout(0.25)
        self._sock = sock

    def send_raw(self, raw: bytes) -> None:
        if self._sock is None:
            return
        with self._send_lock:
            self._sock.sendall(raw)

    def send_packet(self, pkt) -> None:
        self.send_raw(pkt.to_bytes())

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


class SensorNode(BaseTcpNode):
    """Sensornode som lytter etter AUTO/MANUELL-modus og sender periodisk når AUTO er på."""

    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        interval: float = 2.0,
        auto_send: bool = False,
    ) -> None:
        super().__init__(name, host, port)
        self.interval = interval
        self.auto_send = auto_send
        self.uptime_s = 0
        self._tick = 0

    def emit_sample(self) -> None:
        temp_c = 21.8 + 0.9 * math.sin(self._tick * 0.4) + random.uniform(-0.05, 0.05)
        ph_val = 6.15 + 0.12 * math.cos(self._tick * 0.25)
        ec_val = 1240.0 + 35.0 * math.sin(self._tick * 0.2)
        level_cm = 45 + (self._tick % 5)

        self.send_packet(create_fixed32(CAT_TEMPERATURE, 0x01, temp_c))
        self.send_packet(create_fixed32(CAT_PH, 0x02, ph_val))
        self.send_packet(create_fixed32(CAT_EC, 0x03, ec_val))
        self.send_packet(
            create_u32(make_type_id(FMT_RAW32, CAT_WATER_LEVEL), 0x04, level_cm)
        )

        self._tick += 1
        self.uptime_s += int(round(max(1.0, self.interval)))
        if self._tick % 3 == 0:
            self.send_packet(create_u32(SYS_HEARTBEAT, 0x01, self.uptime_s))

    def _rx_ctrl_loop(self) -> None:
        framer = StreamFramer()
        while not self._stop.is_set():
            if self._sock is None:
                break
            try:
                chunk = self._sock.recv(1024)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            for _, pkt in framer.feed(chunk):
                if pkt.type_id == SYS_DISCOVERY and pkt.sub_id == MODE_CTRL_SUB_ID:
                    enabled = bool(pkt.as_u32())
                    self.auto_send = enabled
                    print(
                        f"[{self.name}] Modus endret -> {'AUTO' if enabled else 'MANUELL/ROLIG'}"
                    )

    def run_loop(self) -> None:
        self.connect()
        mode_str = f"AUTO (intervall={self.interval}s)" if self.auto_send else "MANUELL/ROLIG"
        print(f"[{self.name}] Sensornode klar [{mode_str}]")
        rx_t = threading.Thread(target=self._rx_ctrl_loop, daemon=True)
        rx_t.start()
        try:
            while not self._stop.is_set():
                if not self.auto_send:
                    self._stop.wait(0.3)
                    continue
                t = time.monotonic()
                self.emit_sample()
                elapsed = time.monotonic() - t
                sleep_left = max(0.05, self.interval - elapsed)
                self._stop.wait(sleep_left)
        finally:
            self.stop()


class RelayNode(BaseTcpNode):
    """Lytter etter Digital-kommandoer (format 0x1) og Reset (0xFF)."""

    def __init__(self, name: str, host: str, port: int, channels: int = 4) -> None:
        super().__init__(name, host, port)
        self.states: dict[int, bool] = {ch: False for ch in range(1, channels + 1)}

    def run_loop(self) -> None:
        self.connect()
        framer = StreamFramer()
        print(f"[{self.name}] Relénode klar (kanaler={list(self.states.keys())})")
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._sock.recv(1024) if self._sock else b""
                except TimeoutError:
                    continue
                except OSError:
                    break

                if not chunk:
                    break

                for _, pkt in framer.feed(chunk):
                    if pkt.type_id == SYS_RESET:
                        for ch in self.states:
                            self.states[ch] = False
                        print(f"[{self.name}] Mottok RESET (sub_id=0x{pkt.sub_id:02X}) -> alle kanaler AV")
                    elif pkt.format_code == FMT_DIGITAL:
                        ch = pkt.sub_id if pkt.sub_id in self.states else 1
                        new_state = pkt.as_digital()
                        self.states[ch] = new_state
                        print(
                            f"[{self.name}] Kanal {ch} satt til {'PÅ (1)' if new_state else 'AV (0)'}"
                        )
        finally:
            self.stop()


class LoggerNode(BaseTcpNode):
    """Simulert datalogger som tar imot chunkede config- og OTA-overføringer,
    verifiserer CRC32, oppdaterer partisjon/config og sender status på bussen."""

    def __init__(self, name: str, host: str, port: int) -> None:
        super().__init__(name, host, port)
        self.expected_size: int | None = None
        self.chunks: bytearray = bytearray()
        self.config: dict = {"interval_s": 5, "target_ph": 6.2, "log_mode": "normal"}
        self.running_partition: str = "ota_0"
        self.firmware_version: str = "1.0.0"

    def _reset_transfer(self) -> None:
        self.expected_size = None
        self.chunks.clear()

    def _send_status(self, sub_id: int, code_u32: int) -> None:
        self.send_packet(create_u32(make_type_id(FMT_RAW32, CAT_SYSTEM), sub_id, code_u32))

    def _finalize_transfer(self, expected_crc32: int) -> None:
        payload = bytes(self.chunks)
        actual_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
        size_ok = self.expected_size is None or len(payload) == self.expected_size
        crc_ok = actual_crc32 == expected_crc32

        if not size_ok:
            print(
                f"[{self.name}] STØRRELSESFEIL: mottok {len(payload)}B, "
                f"forventet {self.expected_size}B"
            )
            self._send_status(OTA_STATUS_ERR_SIZE, len(payload) & 0xFFFFFFFF)
            self._reset_transfer()
            return

        if not crc_ok:
            print(
                f"[{self.name}] CRC32 FEILET: beregnet 0x{actual_crc32:08X}, "
                f"forventet 0x{expected_crc32:08X}"
            )
            self._send_status(OTA_STATUS_ERR_CRC, actual_crc32)
            self._reset_transfer()
            return

        text_preview = payload.decode("utf-8", errors="ignore")
        if text_preview.lstrip().startswith("{"):
            try:
                parsed = json.loads(text_preview)
                if isinstance(parsed, dict):
                    self.config.update(parsed)
                    new_int = int(self.config.get("interval_s", 5))
                    print(
                        f"[{self.name}] NY CONFIG AKTIVERT ({len(payload)}B, CRC32 OK): {self.config}"
                    )
                    self._send_status(OTA_STATUS_OK, new_int & 0xFFFFFFFF)
                    self._reset_transfer()
                    return
            except json.JSONDecodeError:
                pass

        # Sjekk eventuell PLFW-header ("PLFW" + 16B versjon + 4B lengde)
        parsed_ver = f"1.0.{int(time.time()) % 90 + 10}"
        if len(payload) >= 24 and payload[:4] == b"PLFW":
            raw_ver = payload[4:20].rstrip(b"\x00").decode("ascii", errors="replace").strip()
            if raw_ver:
                parsed_ver = raw_ver
            _magic, _ver, _plen = struct.unpack(">4s16sI", payload[:24])

        target_part = "ota_1" if self.running_partition == "ota_0" else "ota_0"
        print(
            f"[{self.name}] OTA VERIFISERT ({len(payload)}B, CRC32=0x{actual_crc32:08X}) "
            f"-> bytter partisjon {self.running_partition} => {target_part} og restarter..."
        )
        time.sleep(0.15)
        self.running_partition = target_part
        self.firmware_version = parsed_ver
        ver_num = sum(ord(ch) for ch in parsed_ver) & 0xFFFF
        status_word = ((1 if target_part == "ota_1" else 0) << 16) | ver_num
        self._send_status(OTA_STATUS_OK, status_word)
        print(
            f"[{self.name}] OPPDATERING FULLFØRT: kjører nå på {self.running_partition} "
            f"(fw=v{self.firmware_version})"
        )
        self._reset_transfer()

    def run_loop(self) -> None:
        self.connect()
        framer = StreamFramer()
        print(
            f"[{self.name}] Datalogger klar (part={self.running_partition}, "
            f"fw=v{self.firmware_version}, config={self.config})"
        )
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._sock.recv(1024) if self._sock else b""
                except TimeoutError:
                    continue
                except OSError:
                    break

                if not chunk:
                    break

                for _, pkt in framer.feed(chunk):
                    if pkt.type_id == SYS_OTA_CTRL:
                        if pkt.sub_id == 0x01:
                            self.expected_size = pkt.as_u32()
                            self.chunks.clear()
                            target_part = "ota_1" if self.running_partition == "ota_0" else "ota_0"
                            print(
                                f"[{self.name}] Mottok OTA/CONFIG INIT: "
                                f"{self.expected_size} byte -> klargjør {target_part}"
                            )
                            self._send_status(OTA_STATUS_READY, self.expected_size)
                        elif pkt.sub_id == 0x03:
                            self._finalize_transfer(pkt.as_u32())
                    elif pkt.type_id == SYS_OTA_DATA:
                        if self.expected_size is None:
                            self.expected_size = 0
                            self.chunks.clear()
                        remaining = (
                            self.expected_size - len(self.chunks)
                            if self.expected_size > 0
                            else len(pkt.payload)
                        )
                        take = min(len(pkt.payload), max(0, remaining))
                        self.chunks.extend(pkt.payload[:take])
        finally:
            self.stop()


class NoisyNode(BaseTcpNode):
    """Støynode som lytter etter AUTO/MANUELL-modus og sender støy når AUTO er på."""

    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        interval: float = 5.0,
        auto_send: bool = False,
    ) -> None:
        super().__init__(name, host, port)
        self.interval = interval
        self.auto_send = auto_send

    def _rx_ctrl_loop(self) -> None:
        framer = StreamFramer()
        while not self._stop.is_set():
            if self._sock is None:
                break
            try:
                chunk = self._sock.recv(1024)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            for _, pkt in framer.feed(chunk):
                if pkt.type_id == SYS_DISCOVERY and pkt.sub_id == MODE_CTRL_SUB_ID:
                    self.auto_send = bool(pkt.as_u32())

    def run_loop(self) -> None:
        self.connect()
        mode_str = f"AUTO (intervall={self.interval}s)" if self.auto_send else "MANUELL/ROLIG"
        print(f"[{self.name}] Støynode klar [{mode_str}]")
        rx_t = threading.Thread(target=self._rx_ctrl_loop, daemon=True)
        rx_t.start()
        try:
            while not self._stop.is_set():
                if not self.auto_send:
                    self._stop.wait(0.3)
                    continue
                self._stop.wait(self.interval)
                if self._stop.is_set() or not self.auto_send:
                    break
                noise = bytes([random.randint(0x00, 0xFF) for _ in range(4)])
                self.send_raw(noise)

                good = create_fixed32(CAT_TEMPERATURE, 0x09, 99.99).to_bytes()
                bad_crc = bytearray(good)
                bad_crc[-1] ^= 0xFF
                self.send_raw(bytes(bad_crc))
                print(f"[{self.name}] Sendte 4B støy + 1 pakke med ugyldig CRC-8")
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Kjør simulerte PlatLink v1.0-noder mot TCP-buss")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4571)
    parser.add_argument("--interval", type=float, default=2.0, help="Telemetri-intervall ved --auto")
    parser.add_argument("--auto", action="store_true", help="Skru på automatisk periodisk sending")
    parser.add_argument("--no-noise", action="store_true", help="Skru av støynoden")
    args = parser.parse_args()

    nodes: list[BaseTcpNode] = [
        SensorNode("sensor-1", args.host, args.port, interval=args.interval, auto_send=args.auto),
        RelayNode("relay-1", args.host, args.port, channels=4),
        LoggerNode("logger-1", args.host, args.port),
    ]
    if not args.no_noise:
        nodes.append(
            NoisyNode(
                "noise-1",
                args.host,
                args.port,
                interval=max(4.0, args.interval * 2.5),
                auto_send=args.auto,
            )
        )

    threads: list[threading.Thread] = []
    for node in nodes:
        t = threading.Thread(target=node.run_loop, name=node.name, daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[LAB] Stopper simulerte noder...")
        for node in nodes:
            node.stop()


if __name__ == "__main__":
    main()
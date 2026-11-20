"""Lokalt web-GUI for PlatLink v1.0-laben koblet til den ekte TCP-bussen.

Starter en lokal HTTP-server (kun standardbibliotek), leser frontend-malen fra
`web_lab.html`, delegerer firmware-/OTA-logikk til `ota_sim.py` og snakker
med TCP-bussen via `platlink`.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SRC = REPO_ROOT / "python"
LAB_DIR = Path(__file__).resolve().parent
for p in (PYTHON_SRC, LAB_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ota_sim import (  # noqa: E402
    OTA_STATUS_ERR_CRC,
    OTA_STATUS_ERR_SIZE,
    OTA_STATUS_OK,
    OTA_STATUS_READY,
    LoggerReassembler,
    build_realistic_firmware_image,
)
from platlink import (  # noqa: E402
    CAT_EC,
    CAT_HUMIDITY,
    CAT_LIGHT,
    CAT_PH,
    CAT_PRESSURE,
    CAT_SYSTEM,
    CAT_TEMPERATURE,
    CAT_WATER_LEVEL,
    FMT_ANALOG8,
    FMT_DIGITAL,
    FMT_FIXED32,
    FMT_INT32,
    FMT_RAW32,
    FMT_SYSTEM,
    HEADER,
    PACKET_SIZE,
    SYS_DISCOVERY,
    SYS_HEARTBEAT,
    SYS_OTA_CTRL,
    SYS_OTA_DATA,
    SYS_RESET,
    OtaTransfer,
    PlatLinkError,
    PlatLinkPacket,
    create_digital,
    create_fixed32,
    create_u32,
    from_bytes,
    make_type_id,
)

HTML_PATH = LAB_DIR / "web_lab.html"
ARCHIVE_DIR = LAB_DIR / "archives"
MODE_CTRL_SUB_ID = 0x10
MAX_TRANSFER_BYTES = 2 * 1024 * 1024

CATEGORIES = {
    "temperature": ("Temperatur (°C)", CAT_TEMPERATURE),
    "humidity": ("Fukt (%)", CAT_HUMIDITY),
    "light": ("Lys", CAT_LIGHT),
    "ph": ("pH", CAT_PH),
    "ec": ("EC (µS/cm)", CAT_EC),
    "water_level": ("Vannstand (cm)", CAT_WATER_LEVEL),
    "pressure": ("Trykk", CAT_PRESSURE),
}

FORMAT_NAMES = {
    FMT_DIGITAL: "Digital",
    FMT_ANALOG8: "Analog8",
    FMT_FIXED32: "Fixed32",
    FMT_RAW32: "Raw32",
    FMT_INT32: "Int32",
    FMT_SYSTEM: "System",
}

CATEGORY_NAMES = {
    CAT_TEMPERATURE: "Temperatur",
    CAT_HUMIDITY: "Fukt",
    CAT_LIGHT: "Lys",
    CAT_PH: "pH",
    CAT_EC: "EC",
    CAT_WATER_LEVEL: "Vannstand",
    CAT_PRESSURE: "Trykk",
    CAT_SYSTEM: "System",
}

SAMPLE_CONFIG_JSON = json.dumps(
    {"interval_s": 10, "target_ph": 6.0, "log_mode": "burst"},
    separators=(",", ":"),
)


def decode_packet_info(
    raw: bytes,
    pkt: PlatLinkPacket,
    direction: str,
    batch_id: str | None = None,
) -> dict:
    fmt_name = FORMAT_NAMES.get(pkt.format_code, f"0x{pkt.format_code:X}")
    cat_name = CATEGORY_NAMES.get(pkt.category_code, f"0x{pkt.category_code:X}")

    ascii_bytes = bytes(b for b in pkt.payload if 32 <= b <= 126)
    ascii_hint = f' "{ascii_bytes.decode("ascii")}"' if len(ascii_bytes) >= 2 else ""
    kind = "ok"

    if pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM and pkt.sub_id == OTA_STATUS_READY:
        decoded = f"Logger status: KLAR for {pkt.as_u32()} byte"
    elif pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM and pkt.sub_id == OTA_STATUS_OK:
        val = pkt.as_u32()
        part_name = "ota_1" if ((val >> 16) & 1) else "ota_0"
        decoded = f"Logger status: OK ({part_name}, info={val})"
    elif pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM and pkt.sub_id == OTA_STATUS_ERR_SIZE:
        decoded = f"Logger FEIL: Størrelsesavvik (mottok {pkt.as_u32()} B)"
        kind = "error"
    elif pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM and pkt.sub_id == OTA_STATUS_ERR_CRC:
        decoded = f"Logger FEIL: CRC32-mismatch (beregnet 0x{pkt.as_u32():08X})"
        kind = "error"
    elif pkt.type_id == SYS_DISCOVERY and pkt.sub_id == MODE_CTRL_SUB_ID:
        decoded = f"Modus-kontroll -> {'AUTO' if pkt.as_u32() else 'MANUELL'}"
    elif pkt.type_id == SYS_HEARTBEAT:
        decoded = f"Heartbeat: {pkt.as_u32()} s"
    elif pkt.type_id == SYS_DISCOVERY:
        decoded = f"Discovery (u32={pkt.as_u32()})"
    elif pkt.type_id == SYS_RESET:
        decoded = f"Reset (sub=0x{pkt.sub_id:02X})"
    elif pkt.type_id == SYS_OTA_CTRL:
        cmd = (
            "INIT"
            if pkt.sub_id == 0x01
            else "VERIFY"
            if pkt.sub_id == 0x03
            else f"0x{pkt.sub_id:02X}"
        )
        decoded = f"OTA Ctrl [{cmd}]: {pkt.as_u32()}"
    elif pkt.type_id == SYS_OTA_DATA:
        decoded = f"OTA Data [seq={pkt.sub_id}]: {pkt.payload.hex().upper()}{ascii_hint}"
    elif pkt.format_code == FMT_FIXED32:
        decoded = f"{pkt.as_fixed32_float():.2f}"
    elif pkt.format_code == FMT_DIGITAL:
        decoded = "PÅ (1)" if pkt.as_digital() else "AV (0)"
    elif pkt.format_code == FMT_ANALOG8:
        decoded = f"{pkt.as_analog8()}"
    elif pkt.format_code == FMT_INT32:
        decoded = f"{pkt.as_i32()}"
    else:
        decoded = f"{pkt.as_u32()}{ascii_hint}"

    ev = {
        "ts": time.strftime("%H:%M:%S"),
        "dir": direction,
        "kind": kind,
        "hex": raw.hex().upper(),
        "type_id": f"0x{pkt.type_id:02X}",
        "format": fmt_name,
        "category": cat_name,
        "sub_id": f"0x{pkt.sub_id:02X}",
        "decoded": decoded,
    }
    if batch_id:
        ev["batch_id"] = batch_id
    return ev


class PlatLinkLabBridge:
    """Kobler HTTP-serveren til PlatLink TCP-bussen og holder en buffer av hendelser."""

    def __init__(self, bus_host: str, bus_port: int, max_events: int = 500) -> None:
        self.bus_host = bus_host
        self.bus_port = bus_port
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=max_events)
        self._next_id = 1
        self._batch_seq = 1
        self._fw_minor = 1
        self._stop = threading.Event()
        self._buf = bytearray()
        self._sample_tick = 0

        self.auto_mode: bool = False
        self.relay_channels: dict[int, bool] = {1: False, 2: False, 3: False, 4: False}
        self.stats = {"ok": 0, "tx": 0, "crc_err": 0, "noise_bytes": 0}
        self.reassembler = LoggerReassembler()

    def next_batch_id(self, prefix: str = "OTA") -> str:
        with self._events_lock:
            bid = f"{prefix}-{self._batch_seq}"
            self._batch_seq += 1
            return bid

    def next_fw_version(self) -> str:
        with self._events_lock:
            ver = f"1.{self._fw_minor}.0"
            self._fw_minor += 1
            return ver

    def push_event(self, ev: dict) -> None:
        with self._events_lock:
            ev["id"] = self._next_id
            self._next_id += 1
            self._events.append(ev)

    def get_events_since(self, since_id: int) -> dict:
        with self._events_lock:
            items = [e for e in self._events if e["id"] > since_id]
            last_id = self._events[-1]["id"] if self._events else 0
            return {
                "last_id": last_id,
                "events": items,
                "relays": dict(self.relay_channels),
                "stats": dict(self.stats),
                "auto_mode": self.auto_mode,
                "reassembled": self.reassembler.snapshot(),
            }

    def set_auto_mode(self, enabled: bool) -> None:
        self.auto_mode = bool(enabled)
        pkt = create_u32(SYS_DISCOVERY, MODE_CTRL_SUB_ID, 1 if self.auto_mode else 0)
        self.send_packet(
            pkt,
            f"Bussmodus -> {'AUTO' if self.auto_mode else 'MANUELL'}",
        )

    def clear_events(self, reset_stats: bool = True) -> None:
        with self._events_lock:
            self._events.clear()
            if reset_stats:
                self.stats = {"ok": 0, "tx": 0, "crc_err": 0, "noise_bytes": 0}

    def archive_events(self) -> Path:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        archive_path = ARCHIVE_DIR / f"platlink_lab_{ts}.jsonl"
        with self._events_lock:
            items = list(self._events)
            self._events.clear()
            self.stats = {"ok": 0, "tx": 0, "crc_err": 0, "noise_bytes": 0}

        with archive_path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return archive_path

    def connect_bus(self) -> None:
        sock = socket.create_connection((self.bus_host, self.bus_port), timeout=5.0)
        sock.settimeout(0.25)
        self._sock = sock
        t = threading.Thread(target=self._rx_loop, daemon=True)
        t.start()

    def send_raw(
        self,
        raw: bytes,
        description: str = "TX",
        batch_id: str | None = None,
        record_event: bool = True,
    ) -> None:
        if self._sock is None:
            raise RuntimeError("Ikke tilkoblet TCP-buss.")
        with self._send_lock:
            self._sock.sendall(raw)
        with self._events_lock:
            self.stats["tx"] += 1
        if not record_event:
            return
        try:
            pkt = from_bytes(raw)
            ev = decode_packet_info(raw, pkt, "TX", batch_id=batch_id)
            ev["note"] = description
            self.push_event(ev)
        except PlatLinkError:
            ev = {
                "ts": time.strftime("%H:%M:%S"),
                "dir": "TX",
                "kind": "raw",
                "hex": raw.hex().upper(),
                "type_id": "-",
                "format": "Rå bytes",
                "category": "-",
                "sub_id": "-",
                "decoded": description,
            }
            if batch_id:
                ev["batch_id"] = batch_id
            self.push_event(ev)

    def send_packet(
        self,
        pkt: PlatLinkPacket,
        description: str = "Pakke",
        batch_id: str | None = None,
        record_event: bool = True,
        update_local: bool = True,
    ) -> None:
        raw = pkt.to_bytes()
        self.send_raw(raw, description, batch_id=batch_id, record_event=record_event)
        if update_local:
            self._apply_local_state(pkt)

    def emit_manual_sample_burst(self) -> int:
        temp_c = 21.8 + 0.9 * math.sin(self._sample_tick * 0.4) + random.uniform(-0.05, 0.05)
        ph_val = 6.15 + 0.12 * math.cos(self._sample_tick * 0.25)
        ec_val = 1240.0 + 35.0 * math.sin(self._sample_tick * 0.2)
        level_cm = 45 + (self._sample_tick % 5)
        self._sample_tick += 1

        pkts = [
            create_fixed32(CAT_TEMPERATURE, 0x01, temp_c),
            create_fixed32(CAT_PH, 0x02, ph_val),
            create_fixed32(CAT_EC, 0x03, ec_val),
            create_u32(make_type_id(FMT_RAW32, CAT_WATER_LEVEL), 0x04, level_cm),
        ]
        for p in pkts:
            self.send_packet(p, "Manuell måling")
        return len(pkts)

    def run_transfer_async(
        self,
        blob: bytes,
        label: str,
        corrupt_crc: bool = False,
        step_delay: float = 0.0,
    ) -> None:
        def _worker() -> None:
            try:
                self.send_transfer_blob(
                    blob,
                    label=label,
                    corrupt_crc=corrupt_crc,
                    step_delay=step_delay,
                )
            except Exception as exc:
                self.push_event(
                    {
                        "ts": time.strftime("%H:%M:%S"),
                        "dir": "TX",
                        "kind": "error",
                        "hex": "-",
                        "type_id": "0xFC/0xFD",
                        "format": "Feil",
                        "category": "System",
                        "sub_id": "-",
                        "decoded": f"{label} feilet: {exc}",
                    }
                )

        threading.Thread(target=_worker, daemon=True).start()

    def send_transfer_blob(
        self,
        blob: bytes,
        label: str,
        corrupt_crc: bool = False,
        step_delay: float = 0.0,
    ) -> tuple[str, int]:
        if not blob:
            raise ValueError("Kan ikke sende tom data/fil.")
        if len(blob) > MAX_TRANSFER_BYTES:
            raise ValueError(f"Maks fil-/payloadstørrelse i lab er {MAX_TRANSFER_BYTES} byte.")

        batch_id = self.next_batch_id(label)
        transfer = OtaTransfer(blob)
        chunks = transfer.iter_chunks()
        n_chunks = len(chunks)
        crc_to_send = (transfer.crc32 ^ 0xDEADBEEF) & 0xFFFFFFFF if corrupt_crc else transfer.crc32
        log_each_chunk = n_chunks <= 256

        self.push_event(
            {
                "ts": time.strftime("%H:%M:%S"),
                "dir": "TX",
                "kind": "batch_start",
                "batch_id": batch_id,
                "label": label,
                "size": len(blob),
                "chunks": n_chunks,
                "crc32": f"0x{crc_to_send:08X}",
                "hex": "-",
                "type_id": "0xFC/0xFD",
                "format": "Chunket",
                "category": "System",
                "sub_id": "-",
                "decoded": f"{label} ({len(blob)}B, {n_chunks} chunks, CRC32=0x{crc_to_send:08X})",
            }
        )

        self.send_packet(transfer.init_packet(), f"{label} INIT ({len(blob)}B)", batch_id=batch_id)

        tx_buf = bytearray()
        for idx, ch_item in enumerate(chunks, start=1):
            raw_pkt = ch_item.packet.to_bytes()
            tx_buf.extend(raw_pkt)

            if log_each_chunk:
                ev = decode_packet_info(raw_pkt, ch_item.packet, "TX", batch_id=batch_id)
                ev["note"] = f"{label} DATA seq={ch_item.sub_id}"
                self.push_event(ev)

            if len(tx_buf) >= 16384 or idx == n_chunks:
                if self._sock is None:
                    raise RuntimeError("Ikke tilkoblet TCP-buss.")
                with self._send_lock:
                    self._sock.sendall(bytes(tx_buf))
                n_sent_pkts = len(tx_buf) // PACKET_SIZE
                with self._events_lock:
                    self.stats["tx"] += n_sent_pkts
                tx_buf.clear()
                if step_delay > 0:
                    time.sleep(step_delay)

        verify_pkt = create_u32(SYS_OTA_CTRL, 0x03, crc_to_send)
        self.send_packet(
            verify_pkt,
            f"{label} VERIFY (CRC32=0x{crc_to_send:08X})",
            batch_id=batch_id,
        )

        self.push_event(
            {
                "ts": time.strftime("%H:%M:%S"),
                "dir": "TX",
                "kind": "batch_end",
                "batch_id": batch_id,
                "label": label,
                "size": len(blob),
                "chunks": n_chunks,
                "crc32": f"0x{crc_to_send:08X}",
                "hex": "-",
                "type_id": "0xFC",
                "format": "Fullført",
                "category": "System",
                "sub_id": "0x03",
                "decoded": f"{label} sendt ({len(blob)}B i {n_chunks} chunks)",
            }
        )
        return batch_id, n_chunks

    def _apply_local_state(self, pkt: PlatLinkPacket) -> None:
        if pkt.type_id == SYS_RESET:
            for ch in self.relay_channels:
                self.relay_channels[ch] = False
        elif pkt.format_code == FMT_DIGITAL:
            ch = pkt.sub_id if pkt.sub_id in self.relay_channels else 1
            self.relay_channels[ch] = pkt.as_digital()
        elif pkt.type_id in (SYS_OTA_CTRL, SYS_OTA_DATA) or (
            pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM
        ):
            with self._events_lock:
                self.reassembler.observe_packet(pkt)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            if self._sock is None:
                break
            try:
                data = self._sock.recv(8192)
            except TimeoutError:
                continue
            except OSError:
                break

            if not data:
                break

            self._buf.extend(data)
            while True:
                idx = self._buf.find(bytes([HEADER]))
                if idx == -1:
                    if len(self._buf) > 0:
                        dropped = bytes(self._buf)
                        self._buf.clear()
                        with self._events_lock:
                            self.stats["noise_bytes"] += len(dropped)
                        self.push_event(
                            {
                                "ts": time.strftime("%H:%M:%S"),
                                "dir": "RX",
                                "kind": "noise",
                                "hex": dropped.hex().upper(),
                                "type_id": "-",
                                "format": "Støy",
                                "category": "-",
                                "sub_id": "-",
                                "decoded": f"Forkastet {len(dropped)} byte uten 0xA5",
                            }
                        )
                    break

                if idx > 0:
                    skipped = bytes(self._buf[:idx])
                    del self._buf[:idx]
                    with self._events_lock:
                        self.stats["noise_bytes"] += len(skipped)
                    self.push_event(
                        {
                            "ts": time.strftime("%H:%M:%S"),
                            "dir": "RX",
                            "kind": "noise",
                            "hex": skipped.hex().upper(),
                            "type_id": "-",
                            "format": "Støy",
                            "category": "-",
                            "sub_id": "-",
                            "decoded": f"Hoppet over {len(skipped)} byte før 0xA5",
                        }
                    )

                if len(self._buf) < PACKET_SIZE:
                    break

                candidate = bytes(self._buf[:PACKET_SIZE])
                try:
                    pkt = from_bytes(candidate)
                except PlatLinkError as exc:
                    del self._buf[0]
                    with self._events_lock:
                        self.stats["crc_err"] += 1
                    self.push_event(
                        {
                            "ts": time.strftime("%H:%M:%S"),
                            "dir": "RX",
                            "kind": "error",
                            "hex": candidate.hex().upper(),
                            "type_id": f"0x{candidate[1]:02X}",
                            "format": "CRC-feil",
                            "category": "-",
                            "sub_id": f"0x{candidate[2]:02X}",
                            "decoded": str(exc),
                        }
                    )
                    continue

                del self._buf[:PACKET_SIZE]
                with self._events_lock:
                    self.stats["ok"] += 1
                self._apply_local_state(pkt)
                self.push_event(decode_packet_info(candidate, pkt, "RX"))

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


def make_handler(bridge: PlatLinkLabBridge):
    class LabHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = HTML_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/events":
                since = 0
                if "since=" in parsed.query:
                    try:
                        since = int(parsed.query.split("since=", 1)[1].split("&", 1)[0])
                    except ValueError:
                        since = 0
                self._send_json(200, bridge.get_events_since(since))
                return

            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/api/send":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            try:
                data = json.loads(raw_body.decode("utf-8"))
                action = data.get("action")

                if action == "set_auto_mode":
                    enabled = bool(data.get("enabled", False))
                    bridge.set_auto_mode(enabled)
                    self._send_json(
                        200,
                        {"message": f"Auto-trafikk satt til {'PÅ' if enabled else 'AV'}."},
                    )
                    return

                if action == "clear":
                    bridge.clear_events(reset_stats=True)
                    self._send_json(200, {"message": "Logg og tellere tømt."})
                    return

                if action == "archive":
                    saved_path = bridge.archive_events()
                    self._send_json(
                        200,
                        {"message": f"Arkivert til {saved_path.name} og nullstilt logg."},
                    )
                    return

                if action == "sample_burst":
                    count = bridge.emit_manual_sample_burst()
                    self._send_json(200, {"message": f"Sendte {count} manuelle sensormålinger."})
                    return

                if action == "relay":
                    ch = int(data.get("channel", 1))
                    state = bool(data.get("state", False))
                    pkt = create_digital(CAT_SYSTEM, ch, state)
                    bridge.send_packet(pkt, f"Relé kanal {ch} -> {'PÅ' if state else 'AV'}")
                    self._send_json(200, {"message": f"Sendte Digital-kommando til kanal {ch}."})
                    return

                if action == "reset":
                    sub = int(str(data.get("sub_id", "0")), 0)
                    pkt = create_u32(SYS_RESET, sub, 0)
                    bridge.send_packet(pkt, "System Reset")
                    self._send_json(200, {"message": "Sendte System Reset (0xFF)."})
                    return

                if action == "short_text":
                    txt = str(data.get("text", ""))
                    if not txt or len(txt) > 4:
                        raise ValueError("Kort melding må være 1–4 ASCII-tegn.")
                    raw_ascii = txt.encode("ascii")
                    val_u32 = int.from_bytes(raw_ascii.ljust(4, b"\x00"), "big")
                    sub_str = str(data.get("sub_id", "01")).replace("0x", "").strip() or "01"
                    sub = int(sub_str, 16)
                    pkt = create_u32(make_type_id(FMT_RAW32, CAT_SYSTEM), sub, val_u32)
                    bridge.send_packet(pkt, f'Kort melding "{txt}"')
                    self._send_json(200, {"message": f'Sendte kort melding "{txt}".'})
                    return

                if action == "transfer_text":
                    txt = str(data.get("text", SAMPLE_CONFIG_JSON))
                    blob = txt.encode("utf-8")
                    mode_label = "CONFIG" if data.get("mode") == "config" else "DATA"
                    bid, n_chunks = bridge.send_transfer_blob(blob, mode_label)
                    self._send_json(
                        200,
                        {"message": f"[{bid}] Sendte {len(blob)} byte i {n_chunks} chunks."},
                    )
                    return

                if action == "transfer_base64":
                    fname = str(data.get("filename", "fil.bin"))
                    b64 = str(data.get("data_b64", ""))
                    blob = base64.b64decode(b64)
                    bid, n_chunks = bridge.send_transfer_blob(blob, f"FILE({fname})")
                    self._send_json(
                        200,
                        {"message": f"[{bid}] Sendte '{fname}' ({len(blob)} byte) i {n_chunks} chunks."},
                    )
                    return

                if action == "ota_fw":
                    corrupt = bool(data.get("corrupt_crc", False))
                    fw_size = int(data.get("size", 262144))
                    ver = bridge.next_fw_version()
                    img = build_realistic_firmware_image(version=ver, total_size=fw_size)
                    bridge.run_transfer_async(img, f"OTA(v{ver})", corrupt_crc=corrupt, step_delay=0.0)
                    suffix = " (med bevisst feil CRC32)" if corrupt else ""
                    self._send_json(
                        200,
                        {"message": f"Starter OTA v{ver} ({len(img)} byte){suffix}..."},
                    )
                    return

                if action == "simulate_ota":
                    fw_size = int(data.get("size", 262144))
                    ver = bridge.next_fw_version()
                    img = build_realistic_firmware_image(version=ver, total_size=fw_size)
                    bridge.run_transfer_async(img, f"SIM-OTA(v{ver})", corrupt_crc=False, step_delay=0.0005)
                    self._send_json(
                        200,
                        {"message": f"Simulerer firmware-oppdatering v{ver} ({len(img)} byte)..."},
                    )
                    return

                if action == "fixed32":
                    cat_key = str(data.get("category", "temperature"))
                    if cat_key not in CATEGORIES:
                        raise ValueError(f"Ukjent kategori: {cat_key}")
                    _, cat_code = CATEGORIES[cat_key]
                    val = float(data.get("value", 0.0))
                    sub_str = str(data.get("sub_id", "01")).replace("0x", "").strip() or "01"
                    sub = int(sub_str, 16)
                    pkt = create_fixed32(cat_code, sub, val)
                    bridge.send_packet(pkt, f"Fixed32 {val:.2f}")
                    self._send_json(200, {"message": f"Sendte Fixed32-verdi {val:.2f}."})
                    return

                if action == "bad_crc":
                    good = bytearray(create_fixed32(CAT_TEMPERATURE, 0x01, 21.50).to_bytes())
                    good[-1] ^= 0x5A
                    bridge.send_raw(bytes(good), "Pakke med bevisst feil CRC-8")
                    self._send_json(200, {"message": "Sendte pakke med ugyldig CRC-8."})
                    return

                if action == "noise":
                    noise = bytes([0x00, 0x7F, 0x11, 0x88, 0x3A])
                    bridge.send_raw(noise, "Linjestøy uten 0xA5")
                    self._send_json(200, {"message": "Sendte 5 byte linjestøy."})
                    return

                if action == "hex":
                    hex_clean = str(data.get("hex", "")).replace("0x", "").replace(" ", "").strip()
                    raw = bytes.fromhex(hex_clean)
                    if len(raw) != PACKET_SIZE:
                        raise ValueError(f"Må være nøyaktig {PACKET_SIZE} byte (16 hex-tegn).")
                    bridge.send_raw(raw, "Rå 8-byte hex")
                    self._send_json(200, {"message": f"Sendte {raw.hex().upper()}."})
                    return

                raise ValueError(f"Ukjent handling: {action}")
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

    return LabHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Lokalt web-GUI for PlatLink v1.0 TCP-buss")
    parser.add_argument("--bus-host", default="127.0.0.1")
    parser.add_argument("--bus-port", type=int, default=4571)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="Ikke åpne nettleser automatisk")
    args = parser.parse_args()

    bridge = PlatLinkLabBridge(args.bus_host, args.bus_port)
    bridge.connect_bus()

    server = ThreadingHTTPServer((args.web_host, args.web_port), make_handler(bridge))
    url = f"http://{args.web_host}:{args.web_port}/"
    print(f"[WEB] PlatLink Web-Lab kjører på {url} (koblet til buss {args.bus_host}:{args.bus_port})")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Stopper web-server...")
    finally:
        server.server_close()
        bridge.close()


if __name__ == "__main__":
    main()
"""Interaktiv CLI-lab for PlatLink v1.0 over TCP.

Lar deg:
- Lytte og dekode innkommende 8-byte PlatLink-pakker fortløpende (med støy- og CRC-logging)
- Skru relékanaler av/på (Digital 0x1F) og sende System Reset (0xFF)
- Sende en komplett OTA-sekvens (Init 0xFC/0x01 -> Data 0xFD -> Verify 0xFC/0x03)
- Sende egendefinert 8-byte hex eller injisere støy / korrupt CRC
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SRC = REPO_ROOT / "python"
if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))

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

CATEGORY_NAMES = {
    CAT_TEMPERATURE: "Temp (°C)",
    CAT_HUMIDITY: "Fukt (%)",
    CAT_LIGHT: "Lys",
    CAT_PH: "pH",
    CAT_EC: "EC (µS/cm)",
    CAT_WATER_LEVEL: "Vannstand (cm)",
    CAT_PRESSURE: "Trykk",
    CAT_SYSTEM: "System",
}

FORMAT_NAMES = {
    FMT_DIGITAL: "Digital",
    FMT_ANALOG8: "Analog8",
    FMT_FIXED32: "Fixed32",
    FMT_RAW32: "Raw32",
    FMT_INT32: "Int32",
    FMT_SYSTEM: "System",
}


def format_packet_summary(raw: bytes, pkt: PlatLinkPacket) -> str:
    hex_str = raw.hex().upper()
    fmt_name = FORMAT_NAMES.get(pkt.format_code, f"0x{pkt.format_code:X}")
    cat_name = CATEGORY_NAMES.get(pkt.category_code, f"0x{pkt.category_code:X}")

    if pkt.type_id == SYS_HEARTBEAT:
        val_str = f"heartbeat={pkt.as_u32()}s"
    elif pkt.type_id == SYS_DISCOVERY:
        val_str = f"discovery(u32={pkt.as_u32()})"
    elif pkt.type_id == SYS_RESET:
        val_str = f"reset(u32={pkt.as_u32()})"
    elif pkt.type_id == SYS_OTA_CTRL:
        cmd = "INIT" if pkt.sub_id == 0x01 else "VERIFY" if pkt.sub_id == 0x03 else f"0x{pkt.sub_id:02X}"
        val_str = f"ota_ctrl({cmd}, val={pkt.as_u32()})"
    elif pkt.type_id == SYS_OTA_DATA:
        val_str = f"ota_data(seq={pkt.sub_id}, payload={pkt.payload.hex().upper()})"
    elif pkt.format_code == FMT_FIXED32:
        val_str = f"{pkt.as_fixed32_float():.2f}"
    elif pkt.format_code == FMT_DIGITAL:
        val_str = "ON (1)" if pkt.as_digital() else "OFF (0)"
    elif pkt.format_code == FMT_ANALOG8:
        val_str = str(pkt.as_analog8())
    elif pkt.format_code == FMT_INT32:
        val_str = str(pkt.as_i32())
    else:
        val_str = str(pkt.as_u32())

    return (
        f"[{hex_str}] type=0x{pkt.type_id:02X} ({fmt_name} / {cat_name}) "
        f"sub=0x{pkt.sub_id:02X} -> {val_str}"
    )


class ListenerThread(threading.Thread):
    def __init__(self, sock: socket.socket, stop_event: threading.Event, io_lock: threading.Lock) -> None:
        super().__init__(daemon=True)
        self.sock = sock
        self.stop_event = stop_event
        self.io_lock = io_lock
        self._buf = bytearray()
        self.quiet = False

    def _log(self, msg: str) -> None:
        if self.quiet:
            return
        with self.io_lock:
            print(f"\r{msg}\n> ", end="", flush=True)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                data = self.sock.recv(1024)
            except TimeoutError:
                continue
            except OSError:
                break

            if not data:
                self._log("[RX] Tilkobling lukket av buss.")
                break

            self._buf.extend(data)
            while True:
                idx = self._buf.find(bytes([HEADER]))
                if idx == -1:
                    if len(self._buf) > 0:
                        self._log(f"[RX:STØY] Forkastet {len(self._buf)} byte uten 0xA5: {bytes(self._buf).hex().upper()}")
                        self._buf.clear()
                    break

                if idx > 0:
                    skipped = bytes(self._buf[:idx])
                    del self._buf[:idx]
                    self._log(f"[RX:STØY] Hoppet over {len(skipped)} byte før 0xA5: {skipped.hex().upper()}")

                if len(self._buf) < PACKET_SIZE:
                    break

                candidate = bytes(self._buf[:PACKET_SIZE])
                try:
                    pkt = from_bytes(candidate)
                except PlatLinkError as exc:
                    del self._buf[0]
                    self._log(f"[RX:FEIL] {exc} i kandidat {candidate.hex().upper()} (søker videre)")
                    continue

                del self._buf[:PACKET_SIZE]
                self._log(f"[RX:OK]   {format_packet_summary(candidate, pkt)}")


def print_help() -> None:
    print(
        """
Kommandoer i PlatLink Lab CLI:
  relay <ch> <0|1|on|off>   Send Digital-kommando (0x1F) til kanal <ch> (f.eks. 'relay 1 on')
  reset [sub_id_hex]       Send System Reset (0xFF), valgfritt med sub_id (standard 0x00)
  temp <verdi> [sub_hex]   Send test-temperatur i °C som Fixed32 (f.eks. 'temp 23.45 01')
  ota [størrelse_byte]     Kjør en simulert OTA-overføring (Init + chunks + Verify)
  hex <16 hex-tegn>        Send nøyaktig 8-byte hex-ramme (f.eks. 'hex A53101000008CDAA')
  badcrc                   Send en gyldig pakke med ødelagt CRC-8-byte
  noise                    Send tilfeldige ikke-0xA5 støybyte
  quiet                    Slå av/på løpende RX-utskrift under skriving
  help                     Vis denne hjelpen
  quit / exit              Avslutt CLI
"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Interaktiv PlatLink v1.0 CLI over TCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4571)
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5.0)
    sock.settimeout(0.25)

    stop_event = threading.Event()
    io_lock = threading.Lock()
    listener = ListenerThread(sock, stop_event, io_lock)
    listener.start()

    print(f"Koblet til PlatLink TCP-buss på {args.host}:{args.port}. Skriv 'help' for kommandoer.")

    try:
        while not stop_event.is_set():
            with io_lock:
                print("> ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                break
            parts = line.strip().split()
            if not parts:
                continue

            cmd = parts[0].lower()

            if cmd in ("quit", "exit"):
                break
            elif cmd == "help":
                print_help()
            elif cmd == "quiet":
                listener.quiet = not listener.quiet
                print(f"RX-utskrift er nå {'AV' if listener.quiet else 'PÅ'}.")
            elif cmd == "relay" and len(parts) >= 3:
                ch = int(parts[1], 0)
                state_str = parts[2].lower()
                state = state_str in ("1", "on", "true", "på")
                pkt = create_digital(CAT_SYSTEM, ch, state)
                sock.sendall(pkt.to_bytes())
                print(f"[TX] Sendte {format_packet_summary(pkt.to_bytes(), pkt)}")
            elif cmd == "reset":
                sub = int(parts[1], 16) if len(parts) >= 2 else 0x00
                pkt = create_u32(SYS_RESET, sub, 0)
                sock.sendall(pkt.to_bytes())
                print(f"[TX] Sendte {format_packet_summary(pkt.to_bytes(), pkt)}")
            elif cmd == "temp" and len(parts) >= 2:
                val = float(parts[1])
                sub = int(parts[2], 16) if len(parts) >= 3 else 0x01
                pkt = create_fixed32(CAT_TEMPERATURE, sub, val)
                sock.sendall(pkt.to_bytes())
                print(f"[TX] Sendte {format_packet_summary(pkt.to_bytes(), pkt)}")
            elif cmd == "ota":
                size = int(parts[1], 0) if len(parts) >= 2 else 22
                dummy_fw = bytes((i % 251) for i in range(size))
                transfer = OtaTransfer(dummy_fw)
                init_p = transfer.init_packet()
                sock.sendall(init_p.to_bytes())
                print(f"[TX] OTA INIT: {size} byte")
                time.sleep(0.05)

                chunks = transfer.iter_chunks()
                for ch_item in chunks:
                    sock.sendall(ch_item.packet.to_bytes())
                    time.sleep(0.02)
                print(f"[TX] OTA DATA: sendte {len(chunks)} chunks")

                ver_p = transfer.verify_packet()
                sock.sendall(ver_p.to_bytes())
                print(f"[TX] OTA VERIFY: crc32=0x{transfer.crc32:08X}")
            elif cmd == "hex" and len(parts) >= 2:
                raw_hex = "".join(parts[1:]).replace("0x", "").replace(" ", "")
                raw = bytes.fromhex(raw_hex)
                if len(raw) != PACKET_SIZE:
                    print(f"Feil: krever nøyaktig {PACKET_SIZE} byte (16 hex-tegn), fikk {len(raw)} byte.")
                    continue
                sock.sendall(raw)
                print(f"[TX:RAW] Sendte {raw.hex().upper()}")
            elif cmd == "badcrc":
                good = bytearray(create_fixed32(CAT_TEMPERATURE, 0x01, 20.00).to_bytes())
                good[-1] ^= 0x5A
                sock.sendall(bytes(good))
                print(f"[TX:BADCRC] Sendte {bytes(good).hex().upper()}")
            elif cmd == "noise":
                noise = bytes([0x00, 0x7F, 0x11, 0x88, 0x3A])
                sock.sendall(noise)
                print(f"[TX:NOISE] Sendte {noise.hex().upper()}")
            else:
                print("Ukjent kommando eller manglende argumenter. Skriv 'help'.")
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        try:
            sock.close()
        except OSError:
            pass
        print("\nAvsluttet CLI.")


if __name__ == "__main__":
    main()
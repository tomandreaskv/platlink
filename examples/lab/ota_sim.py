"""Firmware-bildebygging og sammensatt OTA/config-reassembly for PlatLink-laben."""

from __future__ import annotations

import struct
import time
import zlib

from platlink import CAT_SYSTEM, FMT_RAW32, SYS_OTA_CTRL, SYS_OTA_DATA, PlatLinkPacket

OTA_STATUS_READY = 0x20
OTA_STATUS_OK = 0x21
OTA_STATUS_ERR_SIZE = 0xE1
OTA_STATUS_ERR_CRC = 0xE2


def build_realistic_firmware_image(
    version: str = "1.1.0",
    total_size: int = 262144,
) -> bytes:
    """Bygger et realistisk firmware-bilde med PLFW-header + versjon + strukturert payload."""
    header_size = 24
    body_len = max(64, total_size - header_size)
    ver_bytes = version.encode("ascii")[:15].ljust(16, b"\x00")
    header = struct.pack(">4s16sI", b"PLFW", ver_bytes, body_len)

    block_pattern = bytes(((i * 31 + 17) ^ (i >> 3)) & 0xFF for i in range(4096))
    repeats, rem = divmod(body_len, len(block_pattern))
    body = (block_pattern * repeats) + block_pattern[:rem]
    return header + body


class LoggerReassembler:
    """Reassemblerer OTA/config-chunks direkte fra pakkene på bussen med strømmende CRC32
    og tolker header/payload slik at dataloggerens oppdateringsløp blir tydelig."""

    def __init__(self) -> None:
        self.running_partition: str = "ota_0"
        self.target_partition: str | None = None
        self.firmware_version: str = "1.0.0"
        self.last_logger_report: str = "Ingen rapport ennå"
        self._head_preview: bytearray = bytearray()
        self._is_fw_image: bool = False
        self._parsed_fw_ver: str | None = None
        self._parsed_body_len: int | None = None
        self._start_mono: float | None = None
        self.reset_session()

    def reset_session(self) -> None:
        self.phase: str = "IDLE"
        self.status: str = "Klar (venter på OTA/config)"
        self.expected_size: int | None = None
        self.received_len: int = 0
        self.chunk_count: int = 0
        self._running_crc32: int = 0
        self.expected_crc32: int | None = None
        self.actual_crc32: int | None = None
        self.crc_ok: bool | None = None
        self.updated_at: str | None = None
        self.seq_num: int = 0
        self._head_preview = bytearray()
        self._is_fw_image = False
        self._parsed_fw_ver = None
        self._parsed_body_len = None
        self._start_mono = None

    def _try_parse_header(self) -> None:
        if len(self._head_preview) >= 24 and bytes(self._head_preview[:4]) == b"PLFW":
            self._is_fw_image = True
            _magic, ver_raw, body_len = struct.unpack(">4s16sI", bytes(self._head_preview[:24]))
            ver_str = ver_raw.rstrip(b"\x00").decode("ascii", errors="replace").strip()
            self._parsed_fw_ver = ver_str or None
            self._parsed_body_len = int(body_len)

    def observe_packet(self, pkt: PlatLinkPacket) -> None:
        now = time.strftime("%H:%M:%S")
        if pkt.format_code == FMT_RAW32 and pkt.category_code == CAT_SYSTEM:
            if pkt.sub_id == OTA_STATUS_READY:
                self.last_logger_report = f"[{now}] Klar for {pkt.as_u32()} B"
            elif pkt.sub_id == OTA_STATUS_OK:
                self.last_logger_report = f"[{now}] OK ({self.running_partition}, v{self.firmware_version})"
            elif pkt.sub_id == OTA_STATUS_ERR_SIZE:
                self.last_logger_report = f"[{now}] FEIL: Størrelse ({pkt.as_u32()} B)"
            elif pkt.sub_id == OTA_STATUS_ERR_CRC:
                self.last_logger_report = f"[{now}] FEIL: CRC32 (0x{pkt.as_u32():08X})"

        if pkt.type_id == SYS_OTA_CTRL:
            if pkt.sub_id == 0x01:  # INIT
                self.reset_session()
                self.seq_num += 1
                self.expected_size = pkt.as_u32()
                self.target_partition = "ota_1" if self.running_partition == "ota_0" else "ota_0"
                self.phase = "RECEIVING"
                self._start_mono = time.monotonic()
                self.status = f"1/3 Mottar & skriver til {self.target_partition} (0/{self.expected_size} B)"
                self.updated_at = now

            elif pkt.sub_id == 0x03:  # VERIFY
                self.expected_crc32 = pkt.as_u32()
                self.actual_crc32 = self._running_crc32 & 0xFFFFFFFF
                size_ok = self.expected_size is None or self.received_len == self.expected_size
                crc_ok = size_ok and (self.actual_crc32 == self.expected_crc32)
                self.crc_ok = crc_ok
                self._try_parse_header()

                if crc_ok:
                    if self._is_fw_image and self.target_partition:
                        self.phase = "REBOOTING"
                        self.status = f"2/3 CRC32 OK -> Rebooter til {self.target_partition}..."
                        self.updated_at = now
                        time.sleep(0.12)

                        self.running_partition = self.target_partition
                        if self._parsed_fw_ver:
                            self.firmware_version = self._parsed_fw_ver
                        self.phase = "VERIFIED"
                        self.status = (
                            f"3/3 Oppdatert og aktiv på {self.running_partition} "
                            f"(fw v{self.firmware_version}, CRC32 OK)"
                        )
                    else:
                        self.phase = "VERIFIED"
                        self.status = f"3/3 Verifisert OK ({self.received_len} B, CRC32 stemmer)"
                else:
                    self.phase = "FAILED"
                    self.status = "FEIL: CRC32 eller størrelse stemmer ikke (beholder gjeldende partisjon)"
                self.updated_at = now

        elif pkt.type_id == SYS_OTA_DATA:
            if self.phase != "RECEIVING":
                self.reset_session()
                self.seq_num += 1
                self.expected_size = 0
                self.phase = "RECEIVING"
                self._start_mono = time.monotonic()

            if self.expected_size and self.expected_size > 0:
                remaining = max(0, self.expected_size - self.received_len)
                take = min(len(pkt.payload), remaining)
            else:
                take = len(pkt.payload)

            chunk = pkt.payload[:take]
            self._running_crc32 = zlib.crc32(chunk, self._running_crc32) & 0xFFFFFFFF
            self.received_len += take
            self.chunk_count += 1

            if len(self._head_preview) < 256:
                need = 256 - len(self._head_preview)
                self._head_preview.extend(chunk[:need])
                self._try_parse_header()

            part = self.target_partition or "ota_?"
            if self.expected_size and self.expected_size > 0:
                pct = min(100.0, (self.received_len / self.expected_size) * 100.0)
                self.status = (
                    f"1/3 Skriver {part} ({pct:.1f}% • {self.received_len}/{self.expected_size} B)"
                )
            else:
                self.status = f"1/3 Skriver {part} ({self.received_len} B)"
            self.updated_at = now

    def _build_decoded_summary(self) -> str:
        raw = bytes(self._head_preview)
        if not raw:
            return "(ingen data mottatt ennå)"

        elapsed = max(0.001, (time.monotonic() - self._start_mono)) if self._start_mono else 0.0
        rate_kbps = (self.received_len / 1024.0) / elapsed if elapsed > 0 else 0.0
        pct_str = ""
        if self.expected_size and self.expected_size > 0:
            pct = min(100.0, (self.received_len / self.expected_size) * 100.0)
            pct_str = f" ({pct:.1f}%)"

        if self._is_fw_image:
            ver_txt = self._parsed_fw_ver or "ukjent"
            body_txt = f"{self._parsed_body_len} B" if self._parsed_body_len is not None else "ukjent"
            target_txt = self.target_partition or "ota_?"
            lines = [
                "Format: PlatLink Firmware Image (magic 'PLFW')",
                f"Ny firmware-versjon: v{ver_txt}",
                f"Header nyttelast-lengde: {body_txt}",
                f"Målpartisjon: {target_txt} (aktiv nå: {self.running_partition})",
                f"Mottatt: {self.received_len} B{pct_str} i {self.chunk_count} chunks • Hastighet: {rate_kbps:.1f} KB/s",
            ]
            if self.phase == "VERIFIED":
                lines.append(
                    f"Oppdateringsløp: Skrevet -> CRC32 OK -> Skiftet til {self.running_partition} -> Kjører v{self.firmware_version}"
                )
            elif self.phase == "FAILED":
                lines.append("Oppdateringsløp: Avbrutt ved verifisering (ingen boot-bytte)")
            else:
                lines.append("Oppdateringsløp: Mottar chunks og oppdaterer løpende CRC32...")
            return "\n".join(lines)

        printable = "".join(chr(b) if (32 <= b <= 126 or b in (10, 13, 9)) else "." for b in raw)
        return (
            f"Mottatt: {self.received_len} B{pct_str} i {self.chunk_count} chunks • Hastighet: {rate_kbps:.1f} KB/s\n"
            f"Tekst/JSON-tolkning: {printable}"
        )

    def snapshot(self) -> dict:
        raw = bytes(self._head_preview)
        hex_grouped = " ".join(
            raw.hex().upper()[i : i + 2] for i in range(0, len(raw.hex()), 2)
        )
        prefix_note = (
            f" [Første {len(raw)} av {self.received_len} byte]"
            if self.received_len > len(raw)
            else ""
        )
        return {
            "seq": self.seq_num,
            "phase": self.phase,
            "status": self.status,
            "running_partition": self.running_partition,
            "firmware_version": self.firmware_version,
            "last_logger_report": self.last_logger_report,
            "expected_size": self.expected_size,
            "received_len": self.received_len,
            "chunk_count": self.chunk_count,
            "expected_crc32": f"0x{self.expected_crc32:08X}" if self.expected_crc32 is not None else None,
            "actual_crc32": f"0x{self.actual_crc32:08X}" if self.actual_crc32 is not None else None,
            "crc_ok": self.crc_ok,
            "updated_at": self.updated_at,
            "decoded_preview": self._build_decoded_summary(),
            "hex_preview": (hex_grouped + prefix_note) if hex_grouped else "(tom)",
        }
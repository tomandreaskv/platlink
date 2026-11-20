"""Hjelpere for å bygge og dele opp PlatLink v1.0 OTA-pakker (0xFC og 0xFD).

Merk: SubID i SYS_OTA_DATA er en 8-bit byte (0..255) og ruller rundt etter
256 pakker (1024 byte ved 4 byte per pakke). Høyere lag bør håndtere
blokksporing og kvittering ved større firmwarebilder eller støyutsatte linjer.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from .codec import (
    OTA_CMD_INIT,
    OTA_CMD_VERIFY,
    PAYLOAD_SIZE,
    SYS_OTA_CTRL,
    SYS_OTA_DATA,
    PlatLinkPacket,
    create_u32,
)


@dataclass(frozen=True)
class OtaChunk:
    """Representerer én 4-byte firmware-chunk knyttet til en PlatLink-pakke."""

    sequence: int
    sub_id: int
    offset: int
    packet: PlatLinkPacket


def build_ota_init(firmware_size: int) -> PlatLinkPacket:
    """Bygger en OTA Control Init-pakke (0xFC, SubID 0x01) med firmwarestørrelse i byte."""
    if firmware_size < 0 or firmware_size > 0xFFFFFFFF:
        raise ValueError(f"Ugyldig firmwarestørrelse: {firmware_size}")
    return create_u32(SYS_OTA_CTRL, OTA_CMD_INIT, firmware_size)


def build_ota_verify(crc32_value: int) -> PlatLinkPacket:
    """Bygger en OTA Control Verify-pakke (0xFC, SubID 0x03) med forventet CRC-32."""
    return create_u32(SYS_OTA_CTRL, OTA_CMD_VERIFY, crc32_value & 0xFFFFFFFF)


class OtaTransfer:
    """Deler opp et firmwarebilde i 4-byte PlatLink OTA Data-pakker (0xFD)."""

    def __init__(self, firmware: bytes | bytearray | memoryview) -> None:
        self.firmware: bytes = bytes(firmware)
        self.crc32: int = zlib.crc32(self.firmware) & 0xFFFFFFFF

    @property
    def size(self) -> int:
        """Total firmwarestørrelse i byte."""
        return len(self.firmware)

    def init_packet(self) -> PlatLinkPacket:
        """Returnerer startpakken (OTA Control Init) for denne overføringen."""
        return build_ota_init(self.size)

    def verify_packet(self) -> PlatLinkPacket:
        """Returnerer verifiseringspakken (OTA Control Verify) med CRC-32."""
        return build_ota_verify(self.crc32)

    def iter_chunks(self) -> list[OtaChunk]:
        """Bygger alle 4-byte OTA Data-pakker (siste chunk null-utfylles til 4 byte)."""
        chunks: list[OtaChunk] = []
        seq = 0
        for offset in range(0, len(self.firmware), PAYLOAD_SIZE):
            raw_slice = self.firmware[offset : offset + PAYLOAD_SIZE]
            padded = raw_slice.ljust(PAYLOAD_SIZE, b"\x00")
            sub_id = seq & 0xFF
            pkt = create_u32(
                SYS_OTA_DATA,
                sub_id,
                int.from_bytes(padded, byteorder="big", signed=False),
            )
            chunks.append(OtaChunk(sequence=seq, sub_id=sub_id, offset=offset, packet=pkt))
            seq += 1
        return chunks
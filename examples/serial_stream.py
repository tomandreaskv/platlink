"""Eksempel på strømleser (framer) for PlatLink v1.0 over seriell/byte-strøm.

Leser vilkårlige byte-biter, søker etter header-byten 0xA5, samler 8-byte-rammer
og validerer CRC-8. Dersom CRC-8 feiler, skyves strømmen videre til neste 0xA5
slik at rammesynkronisering gjenopprettes automatisk ved støy eller tapte byte.
"""

from __future__ import annotations

from collections.abc import Iterable

from platlink import HEADER, PACKET_SIZE, PlatLinkError, PlatLinkPacket, from_bytes


class PlatLinkStreamReader:
    """Tilstandsmaskin for å hente ut gyldige 8-byte PlatLink-pakker fra en strøm."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.discarded_bytes: int = 0
        self.crc_errors: int = 0

    def feed(self, data: bytes | bytearray | memoryview) -> list[PlatLinkPacket]:
        """Mater inn nye byte og returnerer alle fullstendige, gyldige pakker funnet."""
        packets: list[PlatLinkPacket] = []
        self._buf.extend(data)

        while True:
            # Finn første mulige startbyte (0xA5)
            header_idx = self._buf.find(bytes([HEADER]))
            if header_idx == -1:
                self.discarded_bytes += len(self._buf)
                self._buf.clear()
                break

            if header_idx > 0:
                self.discarded_bytes += header_idx
                del self._buf[:header_idx]

            # Vent til vi har minst 8 byte tilgjengelig
            if len(self._buf) < PACKET_SIZE:
                break

            candidate = bytes(self._buf[:PACKET_SIZE])
            try:
                pkt = from_bytes(candidate)
            except PlatLinkError:
                # Ugyldig CRC-8: kast selve header-byten og let videre etter neste 0xA5
                self.crc_errors += 1
                del self._buf[0]
                self.discarded_bytes += 1
                continue

            packets.append(pkt)
            del self._buf[:PACKET_SIZE]

        return packets

    def feed_chunks(self, chunks: Iterable[bytes]) -> list[PlatLinkPacket]:
        """Hjelpemetode for å mate inn flere byte-blokker etter hverandre."""
        found: list[PlatLinkPacket] = []
        for chunk in chunks:
            found.extend(self.feed(chunk))
        return found


if __name__ == "__main__":
    # Simulerer støy før pakke 1, en korrupt pakke midt i, og en gyldig pakke til slutt
    noise = b"\x00\xFF\x12"
    valid_temp = bytes.fromhex("A53101000008CDAA")
    corrupt_pkt = bytes.fromhex("A53101000008CD00")
    valid_hb = bytes.fromhex("A5F00100000E107A")

    stream = noise + valid_temp + corrupt_pkt + valid_hb
    reader = PlatLinkStreamReader()

    # Mates inn i ujevne biter for å simulere seriell/UART-buffer
    chunks = [stream[i : i + 3] for i in range(0, len(stream), 3)]
    decoded = reader.feed_chunks(chunks)

    for pkt in decoded:
        print(
            f"Pakke: type_id=0x{pkt.type_id:02X} "
            f"(fmt=0x{pkt.format_code:X}, cat=0x{pkt.category_code:X}) "
            f"sub_id=0x{pkt.sub_id:02X} u32={pkt.as_u32()}"
        )
    print(f"Forkastede byte: {reader.discarded_bytes}, CRC-feil: {reader.crc_errors}")
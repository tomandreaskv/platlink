"""Koding, dekoding, CRC-8 og pakkehåndtering for PlatLink v1.1.

Rammeformat (8 byte):
- Byte 0:     Header (0xA5)
- Byte 1:     TypeID ((format << 4) | category)
- Byte 2:     SubID  (kanal/endpoint eller system-underkommando/sekvens)
- Bytes 3..6: Payload (4 byte big-endian)
- Byte 7:     CRC8   (CRC-8 over byte 0..6 med polynom 0x07 og init 0x00)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER: int = 0xA5
PACKET_SIZE: int = 8
PAYLOAD_SIZE: int = 4
CRC_POLYNOMIAL: int = 0x07
CRC_INIT: int = 0x00
FIXED32_SCALE: int = 100

# Formater (høy nibble i TypeID: bits 7..4)
FMT_DIGITAL: int = 0x1
FMT_ANALOG8: int = 0x2
FMT_FIXED32: int = 0x3
FMT_RAW32: int = 0x4
FMT_INT32: int = 0x5
FMT_SYSTEM: int = 0xF

# Kategorier (lav nibble i TypeID: bits 3..0)
CAT_TEMPERATURE: int = 0x1
CAT_HUMIDITY: int = 0x2
CAT_LIGHT: int = 0x3
CAT_PH: int = 0x4
CAT_EC: int = 0x5
CAT_WATER_LEVEL: int = 0x6
CAT_PRESSURE: int = 0x7
CAT_ACTUATOR: int = 0x8
CAT_POSITION: int = 0x9
CAT_CUSTOM: int = 0xE
CAT_SYSTEM: int = 0xF

# Systemmeldinger (TypeID 0xF0..0xFF)
SYS_HEARTBEAT: int = 0xF0
SYS_TIMEBASE: int = 0xF1
SYS_TIME_OFFSET: int = 0xF2
SYS_DISCOVERY: int = 0xF4
SYS_OTA_CTRL: int = 0xFC
SYS_OTA_DATA: int = 0xFD
SYS_OTA_STATUS: int = 0xFE
SYS_RESET: int = 0xFF

# OTA-kontroll underkommandoer (SubID for SYS_OTA_CTRL)
OTA_CMD_INIT: int = 0x01
OTA_CMD_EXPECTED_CRC: int = 0x02
OTA_CMD_VERIFY: int = 0x03

_U32_STRUCT = struct.Struct(">I")
_I32_STRUCT = struct.Struct(">i")


class PlatLinkError(ValueError):
    """Feil som kastes ved ugyldig pakkelengde, header, padding eller CRC-8."""


def make_type_id(fmt: int, category: int) -> int:
    """Setter sammen format (høy nibble) og kategori (lav nibble) til én TypeID-byte."""
    return (((fmt & 0x0F) << 4) | (category & 0x0F)) & 0xFF


def split_type_id(type_id: int) -> tuple[int, int]:
    """Deler TypeID opp i (format, category) fra henholdsvis høy og lav nibble."""
    t = type_id & 0xFF
    return (t >> 4) & 0x0F, t & 0x0F


def crc8(data: bytes | bytearray | memoryview) -> int:
    """Beregner CRC-8 over data med polynom 0x07, init 0x00 og xor_out 0x00."""
    crc = CRC_INIT
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ CRC_POLYNOMIAL) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def encode_u32(value: int) -> bytes:
    """Koder et usignert 32-bit heltall (0..4294967295) til 4-byte big-endian."""
    return _U32_STRUCT.pack(value & 0xFFFFFFFF)


def decode_u32(payload: bytes | bytearray | memoryview) -> int:
    """Dekoder 4-byte big-endian payload til et usignert 32-bit heltall."""
    if len(payload) != PAYLOAD_SIZE:
        raise PlatLinkError(f"Payload må være {PAYLOAD_SIZE} byte, fikk {len(payload)}")
    return _U32_STRUCT.unpack(payload)[0]


def encode_i32(value: int) -> bytes:
    """Koder et signert 32-bit heltall (-2147483648..2147483647) til 4-byte big-endian."""
    return _I32_STRUCT.pack(int(value))


def decode_i32(payload: bytes | bytearray | memoryview) -> int:
    """Dekoder 4-byte big-endian payload til et signert 32-bit heltall."""
    if len(payload) != PAYLOAD_SIZE:
        raise PlatLinkError(f"Payload må være {PAYLOAD_SIZE} byte, fikk {len(payload)}")
    return _I32_STRUCT.unpack(payload)[0]


def _validate_padding(data: bytes) -> None:
    """Krever at payload[0..2] er 0x00 for 1-byte formater (Digital og Analog8)."""
    fmt, _ = split_type_id(data[1])
    if fmt in (FMT_DIGITAL, FMT_ANALOG8):
        if data[3] != 0 or data[4] != 0 or data[5] != 0:
            raise PlatLinkError(
                f"Ugyldig padding for format 0x{fmt:X}: payload[0..2] må være 0x00"
            )


@dataclass(frozen=True)
class PlatLinkPacket:
    """Uforanderlig representasjon av en 8-byte PlatLink-pakke."""

    header: int
    type_id: int
    sub_id: int
    payload: bytes
    crc8: int

    def __post_init__(self) -> None:
        p = bytes(self.payload)
        if len(p) != PAYLOAD_SIZE:
            raise PlatLinkError(f"Payload må være {PAYLOAD_SIZE} byte, fikk {len(p)}")
        object.__setattr__(self, "payload", p)

    @property
    def format_code(self) -> int:
        """Returnerer formatkoden (høy nibble i TypeID)."""
        fmt, _ = split_type_id(self.type_id)
        return fmt

    @property
    def category_code(self) -> int:
        """Returnerer kategorikoden (lav nibble i TypeID)."""
        _, cat = split_type_id(self.type_id)
        return cat

    def to_bytes(self) -> bytes:
        """Serialiserer pakken til nøyaktig 8 byte i trådrekkefølge."""
        return (
            bytes([self.header & 0xFF, self.type_id & 0xFF, self.sub_id & 0xFF])
            + self.payload
            + bytes([self.crc8 & 0xFF])
        )

    def as_u32(self) -> int:
        """Dekoder 4-byte payload som usignert 32-bit heltall."""
        return decode_u32(self.payload)

    def as_i32(self) -> int:
        """Dekoder 4-byte payload som signert 32-bit heltall."""
        return decode_i32(self.payload)

    def as_fixed32_scaled(self) -> int:
        """Dekoder Fixed32-verdi som heltall skalert med 100 (f.eks. 2253 for 22.53)."""
        return self.as_i32()

    def as_fixed32_float(self) -> float:
        """Dekoder Fixed32-verdi som float (int32 / 100)."""
        return self.as_i32() / FIXED32_SCALE

    def as_digital(self) -> bool:
        """Dekoder boolsk verdi fra payload[3] (0 -> False, ellers True)."""
        return self.payload[3] != 0

    def as_analog8(self) -> int:
        """Dekoder usignert 8-bit verdi fra payload[3]."""
        return self.payload[3]


def pack(type_id: int, sub_id: int, payload: bytes | bytearray | memoryview) -> PlatLinkPacket:
    """Bygger en PlatLinkPacket fra type_id, sub_id og 4-byte payload med beregnet CRC-8."""
    p = bytes(payload)
    if len(p) != PAYLOAD_SIZE:
        raise PlatLinkError(f"Payload må være {PAYLOAD_SIZE} byte, fikk {len(p)}")

    t = type_id & 0xFF
    s = sub_id & 0xFF
    frame_without_crc = bytes([HEADER, t, s]) + p
    checksum = crc8(frame_without_crc)
    return PlatLinkPacket(header=HEADER, type_id=t, sub_id=s, payload=p, crc8=checksum)


def create_u32(type_id: int, sub_id: int, value: int) -> PlatLinkPacket:
    """Oppretter en pakke med usignert 32-bit verdi i big-endian."""
    return pack(type_id, sub_id, encode_u32(value))


def create_i32(type_id: int, sub_id: int, value: int) -> PlatLinkPacket:
    """Oppretter en pakke med signert 32-bit verdi i big-endian."""
    return pack(type_id, sub_id, encode_i32(value))


def create_fixed32_scaled(category: int, sub_id: int, scaled_by_100: int) -> PlatLinkPacket:
    """Oppretter en Fixed32-pakke fra en verdi som allerede er multiplisert med 100."""
    type_id = make_type_id(FMT_FIXED32, category)
    return create_i32(type_id, sub_id, scaled_by_100)


def create_fixed32(category: int, sub_id: int, value: float) -> PlatLinkPacket:
    """Oppretter en Fixed32-pakke ved å avrunde float * 100 til nærmeste heltall."""
    scaled = round(value * FIXED32_SCALE)
    return create_fixed32_scaled(category, sub_id, scaled)


def create_digital(category: int, sub_id: int, state: bool) -> PlatLinkPacket:
    """Oppretter en Digital-pakke (format 0x1) med 1 for True og 0 for False."""
    type_id = make_type_id(FMT_DIGITAL, category)
    return create_u32(type_id, sub_id, 1 if state else 0)


def create_analog8(category: int, sub_id: int, value: int) -> PlatLinkPacket:
    """Oppretter en Analog8-pakke (format 0x2) med 8-bit verdi (0..255)."""
    if not (0 <= value <= 0xFF):
        raise PlatLinkError(f"Analog8-verdi må være 0..255, fikk {value}")
    type_id = make_type_id(FMT_ANALOG8, category)
    return create_u32(type_id, sub_id, value)


def validate_bytes(raw: bytes | bytearray | memoryview) -> None:
    """Validerer at rådata har lengde 8, header 0xA5, gyldig padding og gyldig CRC-8."""
    data = bytes(raw)
    if len(data) != PACKET_SIZE:
        raise PlatLinkError(f"Pakke må være {PACKET_SIZE} byte, fikk {len(data)}")
    if data[0] != HEADER:
        raise PlatLinkError(f"Ugyldig header: forventet 0x{HEADER:02X}, fikk 0x{data[0]:02X}")
    _validate_padding(data)
    expected = crc8(data[: PACKET_SIZE - 1])
    actual = data[PACKET_SIZE - 1]
    if expected != actual:
        raise PlatLinkError(f"Ugyldig CRC-8: beregnet 0x{expected:02X}, fikk 0x{actual:02X}")


def from_bytes(raw: bytes | bytearray | memoryview) -> PlatLinkPacket:
    """Validerer og parser en 8-byte ramme til en PlatLinkPacket."""
    validate_bytes(raw)
    data = bytes(raw)
    return PlatLinkPacket(
        header=data[0],
        type_id=data[1],
        sub_id=data[2],
        payload=data[3:7],
        crc8=data[7],
    )
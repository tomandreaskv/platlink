"""Enhetstester for PlatLink Python-pakken mot felles testvektorer i protocol_v1.json."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from platlink import (
    CAT_EC,
    CAT_PH,
    CAT_SYSTEM,
    CAT_TEMPERATURE,
    CAT_WATER_LEVEL,
    FMT_DIGITAL,
    FMT_FIXED32,
    FMT_INT32,
    FMT_RAW32,
    FMT_SYSTEM,
    SYS_HEARTBEAT,
    OtaTransfer,
    PlatLinkError,
    create_digital,
    create_fixed32_scaled,
    create_u32,
    from_bytes,
    validate_bytes,
)


class PlatLinkProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec_path = Path(__file__).resolve().parents[2] / "protocol_v1.json"
        with spec_path.open("r", encoding="utf-8") as f:
            cls.spec = json.load(f)

    def test_protocol_vectors_from_json(self) -> None:
        vectors = self.spec.get("test_vectors", [])
        self.assertGreater(len(vectors), 0, "Ingen testvektorer funnet i protocol_v1.json")

        for tv in vectors:
            with self.subTest(vector=tv["name"]):
                raw = bytes.fromhex(tv["hex"])
                if not tv["valid"]:
                    with self.assertRaises(PlatLinkError):
                        validate_bytes(raw)
                    with self.assertRaises(PlatLinkError):
                        from_bytes(raw)
                    continue

                validate_bytes(raw)
                pkt = from_bytes(raw)

                expected_type_id = int(tv["type_id"], 16)
                expected_sub_id = int(tv["sub_id"], 16)
                expected_fmt = int(tv["format"], 16)
                expected_cat = int(tv["category"], 16)

                self.assertEqual(pkt.type_id, expected_type_id)
                self.assertEqual(pkt.sub_id, expected_sub_id)
                self.assertEqual(pkt.format_code, expected_fmt)
                self.assertEqual(pkt.category_code, expected_cat)

                fmt = pkt.format_code
                if fmt == FMT_FIXED32:
                    self.assertAlmostEqual(pkt.as_fixed32_float(), tv["decoded_value"], places=6)
                elif fmt == FMT_INT32:
                    self.assertEqual(pkt.as_i32(), tv["decoded_value"])
                elif fmt in (FMT_RAW32, FMT_SYSTEM):
                    self.assertEqual(pkt.as_u32(), tv["decoded_value"])
                elif fmt == FMT_DIGITAL:
                    self.assertEqual(pkt.as_digital(), tv["decoded_value"])
                else:
                    self.fail(f"Uventet formatkode i testvektor: 0x{fmt:X}")

                self.assertEqual(pkt.to_bytes().hex().upper(), tv["hex"].upper())

    def test_creators_and_negative_values(self) -> None:
        temp_pkt = create_fixed32_scaled(CAT_TEMPERATURE, 0x01, 2253)
        self.assertEqual(temp_pkt.to_bytes().hex().upper(), "A53101000008CDAA")
        self.assertAlmostEqual(temp_pkt.as_fixed32_float(), 22.53)

        neg_pkt = create_fixed32_scaled(CAT_TEMPERATURE, 0x01, -1050)
        self.assertEqual(neg_pkt.to_bytes().hex().upper(), "A53101FFFFFBE6AA")
        self.assertAlmostEqual(neg_pkt.as_fixed32_float(), -10.50)

        ph_pkt = create_fixed32_scaled(CAT_PH, 0x02, 620)
        self.assertEqual(ph_pkt.to_bytes().hex().upper(), "A534020000026C6D")

        ec_pkt = create_fixed32_scaled(CAT_EC, 0x03, 125050)
        self.assertEqual(ec_pkt.to_bytes().hex().upper(), "A535030001E87AEE")

        lvl_pkt = create_u32((FMT_RAW32 << 4) | CAT_WATER_LEVEL, 0x04, 45)
        self.assertEqual(lvl_pkt.to_bytes().hex().upper(), "A546040000002D4D")

        dig_pkt = create_digital(CAT_SYSTEM, 0x05, True)
        self.assertEqual(dig_pkt.to_bytes().hex().upper(), "A51F050000000165")

        hb_pkt = create_u32(SYS_HEARTBEAT, 0x01, 3600)
        self.assertEqual(hb_pkt.to_bytes().hex().upper(), "A5F00100000E107A")
        self.assertEqual(hb_pkt.format_code, FMT_SYSTEM)
        self.assertEqual(hb_pkt.category_code, 0x0)

    def test_ota_transfer_chunks(self) -> None:
        image = b"ABCDEFGH12"  # 10 byte -> 3 chunks (4B, 4B, 2B + 2B padding)
        transfer = OtaTransfer(image)
        self.assertEqual(transfer.size, 10)

        init_pkt = transfer.init_packet()
        self.assertEqual(init_pkt.type_id, 0xFC)
        self.assertEqual(init_pkt.sub_id, 0x01)
        self.assertEqual(init_pkt.as_u32(), 10)

        chunks = transfer.iter_chunks()
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].sub_id, 0)
        self.assertEqual(chunks[0].packet.payload, b"ABCD")
        self.assertEqual(chunks[1].sub_id, 1)
        self.assertEqual(chunks[1].packet.payload, b"EFGH")
        self.assertEqual(chunks[2].sub_id, 2)
        self.assertEqual(chunks[2].packet.payload, b"12\x00\x00")

        verify_pkt = transfer.verify_packet()
        self.assertEqual(verify_pkt.type_id, 0xFC)
        self.assertEqual(verify_pkt.sub_id, 0x03)
        self.assertEqual(verify_pkt.as_u32(), transfer.crc32)


if __name__ == "__main__":
    unittest.main()
/**
 * @file test_c.c
 * @brief Enhetstester for PlatLink C-biblioteket mot felles testvektorer.
 */

#include "platlink.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

/* Beskriver én testvektor (gyldig eller ugyldig) fra protocol_v1.json. */
typedef struct {
    const char *name;
    uint8_t raw[PLATLINK_PACKET_SIZE];
    bool valid;
    uint8_t expected_type_id;
    uint8_t expected_sub_id;
    uint8_t expected_format;
    uint8_t expected_category;
    int32_t expected_i32;
    uint32_t expected_u32;
} test_vector_t;

/* Verifiserer validering, parsing, nibble-uttrekk, verdidekoding og roundtrip. */
static void test_protocol_vectors(void) {
    const test_vector_t vectors[] = {
        {
            .name = "Temperature 22.53 C (Fixed32)",
            .raw = {0xA5, 0x31, 0x01, 0x00, 0x00, 0x08, 0xCD, 0xAA},
            .valid = true,
            .expected_type_id = 0x31,
            .expected_sub_id = 0x01,
            .expected_format = PLATLINK_FMT_FIXED32,
            .expected_category = PLATLINK_CAT_TEMPERATURE,
            .expected_i32 = 2253,
            .expected_u32 = 2253U
        },
        {
            .name = "Negative Temperature -10.50 C (Fixed32)",
            .raw = {0xA5, 0x31, 0x01, 0xFF, 0xFF, 0xFB, 0xE6, 0xAA},
            .valid = true,
            .expected_type_id = 0x31,
            .expected_sub_id = 0x01,
            .expected_format = PLATLINK_FMT_FIXED32,
            .expected_category = PLATLINK_CAT_TEMPERATURE,
            .expected_i32 = -1050,
            .expected_u32 = 0xFFFFFBE6U
        },
        {
            .name = "pH 6.20 (Fixed32)",
            .raw = {0xA5, 0x34, 0x02, 0x00, 0x00, 0x02, 0x6C, 0x6D},
            .valid = true,
            .expected_type_id = 0x34,
            .expected_sub_id = 0x02,
            .expected_format = PLATLINK_FMT_FIXED32,
            .expected_category = PLATLINK_CAT_PH,
            .expected_i32 = 620,
            .expected_u32 = 620U
        },
        {
            .name = "EC 1250.50 uS/cm (Fixed32)",
            .raw = {0xA5, 0x35, 0x03, 0x00, 0x01, 0xE8, 0x7A, 0xEE},
            .valid = true,
            .expected_type_id = 0x35,
            .expected_sub_id = 0x03,
            .expected_format = PLATLINK_FMT_FIXED32,
            .expected_category = PLATLINK_CAT_EC,
            .expected_i32 = 125050,
            .expected_u32 = 125050U
        },
        {
            .name = "Water level 45 cm (Raw32)",
            .raw = {0xA5, 0x46, 0x04, 0x00, 0x00, 0x00, 0x2D, 0x4D},
            .valid = true,
            .expected_type_id = 0x46,
            .expected_sub_id = 0x04,
            .expected_format = PLATLINK_FMT_RAW32,
            .expected_category = PLATLINK_CAT_WATER_LEVEL,
            .expected_i32 = 45,
            .expected_u32 = 45U
        },
        {
            .name = "Digital ON (0x1F)",
            .raw = {0xA5, 0x1F, 0x05, 0x00, 0x00, 0x00, 0x01, 0x65},
            .valid = true,
            .expected_type_id = 0x1F,
            .expected_sub_id = 0x05,
            .expected_format = PLATLINK_FMT_DIGITAL,
            .expected_category = PLATLINK_CAT_SYSTEM,
            .expected_i32 = 1,
            .expected_u32 = 1U
        },
        {
            .name = "System Heartbeat 3600s (0xF0)",
            .raw = {0xA5, 0xF0, 0x01, 0x00, 0x00, 0x0E, 0x10, 0x7A},
            .valid = true,
            .expected_type_id = PLATLINK_SYS_HEARTBEAT,
            .expected_sub_id = 0x01,
            .expected_format = PLATLINK_FMT_SYSTEM,
            .expected_category = 0x0U,
            .expected_i32 = 3600,
            .expected_u32 = 3600U
        },
        {
            .name = "Invalid CRC8",
            .raw = {0xA5, 0x31, 0x01, 0x00, 0x00, 0x08, 0xCD, 0x00},
            .valid = false,
            .expected_type_id = 0,
            .expected_sub_id = 0,
            .expected_format = 0,
            .expected_category = 0,
            .expected_i32 = 0,
            .expected_u32 = 0U
        }
    };

    size_t count = sizeof(vectors) / sizeof(vectors[0]);
    for (size_t i = 0; i < count; i++) {
        const test_vector_t *tv = &vectors[i];
        bool ok = platlink_validate_bytes(tv->raw);
        assert(ok == tv->valid);

        platlink_packet_t pkt;
        bool parsed = platlink_from_bytes(&pkt, tv->raw);
        assert(parsed == tv->valid);

        if (tv->valid) {
            assert(platlink_validate(&pkt));
            assert(pkt.header == PLATLINK_HEADER);
            assert(pkt.type_id == tv->expected_type_id);
            assert(pkt.sub_id == tv->expected_sub_id);
            assert(platlink_get_format(pkt.type_id) == tv->expected_format);
            assert(platlink_get_category(pkt.type_id) == tv->expected_category);
            assert(platlink_decode_i32(pkt.payload) == tv->expected_i32);
            assert(platlink_decode_u32(pkt.payload) == tv->expected_u32);

            /* Sjekk at pakken kan serialiseres tilbake til nøyaktig samme bytes */
            uint8_t roundtrip[PLATLINK_PACKET_SIZE];
            platlink_to_bytes(&pkt, roundtrip);
            assert(memcmp(roundtrip, tv->raw, PLATLINK_PACKET_SIZE) == 0);
        }
    }
}

/* Verifiserer at hjelpefunksjonene bygger pakker med forventet byte-innhold og CRC-8. */
static void test_packet_creators(void) {
    platlink_packet_t pkt;
    uint8_t raw[PLATLINK_PACKET_SIZE];

    platlink_create_fixed32_scaled(&pkt, PLATLINK_CAT_TEMPERATURE, 0x01U, 2253);
    platlink_to_bytes(&pkt, raw);
    assert(platlink_validate_bytes(raw));
    assert(raw[0] == 0xA5U);
    assert(raw[1] == 0x31U);
    assert(raw[2] == 0x01U);
    assert(raw[7] == 0xAAU);

    platlink_create_fixed32_scaled(&pkt, PLATLINK_CAT_TEMPERATURE, 0x01U, -1050);
    platlink_to_bytes(&pkt, raw);
    assert(platlink_validate_bytes(raw));
    assert(raw[7] == 0xAAU);

    platlink_create_u32(&pkt, PLATLINK_SYS_HEARTBEAT, 0x01U, 3600U);
    platlink_to_bytes(&pkt, raw);
    assert(platlink_validate_bytes(raw));
    assert(raw[7] == 0x7AU);
}

int main(void) {
    test_protocol_vectors();
    test_packet_creators();
    printf("PlatLink C tests passed!\n");
    return 0;
}
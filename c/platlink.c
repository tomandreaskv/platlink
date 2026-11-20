/**
 * @file platlink.c
 * @brief Implementasjon av PlatLink v1.0 kjernefunksjoner.
 *
 * Alle funksjoner er skrevet uten dynamisk allokering (ingen malloc/free) og
 * bruker eksplisitt byte-skifting for å være uavhengige av arkitekturens
 * egen endianness og alignment-krav.
 */

#include "platlink.h"

/* Bitvis CRC-8 over len byte med polynom 0x07 og init 0x00. */
uint8_t platlink_crc8(const uint8_t *data, size_t len) {
    uint8_t crc = PLATLINK_CRC_INIT;

    if (data == NULL) {
        return crc;
    }

    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        /* Skift gjennom alle 8 bit i gjeldende byte */
        for (uint8_t bit = 0; bit < 8U; bit++) {
            if ((crc & 0x80U) != 0U) {
                crc = (uint8_t)((crc << 1) ^ PLATLINK_CRC_POLYNOMIAL);
            } else {
                crc = (uint8_t)(crc << 1);
            }
        }
    }

    return crc;
}

/* Skriver uint32 i big-endian rekkefølge (MSB først i payload[0]). */
void platlink_encode_u32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], uint32_t value) {
    payload[0] = (uint8_t)((value >> 24) & 0xFFU);
    payload[1] = (uint8_t)((value >> 16) & 0xFFU);
    payload[2] = (uint8_t)((value >> 8) & 0xFFU);
    payload[3] = (uint8_t)(value & 0xFFU);
}

/* Leser 4 big-endian byte og bygger et uint32. */
uint32_t platlink_decode_u32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]) {
    return ((uint32_t)payload[0] << 24) |
           ((uint32_t)payload[1] << 16) |
           ((uint32_t)payload[2] << 8)  |
           ((uint32_t)payload[3]);
}

/* Koder signert int32 som toerkomplement i big-endian. */
void platlink_encode_i32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], int32_t value) {
    platlink_encode_u32(payload, (uint32_t)value);
}

/* Dekoder 4 big-endian byte som signert int32. */
int32_t platlink_decode_i32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]) {
    return (int32_t)platlink_decode_u32(payload);
}

/* Hjelpefunksjon som pakker de første 7 byte i rekkefølge før CRC-8 beregnes. */
static uint8_t compute_packet_crc(const platlink_packet_t *pkt) {
    uint8_t frame[PLATLINK_PACKET_SIZE - 1U];
    frame[0] = pkt->header;
    frame[1] = pkt->type_id;
    frame[2] = pkt->sub_id;
    for (size_t i = 0; i < PLATLINK_PAYLOAD_SIZE; i++) {
        frame[3U + i] = pkt->payload[i];
    }
    return platlink_crc8(frame, sizeof(frame));
}

/* Fyller ut header, type_id, sub_id, 4-byte payload og beregner crc8. */
void platlink_pack(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                   const uint8_t payload[PLATLINK_PAYLOAD_SIZE]) {
    if (pkt == NULL || payload == NULL) {
        return;
    }

    pkt->header = PLATLINK_HEADER;
    pkt->type_id = type_id;
    pkt->sub_id = sub_id;
    for (size_t i = 0; i < PLATLINK_PAYLOAD_SIZE; i++) {
        pkt->payload[i] = payload[i];
    }
    pkt->crc8 = compute_packet_crc(pkt);
}

/* Oppretter pakke med usignert 32-bit nyttelast. */
void platlink_create_u32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         uint32_t value) {
    uint8_t buf[PLATLINK_PAYLOAD_SIZE];
    platlink_encode_u32(buf, value);
    platlink_pack(pkt, type_id, sub_id, buf);
}

/* Oppretter pakke med signert 32-bit nyttelast. */
void platlink_create_i32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         int32_t value) {
    uint8_t buf[PLATLINK_PAYLOAD_SIZE];
    platlink_encode_i32(buf, value);
    platlink_pack(pkt, type_id, sub_id, buf);
}

/* Oppretter Fixed32-pakke (format 0x3) for gitt kategori og skalert verdi (* 100). */
void platlink_create_fixed32_scaled(platlink_packet_t *pkt, uint8_t category,
                                    uint8_t sub_id, int32_t scaled_by_100) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_FIXED32, category);
    platlink_create_i32(pkt, type_id, sub_id, scaled_by_100);
}

/* Oppretter Digital-pakke (format 0x1) der payload[3] er 1 for true og 0 for false. */
void platlink_create_digital(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, bool state) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_DIGITAL, category);
    platlink_create_u32(pkt, type_id, sub_id, state ? 1U : 0U);
}

/* Oppretter Analog8-pakke (format 0x2) med 8-bit verdi plassert i payload[3]. */
void platlink_create_analog8(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, uint8_t value) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_ANALOG8, category);
    platlink_create_u32(pkt, type_id, sub_id, (uint32_t)value);
}

/* Sjekker at første byte er 0xA5 og at CRC-8 over byte 0..6 matcher byte 7. */
bool platlink_validate_bytes(const uint8_t raw[PLATLINK_PACKET_SIZE]) {
    if (raw == NULL) {
        return false;
    }
    if (raw[0] != PLATLINK_HEADER) {
        return false;
    }
    uint8_t expected = platlink_crc8(raw, PLATLINK_PACKET_SIZE - 1U);
    return expected == raw[PLATLINK_PACKET_SIZE - 1U];
}

/* Serialiserer pakken til bytes og kjører standard byte-validering. */
bool platlink_validate(const platlink_packet_t *pkt) {
    uint8_t raw[PLATLINK_PACKET_SIZE];
    if (pkt == NULL) {
        return false;
    }
    platlink_to_bytes(pkt, raw);
    return platlink_validate_bytes(raw);
}

/* Kopierer feltene fra struct til flat 8-byte buffer i protokollrekkefølge. */
void platlink_to_bytes(const platlink_packet_t *pkt, uint8_t out[PLATLINK_PACKET_SIZE]) {
    if (pkt == NULL || out == NULL) {
        return;
    }
    out[0] = pkt->header;
    out[1] = pkt->type_id;
    out[2] = pkt->sub_id;
    for (size_t i = 0; i < PLATLINK_PAYLOAD_SIZE; i++) {
        out[3U + i] = pkt->payload[i];
    }
    out[7] = pkt->crc8;
}

/* Validerer innkommende 8-byte buffer og fyller ut platlink_packet_t ved suksess. */
bool platlink_from_bytes(platlink_packet_t *pkt, const uint8_t raw[PLATLINK_PACKET_SIZE]) {
    if (pkt == NULL || !platlink_validate_bytes(raw)) {
        return false;
    }
    pkt->header = raw[0];
    pkt->type_id = raw[1];
    pkt->sub_id = raw[2];
    for (size_t i = 0; i < PLATLINK_PAYLOAD_SIZE; i++) {
        pkt->payload[i] = raw[3U + i];
    }
    pkt->crc8 = raw[7];
    return true;
}
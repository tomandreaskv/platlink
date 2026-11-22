/**
 * @file platlink.c
 * @brief Implementasjon av PlatLink v1.1 kjernefunksjoner og byte-strømleser.
 */

#include "platlink.h"
#include <string.h>

/* Bitvis CRC-8 over len byte med polynom 0x07 og init 0x00 */
uint8_t platlink_crc8(const uint8_t *data, size_t len) {
    uint8_t crc = PLATLINK_CRC_INIT;

    if (data == NULL) {
        return crc;
    }

    for (size_t i = 0; i < len; i++) {
        crc = (uint8_t)(crc ^ data[i]);
        /* Skift i uint32_t før maskering til uint8_t for å unngå -Wsign-conversion */
        for (uint8_t bit = 0U; bit < 8U; bit++) {
            uint32_t shifted = (uint32_t)crc << 1U;
            if ((crc & 0x80U) != 0U) {
                shifted ^= (uint32_t)PLATLINK_CRC_POLYNOMIAL;
            }
            crc = (uint8_t)(shifted & 0xFFU);
        }
    }

    return crc;
}

/* Skriver uint32 som 4 big-endian byte (payload[0] = MSB) */
void platlink_encode_u32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], uint32_t value) {
    payload[0] = (uint8_t)((value >> 24) & 0xFFU);
    payload[1] = (uint8_t)((value >> 16) & 0xFFU);
    payload[2] = (uint8_t)((value >> 8) & 0xFFU);
    payload[3] = (uint8_t)(value & 0xFFU);
}

/* Leser 4 big-endian byte til uint32 */
uint32_t platlink_decode_u32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]) {
    return ((uint32_t)payload[0] << 24) |
           ((uint32_t)payload[1] << 16) |
           ((uint32_t)payload[2] << 8)  |
           ((uint32_t)payload[3]);
}

/* Koder signert int32 (toerkomplement) i big-endian */
void platlink_encode_i32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], int32_t value) {
    platlink_encode_u32(payload, (uint32_t)value);
}

/* Dekoder 4 big-endian byte som signert int32 */
int32_t platlink_decode_i32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]) {
    return (int32_t)platlink_decode_u32(payload);
}

/* Pakker byte 0..6 i trådrekkefølge og beregner CRC-8 */
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

/* Fyller ut pakkestruktur med startbyte 0xA5 og beregnet CRC-8 */
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

/* Oppretter pakke med usignert 32-bit verdi */
void platlink_create_u32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         uint32_t value) {
    uint8_t buf[PLATLINK_PAYLOAD_SIZE];
    platlink_encode_u32(buf, value);
    platlink_pack(pkt, type_id, sub_id, buf);
}

/* Oppretter pakke med signert 32-bit verdi */
void platlink_create_i32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         int32_t value) {
    uint8_t buf[PLATLINK_PAYLOAD_SIZE];
    platlink_encode_i32(buf, value);
    platlink_pack(pkt, type_id, sub_id, buf);
}

/* Oppretter Fixed32-pakke (format 0x3) fra allerede skalert verdi (* 100) */
void platlink_create_fixed32_scaled(platlink_packet_t *pkt, uint8_t category,
                                    uint8_t sub_id, int32_t scaled_by_100) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_FIXED32, category);
    platlink_create_i32(pkt, type_id, sub_id, scaled_by_100);
}

/* Oppretter Digital-pakke (format 0x1); payload[0..2] er 0 og payload[3] er 0 eller 1 */
void platlink_create_digital(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, bool state) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_DIGITAL, category);
    platlink_create_u32(pkt, type_id, sub_id, state ? 1U : 0U);
}

/* Oppretter Analog8-pakke (format 0x2); payload[0..2] er 0 og payload[3] er 0..255 */
void platlink_create_analog8(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, uint8_t value) {
    uint8_t type_id = platlink_make_typeid(PLATLINK_FMT_ANALOG8, category);
    platlink_create_u32(pkt, type_id, sub_id, (uint32_t)value);
}

/* Sjekker at payload[0..2] er null for 1-byte formater (Digital og Analog8) */
static bool has_valid_padding(const uint8_t raw[PLATLINK_PACKET_SIZE]) {
    uint8_t fmt = platlink_get_format(raw[1]);
    if (fmt == PLATLINK_FMT_DIGITAL || fmt == PLATLINK_FMT_ANALOG8) {
        if (raw[3] != 0x00U || raw[4] != 0x00U || raw[5] != 0x00U) {
            return false;
        }
    }
    return true;
}

/* Sjekker header (0xA5), padding-regler og CRC-8 (byte 0..6 mot byte 7) */
bool platlink_validate_bytes(const uint8_t raw[PLATLINK_PACKET_SIZE]) {
    if (raw == NULL || raw[0] != PLATLINK_HEADER) {
        return false;
    }
    if (!has_valid_padding(raw)) {
        return false;
    }
    uint8_t expected = platlink_crc8(raw, PLATLINK_PACKET_SIZE - 1U);
    return expected == raw[PLATLINK_PACKET_SIZE - 1U];
}

/* Sjekker header (0xA5) og CRC-8 (byte 0..6 mot byte 7) på rå 8-byte buffer */
bool platlink_validate_bytes(const uint8_t raw[PLATLINK_PACKET_SIZE]) {
    if (raw == NULL || raw[0] != PLATLINK_HEADER) {
        return false;
    }
    uint8_t expected = platlink_crc8(raw, PLATLINK_PACKET_SIZE - 1U);
    return expected == raw[PLATLINK_PACKET_SIZE - 1U];
}

/* Serialiserer pakkestruktur og sjekker header og CRC-8 */
bool platlink_validate(const platlink_packet_t *pkt) {
    uint8_t raw[PLATLINK_PACKET_SIZE];
    if (pkt == NULL) {
        return false;
    }
    platlink_to_bytes(pkt, raw);
    return platlink_validate_bytes(raw);
}

/* Serialiserer pakkestruktur til flat 8-byte buffer i trådrekkefølge */
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

/* Validerer og pakker ut rå 8-byte buffer til platlink_packet_t */
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

/* Nullstiller strømleseren til ventende header-tilstand */
void platlink_reader_init(platlink_reader_t *reader) {
    if (reader == NULL) {
        return;
    }
    memset(reader, 0, sizeof(*reader));
    reader->state = PLATLINK_READER_WAIT_HEADER;
    reader->index = 0U;
}

/*
 * Ved feil CRC kan enten selve rammen være korrupt eller et byte ha gått tapt,
 * slik at starten på neste ramme allerede ligger inne i buf[1..index-1].
 * Vi leter etter første 0xA5 etter indeks 0 og forskyver restbytes til start.
 */
static void platlink_reader_resync(platlink_reader_t *reader) {
    for (size_t i = 1U; i < reader->index; i++) {
        if (reader->buf[i] == PLATLINK_HEADER) {
            size_t remaining = reader->index - i;
            memmove(reader->buf, &reader->buf[i], remaining);
            reader->index = remaining;
            reader->state = PLATLINK_READER_COLLECT_BODY;
            return;
        }
    }

    reader->index = 0U;
    reader->state = PLATLINK_READER_WAIT_HEADER;
}

/* Mater én byte inn i strømleseren; returnerer true når en gyldig 8-byte ramme er klar */
bool platlink_reader_feed(platlink_reader_t *reader, uint8_t byte, platlink_packet_t *out_pkt) {
    if (reader == NULL || out_pkt == NULL) {
        return false;
    }

    if (reader->state == PLATLINK_READER_WAIT_HEADER) {
        if (byte == PLATLINK_HEADER) {
            reader->buf[0] = byte;
            reader->index = 1U;
            reader->state = PLATLINK_READER_COLLECT_BODY;
        }
        return false;
    }

    reader->buf[reader->index] = byte;
    reader->index++;

    if (reader->index < PLATLINK_PACKET_SIZE) {
        return false;
    }

    if (platlink_from_bytes(out_pkt, reader->buf)) {
        reader->frames_ok++;
        reader->index = 0U;
        reader->state = PLATLINK_READER_WAIT_HEADER;
        return true;
    }

    reader->crc_errors++;
    platlink_reader_resync(reader);
    return false;
}
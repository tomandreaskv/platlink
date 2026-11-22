/**
 * @file platlink.h
 * @brief Kjerneheader for PlatLink v1.1 binærprotokoll (fast 8-byte ramme, CRC-8, ingen malloc).
 */

#ifndef PLATLINK_H
#define PLATLINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Protokollkonstanter */
#define PLATLINK_HEADER         0xA5U /* Startbyte for rammesynkronisering */
#define PLATLINK_PACKET_SIZE    8U    /* Fast pakkestørrelse i byte */
#define PLATLINK_PAYLOAD_SIZE   4U    /* Fast nyttelaststørrelse i byte (big-endian) */
#define PLATLINK_CRC_POLYNOMIAL 0x07U /* CRC-8/SMBUS polynom (x^8 + x^2 + x + 1) */
#define PLATLINK_CRC_INIT       0x00U /* Startverdi for CRC-8 */
#define PLATLINK_FIXED32_SCALE  100   /* Skaleringsfaktor for Fixed32 (2 desimaler) */

/* Formater (høy nibble i TypeID: bits 7..4) */
typedef enum {
    PLATLINK_FMT_DIGITAL = 0x1U, /* Boolsk verdi i payload[3] (0 = false, 1 = true); payload[0..2] = 0 */
    PLATLINK_FMT_ANALOG8 = 0x2U, /* Usignert 8-bit i payload[3] (0..255); payload[0..2] = 0 */
    PLATLINK_FMT_FIXED32 = 0x3U, /* Signert 32-bit skalert med 100 (int32 / 100) */
    PLATLINK_FMT_RAW32   = 0x4U, /* Usignert 32-bit heltall (uint32) */
    PLATLINK_FMT_INT32   = 0x5U, /* Signert 32-bit heltall uten skalering (int32) */
    PLATLINK_FMT_SYSTEM  = 0xFU  /* System-, tidsbase-, discovery- og OTA-meldinger (0xF0..0xFF) */
} platlink_format_t;

/* Kategorier (lav nibble i TypeID: bits 3..0) */
typedef enum {
    PLATLINK_CAT_TEMPERATURE = 0x1U, /* Temperatur (°C) */
    PLATLINK_CAT_HUMIDITY    = 0x2U, /* Fuktighet (%) */
    PLATLINK_CAT_LIGHT       = 0x3U, /* Lys (lux) */
    PLATLINK_CAT_PH          = 0x4U, /* pH-verdi */
    PLATLINK_CAT_EC          = 0x5U, /* Elektrisk konduktivitet (µS/cm) */
    PLATLINK_CAT_WATER_LEVEL = 0x6U, /* Vannstand / dybde (cm) */
    PLATLINK_CAT_PRESSURE    = 0x7U, /* Trykk (hPa) */
    PLATLINK_CAT_ACTUATOR    = 0x8U, /* Aktuator / relé (krever echo/ACK + lokal fail-safe) */
    PLATLINK_CAT_POSITION    = 0x9U, /* Posisjon (f.eks. grader * 1e7 som int32) */
    PLATLINK_CAT_CUSTOM      = 0xEU, /* Applikasjonsspesifikke utvidelser */
    PLATLINK_CAT_SYSTEM      = 0xFU  /* Systemkategori */
} platlink_category_t;

/* Systemmeldinger (TypeID i 0xF0..0xFF) */
typedef enum {
    PLATLINK_SYS_HEARTBEAT   = 0xF0U, /* Oppetid i sekunder (uint32) */
    PLATLINK_SYS_TIMEBASE    = 0xF1U, /* Felles tidsreferanse (SubID 0x00=sek, 0x01=ms) */
    PLATLINK_SYS_TIME_OFFSET = 0xF2U, /* Relativ tidsforskyvning i ms for etterfylte logger */
    PLATLINK_SYS_DISCOVERY   = 0xF4U, /* Node-annonsering / kapabilitetsmelding */
    PLATLINK_SYS_OTA_CTRL    = 0xFCU, /* OTA-kontroll (SubID = underkommando) */
    PLATLINK_SYS_OTA_DATA    = 0xFDU, /* OTA-firmwaredata (SubID = chunk-indeks, 4B data) */
    PLATLINK_SYS_OTA_STATUS  = 0xFEU, /* OTA-kvittering / status (SubID = statuskode) */
    PLATLINK_SYS_RESET       = 0xFFU  /* Tilbakestilling / safe stop */
} platlink_system_msg_t;

/* OTA-underkommandoer og statuskoder */
typedef enum {
    PLATLINK_OTA_CMD_INIT         = 0x01U, /* Start OTA; payload[0..1]=størrelse, payload[2..3]=node ID */
    PLATLINK_OTA_CMD_EXPECTED_CRC = 0x02U, /* Forventet CRC32 over hele bildet */
    PLATLINK_OTA_CMD_VERIFY       = 0x03U, /* Verifiser overføring med CRC32 */
    PLATLINK_OTA_STATUS_ACK       = 0x20U, /* Blokk-ACK; payload[0..1]=neste byte-offset */
    PLATLINK_OTA_STATUS_READY     = 0x21U, /* Bilde verifisert og klart */
    PLATLINK_OTA_STATUS_NAK_OFF   = 0xE1U, /* NAK; payload[0..1]=forventet byte-offset */
    PLATLINK_OTA_STATUS_ERR_CRC   = 0xE2U  /* CRC-feil ved sluttverifisering */
} platlink_ota_cmd_t;

/* Komplett 8-byte ramme på tråd/buss: [0]=header, [1]=type_id, [2]=sub_id, [3..6]=payload, [7]=crc8 */
typedef struct {
    uint8_t header;
    uint8_t type_id;
    uint8_t sub_id;
    uint8_t payload[PLATLINK_PAYLOAD_SIZE];
    uint8_t crc8;
} platlink_packet_t;

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(platlink_packet_t) == PLATLINK_PACKET_SIZE,
               "platlink_packet_t must be 8 bytes");
#endif

/* Tilstander for byte-for-byte strømleser */
typedef enum {
    PLATLINK_READER_WAIT_HEADER = 0,
    PLATLINK_READER_COLLECT_BODY
} platlink_reader_state_t;

/* Tilstand og diagnostikk for inkrementell strømlesing uten dynamisk minne */
typedef struct {
    platlink_reader_state_t state;
    size_t index;
    uint8_t buf[PLATLINK_PACKET_SIZE];
    uint32_t frames_ok;
    uint32_t crc_errors;
} platlink_reader_t;

/* Pakker format (høy nibble) og kategori (lav nibble) inn i én TypeID-byte */
static inline uint8_t platlink_make_typeid(uint8_t format, uint8_t category) {
    return (uint8_t)(((format & 0x0FU) << 4) | (category & 0x0FU));
}

/* Henter format-nibblen (bits 7..4) fra TypeID */
static inline uint8_t platlink_get_format(uint8_t type_id) {
    return (uint8_t)((type_id >> 4) & 0x0FU);
}

/* Henter kategori-nibblen (bits 3..0) fra TypeID */
static inline uint8_t platlink_get_category(uint8_t type_id) {
    return (uint8_t)(type_id & 0x0FU);
}

/* Beregner CRC-8 (poly 0x07, init 0x00) over len byte */
uint8_t platlink_crc8(const uint8_t *data, size_t len);

/* Koder og dekoder usignert 32-bit heltall i big-endian (MSB først) */
void platlink_encode_u32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], uint32_t value);
uint32_t platlink_decode_u32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);

/* Koder og dekoder signert 32-bit heltall (toerkomplement) i big-endian */
void platlink_encode_i32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], int32_t value);
int32_t platlink_decode_i32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);

/* Bygger 8-byte pakker og beregner CRC-8 automatisk */
void platlink_pack(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                   const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);
void platlink_create_u32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         uint32_t value);
void platlink_create_i32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         int32_t value);
void platlink_create_fixed32_scaled(platlink_packet_t *pkt, uint8_t category,
                                    uint8_t sub_id, int32_t scaled_by_100);
void platlink_create_digital(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, bool state);
void platlink_create_analog8(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, uint8_t value);

/* Validerer header (0xA5) og CRC-8, og konverterer mellom struct og rå 8-byte buffer */
bool platlink_validate_bytes(const uint8_t raw[PLATLINK_PACKET_SIZE]);
bool platlink_validate(const platlink_packet_t *pkt);
void platlink_to_bytes(const platlink_packet_t *pkt, uint8_t out[PLATLINK_PACKET_SIZE]);
bool platlink_from_bytes(platlink_packet_t *pkt, const uint8_t raw[PLATLINK_PACKET_SIZE]);

/* Initialiserer og mater strømleseren én byte om gangen */
void platlink_reader_init(platlink_reader_t *reader);
bool platlink_reader_feed(platlink_reader_t *reader, uint8_t byte, platlink_packet_t *out_pkt);

#ifdef __cplusplus
}
#endif

#endif /* PLATLINK_H */
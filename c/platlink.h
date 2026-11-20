/**
 * @file platlink.h
 * @brief Kjerneheader for PlatLink v1.0 binærprotokoll.
 *
 * Definerer protokollkonstanter, formater (høy nibble), kategorier (lav nibble),
 * systemmeldinger, 8-byte pakkestruktur og funksjonsprototyper.
 * Designet for avhengighetsfri bruk på både vertsmaskiner og små MCU-er
 * (som CH32V003 uten maskinvare-FPU og uten dynamisk minneallokering).
 */

#ifndef PLATLINK_H
#define PLATLINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ========================================================================== */
/* Protokollkonstanter                                                        */
/* ========================================================================== */

#define PLATLINK_HEADER          0xA5U  /**< Fast startbyte for rammesynkronisering */
#define PLATLINK_PACKET_SIZE     8U     /**< Fast pakkestørrelse i byte */
#define PLATLINK_PAYLOAD_SIZE    4U     /**< Fast nyttelaststørrelse i byte (big-endian) */
#define PLATLINK_CRC_POLYNOMIAL  0x07U  /**< CRC-8 polynom (x^8 + x^2 + x + 1) */
#define PLATLINK_CRC_INIT        0x00U  /**< Startverdi for CRC-8 beregning */
#define PLATLINK_FIXED32_SCALE   100    /**< Skaleringsfaktor for Fixed32 (2 desimaler) */

/* ========================================================================== */
/* Formater (Høy nibble: bits 7..4 i TypeID)                                  */
/* ========================================================================== */

typedef enum {
    PLATLINK_FMT_DIGITAL  = 0x1U,  /**< Boolsk tilstand (0 = false, ellers true i payload[3]) */
    PLATLINK_FMT_ANALOG8  = 0x2U,  /**< Usignert 8-bit verdi (0..255 i payload[3]) */
    PLATLINK_FMT_FIXED32  = 0x3U,  /**< Signert 32-bit heltall skalert med 100 (int32 / 100) */
    PLATLINK_FMT_RAW32    = 0x4U,  /**< Usignert 32-bit heltall (uint32) */
    PLATLINK_FMT_INT32    = 0x5U,  /**< Signert 32-bit heltall uten skalering (int32) */
    PLATLINK_FMT_SYSTEM   = 0xFU   /**< System- og kontrollmeldinger (0xF0..0xFF) */
} platlink_format_t;

/* ========================================================================== */
/* Kategorier (Lav nibble: bits 3..0 i TypeID)                                 */
/* ========================================================================== */

typedef enum {
    PLATLINK_CAT_TEMPERATURE = 0x1U,  /**< Temperatur (°C) */
    PLATLINK_CAT_HUMIDITY    = 0x2U,  /**< Fuktighet (%) */
    PLATLINK_CAT_LIGHT       = 0x3U,  /**< Lys (lux) */
    PLATLINK_CAT_PH          = 0x4U,  /**< pH-verdi */
    PLATLINK_CAT_EC          = 0x5U,  /**< Elektrisk konduktivitet (µS/cm) */
    PLATLINK_CAT_WATER_LEVEL = 0x6U,  /**< Vannstand (cm) */
    PLATLINK_CAT_PRESSURE    = 0x7U,  /**< Trykk (hPa) */
    PLATLINK_CAT_CUSTOM      = 0xEU,  /**< Reservert for egne utvidelser */
    PLATLINK_CAT_SYSTEM      = 0xFU   /**< Systemkategori */
} platlink_category_t;

/* ========================================================================== */
/* Systemmeldinger og OTA-underkommandoer                                     */
/* ========================================================================== */

typedef enum {
    PLATLINK_SYS_HEARTBEAT = 0xF0U,  /**< Periodisk livstegn med oppetid i sekunder (uint32) */
    PLATLINK_SYS_DISCOVERY = 0xF4U,  /**< Node-annonsering / kapabilitetsmelding */
    PLATLINK_SYS_OTA_CTRL  = 0xFCU,  /**< OTA-kontroll (SubID angir underkommando) */
    PLATLINK_SYS_OTA_DATA  = 0xFDU,  /**< OTA-firmwaredata (SubID = chunk/sekvens, 4B data) */
    PLATLINK_SYS_RESET     = 0xFFU   /**< Tilbakestillingskommando */
} platlink_system_msg_t;

typedef enum {
    PLATLINK_OTA_CMD_INIT   = 0x01U,  /**< Start OTA-økt; payload = firmwarestørrelse i byte */
    PLATLINK_OTA_CMD_VERIFY = 0x03U   /**< Verifiser OTA-overføring; payload = forventet CRC32 */
} platlink_ota_cmd_t;

/* ========================================================================== */
/* Pakkestruktur (8 byte på tråd/wire)                                        */
/* ========================================================================== */

/**
 * @brief Representerer en komplett 8-byte PlatLink-ramme.
 *
 * Byte-rekkefølge på tråd:
 * [0]    header  (0xA5)
 * [1]    type_id ((format << 4) | category)
 * [2]    sub_id  (kanal/endpoint eller system-underkommando/sekvens)
 * [3..6] payload (4 byte big-endian)
 * [7]    crc8    (CRC-8 over byte 0..6)
 */
typedef struct {
    uint8_t header;
    uint8_t type_id;
    uint8_t sub_id;
    uint8_t payload[PLATLINK_PAYLOAD_SIZE];
    uint8_t crc8;
} platlink_packet_t;

/* ========================================================================== */
/* Nibble- og TypeID-hjelpere                                                 */
/* ========================================================================== */

/**
 * @brief Setter sammen format-nibble og kategori-nibble til én TypeID-byte.
 * @param format Høy nibble (0x0..0xF).
 * @param category Lav nibble (0x0..0xF).
 * @return Kombinert TypeID-byte.
 */
static inline uint8_t platlink_make_typeid(uint8_t format, uint8_t category) {
    return (uint8_t)(((format & 0x0FU) << 4) | (category & 0x0FU));
}

/**
 * @brief Henter ut format-nibblen (de 4 øvre bitene) fra en TypeID.
 * @param type_id TypeID-byte.
 * @return Formatkode i området 0x0..0xF.
 */
static inline uint8_t platlink_get_format(uint8_t type_id) {
    return (uint8_t)((type_id >> 4) & 0x0FU);
}

/**
 * @brief Henter ut kategori-nibblen (de 4 nedre bitene) fra en TypeID.
 * @param type_id TypeID-byte.
 * @return Kategorikode i området 0x0..0xF.
 */
static inline uint8_t platlink_get_category(uint8_t type_id) {
    return (uint8_t)(type_id & 0x0FU);
}

/* ========================================================================== */
/* Kjernefunksjoner                                                           */
/* ========================================================================== */

/**
 * @brief Beregner CRC-8 over en byte-sekvens med polynom 0x07 og init 0x00.
 * @param data Peker til databyte.
 * @param len Antall byte som skal inngå i sjekksummen.
 * @return Beregnet 8-bits CRC-verdi.
 */
uint8_t platlink_crc8(const uint8_t *data, size_t len);

/**
 * @brief Koder et usignert 32-bit heltall til 4-byte big-endian payload.
 * @param payload Målbuffer på 4 byte.
 * @param value Usignert 32-bit verdi.
 */
void platlink_encode_u32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], uint32_t value);

/**
 * @brief Dekoder 4-byte big-endian payload til et usignert 32-bit heltall.
 * @param payload Kildebuffer på 4 byte.
 * @return Dekodet uint32-verdi.
 */
uint32_t platlink_decode_u32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);

/**
 * @brief Koder et signert 32-bit heltall til 4-byte big-endian payload.
 * @param payload Målbuffer på 4 byte.
 * @param value Signert 32-bit verdi (toerkomplement).
 */
void platlink_encode_i32(uint8_t payload[PLATLINK_PAYLOAD_SIZE], int32_t value);

/**
 * @brief Dekoder 4-byte big-endian payload til et signert 32-bit heltall.
 * @param payload Kildebuffer på 4 byte.
 * @return Dekodet int32-verdi.
 */
int32_t platlink_decode_i32(const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);

/**
 * @brief Bygger en komplett pakke fra rå 4-byte payload og beregner CRC-8.
 * @param pkt Peker til pakkestruktur som skal fylles ut.
 * @param type_id TypeID-byte.
 * @param sub_id SubID-byte.
 * @param payload 4 byte ferdig formatert nyttelast.
 */
void platlink_pack(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                   const uint8_t payload[PLATLINK_PAYLOAD_SIZE]);

/**
 * @brief Oppretter en pakke med en usignert 32-bit verdi i big-endian.
 * @param pkt Peker til pakkestruktur.
 * @param type_id TypeID-byte.
 * @param sub_id SubID-byte.
 * @param value Usignert 32-bit verdi.
 */
void platlink_create_u32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         uint32_t value);

/**
 * @brief Oppretter en pakke med en signert 32-bit verdi i big-endian.
 * @param pkt Peker til pakkestruktur.
 * @param type_id TypeID-byte.
 * @param sub_id SubID-byte.
 * @param value Signert 32-bit verdi.
 */
void platlink_create_i32(platlink_packet_t *pkt, uint8_t type_id, uint8_t sub_id,
                         int32_t value);

/**
 * @brief Oppretter en Fixed32-pakke fra en allerede skalert int32-verdi (verdi * 100).
 * @param pkt Peker til pakkestruktur.
 * @param category Kategorikode (lav nibble).
 * @param sub_id SubID-byte.
 * @param scaled_by_100 Verdi multiplisert med 100 (f.eks. 2253 for 22.53).
 */
void platlink_create_fixed32_scaled(platlink_packet_t *pkt, uint8_t category,
                                    uint8_t sub_id, int32_t scaled_by_100);

/**
 * @brief Oppretter en Digital-pakke (format 0x1) med boolsk tilstand.
 * @param pkt Peker til pakkestruktur.
 * @param category Kategorikode (lav nibble).
 * @param sub_id SubID-byte.
 * @param state true for PÅ (1), false for AV (0).
 */
void platlink_create_digital(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, bool state);

/**
 * @brief Oppretter en Analog8-pakke (format 0x2) med 8-bit verdi (0..255).
 * @param pkt Peker til pakkestruktur.
 * @param category Kategorikode (lav nibble).
 * @param sub_id SubID-byte.
 * @param value 8-bit usignert verdi.
 */
void platlink_create_analog8(platlink_packet_t *pkt, uint8_t category,
                             uint8_t sub_id, uint8_t value);

/**
 * @brief Validerer header (0xA5) og CRC-8 på en rå 8-byte buffer.
 * @param raw Peker til 8 byte med mottatte/rå pakkedata.
 * @return true hvis header og CRC-8 stemmer, ellers false.
 */
bool platlink_validate_bytes(const uint8_t raw[PLATLINK_PACKET_SIZE]);

/**
 * @brief Validerer header (0xA5) og CRC-8 på en pakkestruktur.
 * @param pkt Peker til pakkestruktur.
 * @return true hvis header og CRC-8 stemmer, ellers false.
 */
bool platlink_validate(const platlink_packet_t *pkt);

/**
 * @brief Serialiserer en pakkestruktur til en flat 8-byte buffer i trådrekkefølge.
 * @param pkt Kilde-pakkestruktur.
 * @param out Målbuffer på 8 byte.
 */
void platlink_to_bytes(const platlink_packet_t *pkt, uint8_t out[PLATLINK_PACKET_SIZE]);

/**
 * @brief Validerer og parser en 8-byte buffer til en pakkestruktur.
 * @param pkt Mål-pakkestruktur.
 * @param raw Kildebuffer på 8 byte.
 * @return true hvis pakken er gyldig og kopiert inn i pkt, ellers false.
 */
bool platlink_from_bytes(platlink_packet_t *pkt, const uint8_t raw[PLATLINK_PACKET_SIZE]);

#ifdef __cplusplus
}
#endif

#endif /* PLATLINK_H */
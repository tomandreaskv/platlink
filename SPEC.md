# PlatLink v1.0 Specification

PlatLink er en lettvekts, fast-lengde **8-byte binærprotokoll** laget for mikrokontrollere med begrenset minne og uten flyttallsenhet (FPU), samtidig som den er enkel å bruke på tvers av språk, busser og nettverk/MQTT.

## 1. Pakkeformat (8 byte)

```text
+--------+--------+--------+--------------------+--------+
| Header | TypeID | SubID  | Payload (4 bytes)  |  CRC8  |
|  0xA5  |   1B   |   1B   |     Big-Endian     |   1B   |
+--------+--------+--------+--------------------+--------+
  Byte 0   Byte 1   Byte 2       Bytes 3..6        Byte 7
```

- **Header (Byte 0):** Alltid `0xA5`. Brukes til rammesynkronisering i strømmer.
- **TypeID (Byte 1):** Delt i to 4-bits nibbles:
  - **Høy nibble (`(type_id >> 4) & 0x0F`):** *Format* – hvordan payload skal tolkes.
  - **Lav nibble (`type_id & 0x0F`):** *Kategori* – hva målingen/meldingen gjelder.
- **SubID (Byte 2):**
  - For vanlige data/kanaler: kanal- eller endpoint-ID på noden (`0x00..0xEF`).
  - For systemmeldinger (`0xFX`): underkommando eller sekvensnummer.
- **Payload (Bytes 3..6):** 4 byte i **big-endian** nettverksrekkefølge.
- **CRC8 (Byte 7):** Beregnet over byte `0..6` med polynom `0x07` og startverdi `0x00` (ingen refleksjon, ingen XOR ut).

## 2. Formater (Høy nibble)

| Kode | Navn | Nøkkel | Payload-tolkning | Skala |
| :--- | :--- | :--- | :--- | :--- |
| `0x1` | Digital | `digital` | `0` = `false`, ellers `true` (`payload[3]`) | `1` |
| `0x2` | Analog8 | `analog8` | Usignert 8-bit `0..255` (`payload[3]`) | `1` |
| `0x3` | Fixed32 | `fixed32` | Signert 32-bit heltall (`int32`) delt på `100` | `100` |
| `0x4` | Raw32 | `raw32` | Usignert 32-bit heltall (`uint32`) | `1` |
| `0x5` | Int32 | `int32` | Signert 32-bit heltall (`int32`) | `1` |
| `0xF` | System | `system` | System- og kontrollmeldinger (`0xF0..0xFF`) | `1` |

## 3. Kategorier (Lav nibble)

| Kode | Navn | Nøkkel | Standard enhet | Typisk format |
| :--- | :--- | :--- | :--- | :--- |
| `0x1` | Temperatur | `temp` | `°C` | `0x3` (`Fixed32`) |
| `0x2` | Fuktighet | `humidity` | `%` | `0x3` (`Fixed32`) |
| `0x3` | Lys | `light` | `lux` | `0x4` (`Raw32`) |
| `0x4` | pH | `ph` | `pH` | `0x3` (`Fixed32`) |
| `0x5` | EC | `ec` | `µS/cm` | `0x3` (`Fixed32`) |
| `0x6` | Vannstand | `water_level` | `cm` | `0x4` (`Raw32`) |
| `0x7` | Trykk | `pressure` | `hPa` | `0x3` (`Fixed32`) |
| `0xE` | Utvidelse | `custom` | `""` | `0x4` (`Raw32`) |
| `0xF` | System | `system` | `""` | `0xF` (`System`) |

## 4. Systemmeldinger

| TypeID | Navn | Nøkkel | Beskrivelse |
| :--- | :--- | :--- | :--- |
| `0xF0` | Heartbeat | `heartbeat` | Oppetid i sekunder (`uint32`) |
| `0xF4` | Discovery | `discovery` | Node-annonsering / kapabiliteter |
| `0xFC` | OTA Control | `ota_ctrl` | `SubID`: `0x01` = Init (størrelse), `0x03` = Verify (CRC32) |
| `0xFD` | OTA Data | `ota_data` | `SubID` = sekvens/chunk, 4 byte firmware-data |
| `0xFF` | Reset | `reset` | Tilbakestilling av node |

## 5. Utvidelser og bruk over nettverk/MQTT

- **Egne utvidelser:** Bruk kategori `0xE` (`custom`) eller bygg domenespesifikke tabeller på toppen av dekoderen uten å endre selve 8-byte-rammen eller CRC8-valideringen.
- **MQTT og logging:** Selve 8-byte-rammen kan sendes uendret eller pakkes inn i en konvolutt med `timestamp`, `actor`/`source`, `correlation_id` og `status` på høyere lag.
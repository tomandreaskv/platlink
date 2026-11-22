# Platlink Protocol Specification (`v1.1.0`)

## 1. Frame Format (Fixed 8 Bytes)

All Platlink frames are strictly **8 bytes** long across all transports (I2C, UART, RS-485, ESP-NOW, radio links, and local log buffers).

| Byte | Field | Description |
| :--- | :--- | :--- |
| `0` | `Header` | Start-of-frame marker: `0xA5` |
| `1` | `TypeID` | Upper nibble (`bits 7..4`): **Format** (`0x1`–`0x5`, `0xF`)<br>Lower nibble (`bits 3..0`): **Category** (`0x1`–`0xF`) |
| `2` | `SubID` | Channel/endpoint (`0x00`–`0xDF`) or control/status subcommand (`0xE0`–`0xFF`) |
| `3..6` | `Payload` | 4-byte big-endian payload (`P0` = MSB, `P3` = LSB) |
| `7` | `CRC8` | `CRC-8/SMBUS` (`poly = 0x07`, `init = 0x00`, `xor_out = 0x00`) over bytes `0..6` |

### Strict Padding & Validation Rules
* **Single-byte values (`Digital`, `Analog8`):** Must be placed in `P3` (`byte 6`). Unused upper bytes `P0..P2` (`bytes 3..5`) **must** be `0x00`. Receivers **must** discard frames where `P0..P2 != 0x00`.
* **Transport bundling:** When sending over packetized links (e.g., radio to a ground station or MQTT), multiple complete 8-byte frames may be concatenated back-to-back inside a single transport packet without modifying the 8-byte frame structure.

---

## 2. Formats (`TypeID` Upper Nibble: `bits 7..4`)

| Code | Name | Key | Payload Type | Scale | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x1` | `Digital` | `digital` | `bool` | `1` | `0x00` = false/OFF, `0x01` = true/ON in `P3` (`P0..P2 = 0x00`) |
| `0x2` | `Analog8` | `analog8` | `uint8` | `1` | Unsigned 8-bit (`0..255`) in `P3` (`P0..P2 = 0x00`) |
| `0x3` | `Fixed32` | `fixed32` | `int32` | `100` | Signed 32-bit two's complement divided by `100` |
| `0x4` | `Raw32` | `raw32` | `uint32` | `1` | Unsigned 32-bit integer (counters, flags, raw values) |
| `0x5` | `Int32` | `int32` | `int32` | `1` | Signed 32-bit integer (e.g., coordinates `deg * 10^7`, signed offsets) |
| `0xF` | `System` | `system` | `uint32` / structured | `1` | System, timebase, discovery, and OTA messages (`0xF0`–`0xFF`) |

---

## 3. Categories (`TypeID` Lower Nibble: `bits 3..0`)

| Code | Name | Key | Default Unit | Default Format | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x1` | `Temperature` | `temp` | `°C` | `Fixed32` (`0x3`) | Air, water, or soil temperature |
| `0x2` | `Humidity` | `humidity` | `%` | `Fixed32` (`0x3`) | Relative humidity or moisture percentage |
| `0x3` | `Light` | `light` | `lux` | `Raw32` (`0x4`) | Illuminance / PAR |
| `0x4` | `pH` | `ph` | `pH` | `Fixed32` (`0x3`) | Acidity / alkalinity |
| `0x5` | `EC` | `ec` | `µS/cm` | `Fixed32` (`0x3`) | Electrical conductivity |
| `0x6` | `WaterLevel` | `water_level` | `cm` | `Raw32` (`0x4`) | Water level or sonar depth (`Fixed32` for 0.01 m precision) |
| `0x7` | `Pressure` | `pressure` | `hPa` | `Fixed32` (`0x3`) | Barometric or fluid pressure |
| `0x8` | `Actuator` | `actuator` | — | `Digital` (`0x1`) | Relays, grow lights, pumps, valves (requires echo/ACK + fail-safe) |
| `0x9` | `Position` | `position` | `deg * 1e7` | `Int32` (`0x5`) | `SubID 0x00` = Latitude, `0x01` = Longitude, `0x02` = Heading (`Fixed32`) |
| `0xE` | `Custom` | `custom` | — | `Raw32` (`0x4`) | Application-specific extensions |
| `0xF` | `System` | `system` | — | `System` (`0xF`) | Reserved for `0xF0`–`0xFF` system frames |

---

## 4. SubID Allocation Rules (`byte 2`)

* **`0x00`–`0xDF` (Channel / Endpoint):** Identifies the sensor or actuator index on a node (e.g., `0x00` = primary channel, `0x01` = secondary channel). For `Position` (`0x9`), `0x00` = Latitude, `0x01` = Longitude, `0x02` = Heading, `0x03` = Speed.
* **`0xE0`–`0xFF` (Control / Status):** Reserved for protocol subcommands, acknowledgements, and error codes.

---

## 5. Actuator Safety & Fail-Safe Rules (`Category 0x8`)

Because an 8-byte frame with `CRC-8` has a `1/256` random collision probability under line noise, actuators require two mandatory safeguards:

1. **Mandatory Echo / ACK:** Upon executing a valid actuator frame, the node echoes the exact 8-byte frame back to the controller.
2. **Hardware Fail-Safe Timeout:** Nodes must boot with all actuators **OFF**.
   * Simple toggle (`Format 0x1` Digital): `P3 = 0x00` (OFF) or `0x01` (ON), `P0..P2 = 0x00`.
   * Timed actuation (`Format 0x4` Raw32): `P0` = state (`0x00` OFF, `0x01` ON), `P1..P2` = fail-safe timeout in seconds (`uint16` big-endian), `P3` = channel index. If no refresh arrives before the timeout expires, the node forces the output **OFF**.

---

## 6. System, Timebase & Discovery Messages (`Format 0xF`)

| `TypeID` | Name | `SubID` | Payload (`P0..P3`) | Description |
| :--- | :--- | :--- | :--- | :--- |
| `0xF0` | `Heartbeat` | Node/Channel ID | `uint32` uptime in seconds | Periodic liveness signal |
| `0xF1` | `Timebase` | `0x00` = Unix epoch (s)<br>`0x01` = Mission/boot ms | `uint32` timestamp | Establishes shared time reference for buffered/backfilled logs and telemetry |
| `0xF2` | `TimeOffset` | `0x00` = Delta ms | `uint32` ms since last `Timebase` | Precedes buffered frames during log backfill |
| `0xF4` | `Discovery` | `0x00` = Request<br>`0x01` = Response | `P0..P1`: NodeID (`0xFFFF` = broadcast)<br>`P2`: Protocol version (`0x11` = v1.1)<br>`P3`: Profile ID | Plug-and-play node identification |
| `0xFC` | `OTA_Ctrl` | `0x01` = Init<br>`0x02` = ExpectedCRC<br>`0x03` = Verify | `Init`: `P0..P1` size (`uint16`), `P2..P3` NodeID<br>`ExpectedCRC`/`Verify`: `uint32` CRC32 | Optional OTA transfer profile control |
| `0xFD` | `OTA_Data` | Chunk index (`0..63`) | 4 bytes firmware data | Up to 64 chunks (256 bytes) per flash page block |
| `0xFE` | `OTA_Status` | `0x20` = ACK<br>`0x21` = Ready<br>`0xE1` = NAK Offset<br>`0xE2` = CRC Error | `ACK`/`NAK`: `P0..P1` next/expected offset (`uint16`), `P2..P3 = 0x0000`<br>`CRC Error`: computed `uint32` CRC32 | Block-level OTA acknowledgement |
| `0xFF` | `Reset` | `0x00` = SafeStop<br>`0x01` = Reboot | `P0..P1`: Target NodeID (`0xFFFF` allowed for SafeStop only)<br>`P2..P3`: `0x5A 0xA5` magic key | Requires NodeID + magic key `0x5AA5` |

### Standard Node Profiles (`Discovery` `P3`)
* `0x01`: **Climate & Light** (Air temp, air humidity, lux, grow-light relay)
* `0x02`: **Soil & Water Level** (Soil temp, soil moisture, water level)
* `0x03`: **Scale & Tank** (Load cell / weight)
* `0x04`: **Water Quality** (Water temp, pH, EC)
* `0x05`: **Dosing** (Peristaltic pumps)
* `0x06`: **Actuator Hub** (Relays, fans, circulation pumps)
* `0x07`: **USV / Bathymetry** (GNSS position, heading, sonar depth, battery)
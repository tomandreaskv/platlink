# PlatLink v1.1

PlatLink er en lettvekts, avhengighetsfri binærprotokoll for mikrokontrollere, sensornoder og bussenheter – designet med fast 8-byte ramme, CRC-8-sjekksum og ingen dynamisk minneallokering (`malloc`) på målenheten.

## Rammeformat (8 byte)

Alle pakker på bussen sendes i big-endian (nettverksrekkefølge):

| Byte | Felt | Beskrivelse |
| :--- | :--- | :--- |
| `0` | `Header` | Fast startbyte `0xA5` for rammesynkronisering |
| `1` | `TypeID` | `(format << 4) | category` (høy nibble = format, lav nibble = kategori) |
| `2` | `SubID` | Kanal/endpoint, sekvensnummer eller system-underkommando |
| `3..6` | `Payload` | 4-byte nyttelast i big-endian |
| `7` | `CRC8` | CRC-8 over byte `0..6` med polynom `0x07` og init `0x00` (`xor_out = 0x00`) |

### Formater (`TypeID` bits `7..4`)
- `0x1` (`Digital`): Boolsk verdi i `payload[3]` (`0` = `false`, `1` = `true`). `payload[0..2]` må være `0x00`.
- `0x2` (`Analog8`): Usignert 8-bit verdi (`0..255`) i `payload[3]`. `payload[0..2]` må være `0x00`.
- `0x3` (`Fixed32`): Signert 32-bit heltall (`int32`) skalert med `100` (`verdi / 100`).
- `0x4` (`Raw32`): Usignert 32-bit heltall (`uint32`).
- `0x5` (`Int32`): Signert 32-bit heltall (`int32`) uten skalering (f.eks. posisjon i `deg * 1e7`).
- `0xF` (`System`): System-, tidsbase-, discovery- og OTA-meldinger (`0xF0..0xFF`).

### Kategorier (`TypeID` bits `3..0`)
- `0x1`: Temperatur (`°C`)
- `0x2`: Fuktighet (`%`)
- `0x3`: Lys (`lux`)
- `0x4`: pH
- `0x5`: Elektrisk konduktivitet (`µS/cm`)
- `0x6`: Vannstand / dybde (`cm`)
- `0x7`: Trykk (`hPa`)
- `0x8`: Aktuator / relé
- `0x9`: Posisjon (`deg * 1e7` som `int32`)
- `0xE`: Egendefinerte utvidelser (`Custom`)
- `0xF`: Systemkategori (`System`)

### Systemmeldinger og OTA
- `0xF0`: `SYS_HEARTBEAT` (oppetid i sekunder, `uint32`)
- `0xF1`: `SYS_TIMEBASE` (felles tidsreferanse)
- `0xF2`: `SYS_TIME_OFFSET` (relativt offset i ms)
- `0xF4`: `SYS_DISCOVERY` (node-annonsering / kapabiliteter)
- `0xFC`: `SYS_OTA_CTRL` (`SubID`: `0x01` init, `0x02` expected CRC32, `0x03` verify)
- `0xFD`: `SYS_OTA_DATA` (`SubID` = chunk/sekvens, 4B data)
- `0xFE`: `SYS_OTA_STATUS` (OTA-status / ACK / NAK)
- `0xFF`: `SYS_RESET` (tilbakestilling / safe stop)

---

## Felles testvektorer og kodegenerering

`protocol_v1.json` er felles sannhetskilde for testvektorer på tvers av språkene. Oppdater genererte testvektorer ved behov:

```powershell
python scripts/gen_test_vectors.py
```

---

## Kjøre tester

### Python
```powershell
cd python
python -m unittest discover -s tests -v
```

### Go
```powershell
cd go
go test ./...
```

### C (med `clang`)
```powershell
cd c
clang -std=c11 -Wall -Wextra -Werror -I. platlink.c test_c.c -o test_c.exe
.\test_c.exe
```
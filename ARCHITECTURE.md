# PlatLink Architecture

Dette dokumentet beskriver de arkitektoniske valgene, systemflyten og ansvarsgrensene rundt **PlatLink v1.0**. Mens `SPEC.md` og `protocol_v1.json` definerer selve 8-byte-protokollen og testvektorene, forklarer `ARCHITECTURE.md` *hvordan* og *hvorfor* delene er bygget opp som en helhet.

## 1. Overordnet systemflyt

PlatLink er designet som et kompakt transport- og kjerneprotokoll-lag i den nedre delen av systemet, der ressursene er knappest og forutsigbarhet betyr mest:

```text
+------------------+     8B ramme      +-----------------+    JSON/MQTT/DB   +------------------+
|  Felt-/Sensornode| -----------------> |  Gateway / Hub  | -----------------> |  Backend / UI /  |
| (f.eks. CH32V003)|  (UART / I2C /   | (f.eks. ESP32 / |  (tidsstempel,    |  Lagring / Regler|
|  2 KB RAM, no FPU|   buss / seriell) |  Pi / tjeneste) |   aktor, sporbar) |                  |
+------------------+ -----------------> +-----------------+ <----------------- +------------------+
                            kommando/
                            OTA/kontroll
```

1. **Felt-/sensornode:** Samler inn målinger eller utfører enkle kommandoer med minimal kode, statisk/stack-basert minnebruk og heltalls-/fastpunktmatematikk (`Fixed32`, `Raw32`, `Int32`, `Analog8`, `Digital`).
2. **Gateway / hub:** Leser 8-byte-rammer (synkroniserer på `0xA5`, validerer `CRC8`), dekoder verdiene og kobler dem til enhet, kanal og eventuelt høyere lag (som MQTT, logging, tidsstempel og sikkerhetskontroller).
3. **Backend / tjenester:** Mottar strukturerte hendelser og kommandoer, håndterer lagring, visualisering, regler, bruker-/kildeidentitet (`actor`) og sporbarhet (`correlation_id`).

## 2. Kjernearkitektoniske valg og begrunnelser

### Fast 8-byte ramme med `0xA5`-header og `CRC8` (polynom `0x07`)
- **Valg:** Hver pakke er fast 8 byte (`1B header` + `1B TypeID` + `1B SubID` + `4B payload` + `1B CRC8`), med `CRC8` over byte `0..6`.
- **Begrunnelse:** Fast pakkelengde eliminerer variabel bufferstørrelse og dynamisk allokering på små mikrokontrollere. `0xA5` (`1010 0101`) gir en tydelig synkroniseringsbyte i en strøm, og `CRC8` med polynom `0x07` gir god feildeteksjon på korte rammer til en svært lav kostnad i flash og CPU-sykler.
- **Avveining:** Maks 4 byte nyttelast per pakke krever oppdeling over flere pakker (som i OTA) eller bruk av et ytre lag når mer data eller metadata trengs.

### `TypeID` delt i høy nibble (format) og lav nibble (kategori)
- **Valg:** Byte 1 pakker formatet i de øvre 4 bitene (`(type_id >> 4) & 0x0F`) og kategorien i de nedre 4 bitene (`type_id & 0x0F`).
- **Begrunnelse:** Sparer plass i den trange 8-byte-rammen og gjør det mulig å tolke payload-typen generelt (`Fixed32`, `Raw32`, `Digital`, osv.) uavhengig av hvilken domenekategori som sendes.
- **Avveining:** Begrenset til 16 formater og 16 kategorier (`0x0..0xF`), noe som krever bevisst forvaltning av koder og tydelig skille mellom kjerne- og utvidelsesområder.

### `Fixed32` (skalert med `100`) i stedet for flyttall (`float`)
- **Valg:** Desimalverdier med to desimalers oppløsning kodes som signerte 32-bit heltall (`int32` i big-endian) som deles på `100` ved dekoding, i tillegg til `Int32`, `Raw32`, `Analog8` og `Digital`.
- **Begrunnelse:** Mikrokontrollere uten maskinvare-FPU slipper programvare-emulering av flyttall, samtidig som `Fixed32` støtter negative verdier (som minusgrader) og heltallssammenligninger rett på noden. Big-endian er valgt som en entydig nettverksstandard på tvers av språk og maskinvare.
- **Avveining:** Fast skalering på `100` låser presisjonen til to desimaler for `Fixed32`; høyere presisjon eller annet verdiområde må håndteres med annet format/skala eller i høyere lag.

### Rollefordeling for `SubID` og systemmeldinger (`0xF0..0xFF`)
- **Valg:** `SubID` identifiserer kanal/endpoint på en node ved normale målinger/styring, og underkommando/sekvens ved systemmeldinger (`0xF0..0xFF`).
- **Begrunnelse:** Lar én node ha flere kanaler (f.eks. flere sensorer av samme kategori) uten å opprette egne kategorier per kanal, og holder kontroll-/driftsmeldinger (`Heartbeat`, `Discovery`, `OTA Control`, `OTA Data`, `Reset`) atskilt fra sensordata.
- **Avveining:** Et 8-bit `SubID`-sekvensnummer går rundt etter 256 pakker. Ved større OTA-overføringer eller støyutsatte linjer må høyere lag håndtere sekvens-/blokksporing og kvittering (`ACK`/`NAK`).

### Frittstående kjerne uten eksterne avhengigheter
- **Valg:** PlatLink organiseres som et eget prosjekt med `protocol_v1.json` og `SPEC.md` som felles fasit, og med implementasjoner i C, Go og Python som kun bruker standardbiblioteket i hvert språk.
- **Begrunnelse:** Skiller gjenbrukbar protokollkode fra domenespesifikk forretningslogikk, unngår avhengighetsråte og gjør det enkelt å verifisere at alle språk oppfører seg likt mot de samme testvektorene.
- **Avveining:** Endringer i protokollen må holdes i synk på tvers av spesifikasjon, testvektorer og språkimplementasjoner.

## 3. Hva som ligger utenfor selve 8-byte-pakken

En rå 8-byte PlatLink-pakke er bevisst minimal. Flere viktige systemegenskaper hører derfor hjemme i laget *rundt* pakken (i gateway, MQTT-bro eller applikasjonslag):

- **Tidsstempel og rekkefølge:** Selve pakken har ikke klokke eller absolutt tidsstempel; gateway eller mottaker bør stemple innkommende pakker ved mottak eller knytte dem til synkronisert tid.
- **Sporbarhet og kilde/aktor (`actor`, `correlation_id`):** Ved kommandoer og hendelser over nettverk/MQTT bør konvolutten kunne bære hvem eller hvilken tjeneste som utløste en handling (`actor`), en sporings-/korrelasjons-ID (`correlation_id`) og status, slik at man kan følge en kommando fra bruker/regel helt til enhet og tilbake.
- **Sikkerhet og feilhåndtering for aktuatorer:** For styring av utstyr som pumper, ventiler eller dosering bør høyere lag håndheve sikkerhetsmekanismer som maksimal kjøretid (*timeout*), tilstandssjekk, kvittering og trygg retur til «av» ved kommunikasjonsbrudd eller feil.
- **Domeneregler og utvidelser:** Kategori `0xE` (`Utvidelse` / `custom`) og domenespesifikke oppslagstabeller lar prosjekter utvide funksjonalitet uten å endre kjerneprotokollen.

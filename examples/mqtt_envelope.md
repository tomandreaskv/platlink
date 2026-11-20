# MQTT-konvolutt rundt PlatLink v1.0

Selve **PlatLink v1.0-pakken** er bevisst en fast **8-byte binærpakke** uten tidsstempel, bruker-/kildeidentitet eller sporings-ID. Når pakker eller kommandoer sendes over nettverk/MQTT eller logges i gateway/backend, bør de pakkes inn i en konvolutt i laget *rundt* selve pakken.

## Når bør konvolutt brukes?

- **Telemetri fra gateway til MQTT/backend:** Når mottakeren vil knytte et nøyaktig `timestamp` (og eventuelt rå `raw_hex`) til den dekodede verdien og enheten/kanalen.
- **Kommandoer og aktuatorstyring:** Når en tjeneste, regel eller bruker sender en kommando (f.eks. relé, pumpe eller dosering) og man trenger sporbarhet (`correlation_id`), hvem/hva som utløste handlingen (`actor`), status og trygge grenser/timeouts i applikasjonslaget.

## Eksempel på JSON-konvolutt (telemetri)

```json
{
  "protocol": "platlink",
  "version": "1.0.0",
  "timestamp": "2026-11-20T16:00:00Z",
  "gateway_id": "gw-honefoss-01",
  "node_id": "node-ch32-02",
  "correlation_id": "evt-9f8e7d6c-5b4a-4321-8765-fedcba098765",
  "packet": {
    "raw_hex": "A53101000008CDAA",
    "type_id": "0x31",
    "format": {
      "code": "0x3",
      "name": "Fixed32"
    },
    "category": {
      "code": "0x1",
      "name": "Temperatur",
      "key": "temp",
      "unit": "°C"
    },
    "sub_id": "0x01",
    "decoded_value": 22.53
  }
}
```

## Eksempel på JSON-konvolutt (kommando / aktuator)

```json
{
  "protocol": "platlink",
  "version": "1.0.0",
  "timestamp": "2026-11-20T16:05:00Z",
  "actor": "rule-engine:pH-control",
  "target_node": "node-ch32-04",
  "correlation_id": "cmd-123e4567-e89b-12d3-a456-426614174000",
  "status": "dispatched",
  "safety": {
    "max_duration_ms": 5000,
    "requires_ack": true
  },
  "packet": {
    "raw_hex": "A51F050000000165",
    "type_id": "0x1F",
    "sub_id": "0x05",
    "decoded_value": true
  }
}
```

## Tommelfingerregler

- **Hold 8-byte-kjernen urørt:** Ikke legg til ekstra bytes i selve trådprotokollen når du trenger metadata – bruk konvolutten i gateway/broker/backend.
- **Bruk `correlation_id` og `actor` konsekvent:** Gjør det enkelt å spore en kommando fra bruker eller regelmotor, gjennom gateway og node, og tilbake til statushendelse/logg.
- **Håndhev aktuatorsikkerhet i høyere lag:** Tidsavbrudd (*timeout*), tilstandskontroll og kvitteringer bør ligge i styringslaget rundt pakken.
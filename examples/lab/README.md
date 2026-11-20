# PlatLink v1.0 Lab – Bruksanvisning

Denne veiledningen viser hvordan du starter og bruker den lokale PlatLink v1.0-laben med ekte TCP-buss, simulerte noder, interaktiv CLI og valgfritt lokalt web-GUI som bruker `platlink`-biblioteket direkte.

## Filer i `examples/lab/`

- `bus_broker.py` – Lokal TCP-buss (`127.0.0.1:4571` som standard) som videresender rå byte mellom tilkoblede klienter.
- `sim_nodes.py` – Simulerte noder som sender telemetri (`Fixed32`, `Raw32`, `Heartbeat`) og lytter på relé- og reset-kommandoer, samt en støynode som sender linjestøy og ugyldig CRC-8.
- `interactive_cli.py` – Interaktiv kommandolinjeklient for å lytte på trafikken i sanntid og sende kommandoer, OTA-sekvenser, rå hex og feilpakker.
- `web_lab.py` – Lokal HTTP-server med et enkelt web-GUI i nettleseren koblet direkte mot TCP-bussen.
- `start_lab.py` – Samlet oppstartsskript som starter buss, noder, eventuelt web-GUI og CLI med ryddig opprydding ved avslutning.

## Rask oppstart (anbefalt)

Kjør dette fra rotmappen til repoet (`platlink/`) i PowerShell eller terminal:

```powershell
python examples/lab/start_lab.py --web
```

Dette gjør følgende:

1. Starter TCP-bussen på `127.0.0.1:4571`.
2. Venter til porten er klar og starter de simulerte nodene (`sensor-1`, `relay-1` og `noise-1`).
3. Starter web-GUI-et på `http://127.0.0.1:8080/` og åpner nettleseren.
4. Starter den interaktive CLI-en i terminalen.

For å kjøre uten web-GUI:

```powershell
python examples/lab/start_lab.py
```

For å justere telemetri-intervall eller slå av støynoden:

```powershell
python examples/lab/start_lab.py --web --interval 1.0 --no-noise
```

## Manuell oppstart i separate terminaler

Hvis du vil se hver prosess i sitt eget terminalvindu:

1. Terminal 1 (TCP-buss):
   ```powershell
   python examples/lab/bus_broker.py
   ```
2. Terminal 2 (Simulerte noder):
   ```powershell
   python examples/lab/sim_nodes.py
   ```
3. Terminal 3 (Interaktiv CLI eller Web-GUI):
   ```powershell
   python examples/lab/interactive_cli.py
   ```
   eller:
   ```powershell
   python examples/lab/web_lab.py
   ```

## Kommandoer i `interactive_cli.py`

Når CLI-en er tilkoblet, kan du skrive `help` eller bruke følgende kommandoer:

- `relay 1 on` / `relay 1 off` – Sender `Digital`-kommando (`0x1F`) til relékanal.
- `reset` – Sender `System Reset` (`0xFF`).
- `temp 23.45 01` – Sender temperatur som `Fixed32` (`0x31`) med `SubID 0x01`.
- `ota 22` – Kjører en simulert OTA-overføring (`0xFC` Init, `0xFD` Data-chunks, `0xFC` Verify).
- `hex A53101000008CDAA` – Sender en nøyaktig 8-byte heksadesimal ramme.
- `badcrc` – Sender en pakke med bevisst feil CRC-8 for å teste feilhåndtering.
- `noise` – Sender tilfeldige byte uten `0xA5` for å teste rammesynkronisering.
- `quiet` – Slår av eller på løpende innkommende loggmeldinger mens du skriver.
- `quit` – Avslutter CLI-en (og stopper bakgrunnsprosessene hvis startet via `start_lab.py`).

## Bruk av web-GUI-et (`web_lab.py`)

I nettleservinduet (`http://127.0.0.1:8080/`) kan du:

- Se tellere for gyldige pakker, sendte pakker (`TX`), CRC-feil og støybyte øverst.
- Skru relékanaler av og på eller sende `Reset (0xFF)`.
- Velge kategori, verdi og `SubID` og sende en `Fixed32`-måling.
- Trigge en OTA-sekvens, sende en pakke med feil CRC-8, sende linjestøy eller sende egendefinert 8-byte hex.
- Følge all trafikk fortløpende i tabellen for live buss-trafikk.

## Avslutning

Skriv `quit` i `interactive_cli.py` eller trykk `Ctrl+C`. `start_lab.py` avslutter da `web_lab`, `sim_nodes` og `bus_broker` automatisk.
<p align="center">
  <img src="custom_components/telenot/brand/icon.png" width="128" alt="Telenot integration" />
</p>

<h1 align="center">Telenot — Home-Assistant-Integration</h1>

<p align="center">
  Lokale Push-Integration für die Einbruchmeldezentrale <b>Telenot complex 400</b> über ihre
  <b>serielle GMS-Schnittstelle</b> — scharf, unscharf, Bereitschaft, Meldebereiche und Melder
  mit den Namen aus der Zentrale. Ohne MQTT, ohne zusätzlichen Container.
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

> **Stand: in Entwicklung, noch nicht veröffentlicht.** Protokoll, Verbindung, Einrichtung mit
> Suchlauf, Geräte, Entitäten und Optionen sind fertig und gegen Telegramme einer echten Zentrale
> sowie eine simulierte Zentrale über TCP getestet. Als Nächstes folgt der erste Test an einer
> echten Zentrale.

---

## Warum

Üblich ist ein eigener Bridge-Dienst, der das GMS-Protokoll der Zentrale nach MQTT übersetzt.
Das funktioniert, kostet aber einen Container, den Umweg über den Broker und von Hand
gepflegte Sensorlisten – und die Bridge, die diese Integration ablösen soll, verarbeitete nach
jedem Neuaufbau der Verbindung zum Wandler stillschweigend keine Daten mehr. Home Assistant
zeigte dann stundenlang einen veralteten Alarmzustand.

Diese Integration spricht direkt mit der Zentrale, verbindet sich unbegrenzt neu, meldet alles
als **nicht verfügbar**, wenn die Zentrale nicht erreichbar ist, und liest **Namen und
Meldebereiche der Melder aus der Zentrale** – statt Adressen durch Ausprobieren zu suchen.

## Was du bekommst

| Gerät | Entitäten |
|---|---|
| **Telenot complex 400** | Alarmpanel (unscharf / intern scharf / extern scharf), intern bereit, extern bereit, Alarm, Störung, Störung Akku, Störung Netz, Knopf *Alarm zurücksetzen*, Verbindung (Diagnose) |
| **Je Meldebereich ein Gerät**, benannt wie in der Zentrale, verbunden mit der Zentrale | *Zustand* des Bereichs, jeder Melder des Bereichs (standardmäßig deaktiviert), *gesperrt* (Diagnose) |


Befehle gehen nur im Sendefenster der Zentrale raus und werden von ihr quittiert; der
Alarmzustand kommt immer von der Zentrale, nie aus dem gesendeten Befehl. Nur Sicherungsbereich 1.

## Hardware

```
complex 400 ──RS232 (GMS)──▶ Seriell-Ethernet-Wandler ──LAN──▶ Home Assistant
```

| Teil | Hinweise |
|---|---|
| **Telenot complex 400 / 400H** | Das **GMS-Protokoll muss auf der seriellen Schnittstelle freigeschaltet sein**. Das macht der Errichter in *compasX*; ab Werk ist es aus. |
| **Seriell-Ethernet-Wandler** | Jeder transparente RS232-zu-TCP-Server, z. B. die Reihe **USR-TCP232**. Betriebsart *TCP-Server*, **9600 Baud, 8N1**, kein eigenes Protokoll. Host und Port notieren. |
| **Serielles Kabel** | **1:1 (Straight-through)**, kein Nullmodem – TX/RX werden nicht gekreuzt. Genutzt werden nur TXD, RXD und Masse. |
| Netzwerk | Home Assistant muss den TCP-Port des Wandlers erreichen. |

Gut zu wissen:

- **Nur ein Partner gleichzeitig.** Der Wandler bedient genau eine TCP-Verbindung. Eine andere
  Bridge (z. B. ein telenot-bridge-Container) muss vor der Einrichtung gestoppt sein.
- **Wandler außerhalb des Zentralengehäuses** und die serielle Leitung kurz halten. GMS-Befehle
  sind **am Draht nicht abgesichert**: Wer an das Kabel oder den TCP-Port des Wandlers kommt,
  kann die Anlage unscharf schalten. Zugriff auf den Wandler im Netz einschränken.
- Solange niemand die Telegramme der Zentrale quittiert (Home Assistant startet neu, Wandler
  weg), kann die Zentrale eine Störung des Übertragungswegs vermerken. Das ist eine
  Störungsmeldung, kein Alarm.
- **Nach einem Neustart oder Verbindungsabbruch** liest die Integration den vollständigen
  Zustand der Zentrale neu, bevor die Entitäten verfügbar werden – Änderungen am Bedienteil
  in der Zwischenzeit kommen also an.
- Das Öffnen des Zentralengehäuses löst den Sabotagekontakt aus – an der Verkabelung nur bei
  unscharfer Anlage und ggf. im Errichtermodus arbeiten.

## Voraussetzungen

- Home Assistant **2026.8** oder neuer (geplantes Minimum).
- Keine Python-Abhängigkeiten; die Integration nutzt nur asyncio-TCP.

## Installation

Noch nicht veröffentlicht. Danach: HACS → benutzerdefiniertes Repository `benji2k2/ha_telenot`
(Kategorie *Integration*) → installieren → Home Assistant neu starten.

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Telenot*.
2. Host und Port des Wandlers eintragen. Die Integration prüft, ob die Zentrale spricht.
3. **Suchlauf**: Die Integration fragt die Zentrale, welche Adressen belegt sind, und liest
   Namen und Meldebereiche (rund 6 Sekunden je Adresse, ~9 Minuten bei 80 Adressen). Dabei
   werden nur Leseabfragen gesendet.
4. Bestätigen. Geräte und Entitäten entstehen; den Meldebereich-Geräten Räume zuordnen.

### Optionen

- **Alarmcode** – leer lassen, um nie nach einem Code zu fragen. Ist ein Code gesetzt, zeigt die
  Alarm-Karte ein Ziffernfeld.
- **Code verlangen für** – beliebig aus *extern scharf*, *intern scharf*, *unscharf*.
  Standard: nur *extern scharf*. Nicht ausgewählte Aktionen gehen ohne Code. Ein falscher Code
  erreicht die Zentrale nie.
- **Zentrale erneut auslesen** – nachdem der Errichter umprogrammiert hat. Nutzt die laufende
  Verbindung.

Melder bekommen eine Geräteklasse, die aus ihrem Namen abgeleitet wird (Fenster, Bewegung,
Sabotage …); passt sie nicht, in den Entitätseinstellungen unter *Anzeigen als* ändern.

## Umstieg von telenot-bridge (MQTT)

1. Bridge-Container stoppen.
2. Die gespeicherten MQTT-Discovery-Topics der Bridge (`homeassistant/binary_sensor/telenot/…`)
   und das MQTT-Alarmpanel entfernen.
3. Integration einrichten und die neuen Entitäten auf die alten Entitäts-IDs umbenennen –
   Automationen, Dashboards, HomeKit und Verlauf laufen weiter.

## Entwicklung

```bash
python -m pytest -q
```

Der Protokollkern (`protocol.py`, `state.py`) hängt nicht von Home Assistant ab und wird gegen
Referenztelegramme getestet; die Verbindung gegen eine simulierte Zentrale über TCP.

`tools/probe.py` spricht mit dem Client der Integration, aber ohne Home Assistant, mit einer
echten Zentrale: beobachten und jede Änderung ausgeben (nur lesend), eine Neustart-Lücke
nachstellen (`--gap 120`), Suchlauf (`--scan out.json`) oder genau einen Befehl senden
(`--command … --confirm`). Details im Docstring.

## Icons

Icons und Logos liegen in `custom_components/telenot/brand/`. Home Assistant nutzt sie von dort
ab **2026.3**. Sie sind aus dem TELENOT-Logo erstellt, wie es auf
[telenot.com](https://www.telenot.com/) verwendet wird.

## Dank

Das Wissen über das GMS-Protokoll stammt aus [carhensi/telenot-bridge](https://github.com/carhensi/telenot-bridge)
und [carhensi/telenot-esp-bridge](https://github.com/carhensi/telenot-esp-bridge) (beide Apache-2.0),
siehe [NOTICE](NOTICE). Kein Angebot der TELENOT ELECTRONIC GmbH und nicht von ihr unterstützt;
*Telenot*, *complex*, *compasX* und das TELENOT-Logo sind deren Marken und werden nur zur Kennzeichnung
der unterstützten Hardware verwendet.

## Lizenz

[Apache-2.0](LICENSE)

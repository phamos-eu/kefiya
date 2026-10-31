# Arbeitsregeln für diese App

kefiya redet mit echten Banken und bewegt echtes Geld. Ein Fehler hier ist
kein roter Test, sondern eine Überweisung, die die Bank angenommen hat und
die niemand mehr zurückholt. Die Regeln unten sind aus Fehlern entstanden,
die genau das gekostet haben.

## Niemals Daten des Betreibers im Quelltext

Das ist die erste Regel, weil sie keine Ausnahme hat.

**Nicht in den Quelltext, nicht in Tests, nicht in Kommentare, nicht in
Commit-Nachrichten, nicht in Dokumentation:** IBANs, Kontonummern,
Bankleitzahlen, Kennungen von Bankzugängen, Namen von Personen oder
Gesellschaften, Namen von Bankkonten, Auftrags- oder Belegnummern,
Mandantennamen, Salden, Buchungsvolumen, Mailadressen, Hostnamen der
Instanz. Auch nicht Namen von Gegenseiten — das sind Daten Dritter.

Am 09.10.2026 stand genau das in 33 Dateien dieses öffentlich einsehbaren
Repositories, darunter echte IBANs und die Kennung eines
Online-Banking-Zugangs neben seiner Bankleitzahl. Niemand hatte es
böswillig hineingeschrieben: es ist der Hausstil. Die Messung wandert in
den Docstring, damit nachlesbar bleibt, woran eine Regel gemessen wurde —
und nimmt den Namen mit. Deshalb reicht ein Vorsatz nicht.

**Die Messung bleibt, die Identität geht.** Aus

> Gemessen am 30.09.2026, *\<Gesellschaft\>* Volksbank

wird

> Gemessen am 30.09.2026 an einem Volksbank-Konto

Das Datum und die Zahlen sind der Wert dieser Kommentare, der Name ist es
nicht. Für Beispieldaten gilt: erfundene IBANs mit gültiger Prüfsumme
(Bankleitzahlen 99999999, 88888888 und 77777777 sind in Deutschland nicht
vergeben), erfundene Namen, `example.com` als Domain.

`test_no_tenant_data.py` prüft das bei jedem Lauf — über Hashes, damit der
Wächter nicht selbst die Offenlegung ist. **Diesen Test nicht aufweichen:**
er ist die Zusage, dass ein Beitrag an das Original (`phamos-eu/kefiya`)
nichts aus diesem Fork mitträgt. Wer einen neuen Namen neutralisiert,
trägt ihn dort als Hash nach, damit er nicht zurückkehren kann.

Was nur die Instanz betrifft — Patches für Server Scripts, Skripte von
Custom HTML Blocks, Einrichtungsnotizen — gehört gar nicht ins Repository,
auch nicht unter `docs/`.

## Vor jedem Push

```
python3 -m flake8 kefiya/                      # oder: python3 -m pyflakes kefiya/
python3 -m unittest discover -s kefiya/kefiya/tests -t . -p "test_*.py"
```

Die Testsuite meldet Module, die eine Bench brauchen, als
`ModuleNotFoundError: No module named 'frappe'`. Das sind **keine**
Fehlschläge — sie laufen nur auf der Instanz. Alles andere schon.

## Ein Test, der Quelltext liest, lädt keinen Quelltext

Viele Tests hier prüfen den Quelltext als Text: sie suchen eine Zeile, eine
Reihenfolge, eine Meldung. Das ist Absicht — so lässt sich ohne Bench prüfen,
dass die TAN erst geparkt und dann erfragt wird. Aber es hat eine Grenze, und
die hat einmal eine Überweisung gekostet:

> Beim Herausziehen der TAN-Strecke nach `fints_tan_session.py` wanderten vier
> Namen mit und ihre Importe nicht. Die Datei ließ sich parsen — Python löst
> Namen auf, wenn die Zeile läuft, nicht wenn das Modul lädt. Die Testsuite
> war grün, das Release baute, der Deploy ging raus, und der erste Druck auf
> „Senden" endete mit `NameError: name 'NeedTANResponse' is not defined` —
> **nachdem** die Bank nach einer TAN gefragt worden war.

Daraus folgen drei Regeln:

1. **Nach jedem Verschieben von Code zwischen Dateien: `pyflakes` laufen
   lassen.** Nicht „sieht richtig aus". Der Umzug ist genau die Bewegung, bei
   der Importe zurückbleiben.
2. `test_every_python_file_compiles.py` prüft das inzwischen automatisch:
   jeder gelesene Name muss in seiner Datei gebunden, importiert oder ein
   Builtin sein. **Diesen Test nicht aufweichen.**
3. Ein grüner Quelltext-Test heißt „die Zeile steht da", nicht „der Code
   läuft". Wo es auf das Laufen ankommt, gehört eine Prüfung dazu, die den
   Code tatsächlich ausführt oder wenigstens seine Namen auflöst.

## Zirkuläre Importe

`fints_controller` importiert `fints_tan_session`. Was **beide** brauchen,
gehört in ein drittes Modul ohne eigene Importe — so wie
`fints_errors.py` für `InitFailedException` und `TanInteractionRequired`.
Der Controller reicht solche Namen weiter, damit bestehende Aufrufer
(`from kefiya.utils.fints_controller import TanInteractionRequired` in
`client.py`) gültig bleiben.

## Die Bank ist der Schiedsrichter, nicht das System

Buchungen des Systems gegen Buchungen des Systems zu prüfen ist zirkulär —
kefiya holt beide Seiten selbst. Wo es um Vollständigkeit oder Doppelungen
geht, entscheidet der Kontoauszug der Bank (MT940/`.sta`) oder der Saldo aus
HKSAL. Beim Zählen aus MT940 gilt: **`entry_date` (Buchungstag), nicht
`date` (Valuta)** — kefiya speichert den Buchungstag, und die beiden weichen
regelmäßig um Tage ab.

## Nichts löschen, nichts senden ohne Zustimmung

Löschungen von Buchungen und das Absenden von Aufträgen brauchen die
ausdrückliche Zustimmung des Nutzers. Ein Auftrag braucht seine TAN von ihm,
nicht von einer Automatik.

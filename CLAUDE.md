# Arbeitsregeln für diese App

kefiya redet mit echten Banken und bewegt echtes Geld. Ein Fehler hier ist
kein roter Test, sondern eine Überweisung, die die Bank angenommen hat und
die niemand mehr zurückholt. Die Regeln unten sind aus Fehlern entstanden,
die genau das gekostet haben.

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

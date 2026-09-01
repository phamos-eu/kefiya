# Finanzübersicht: die Konten nach Kontoart gliedern

Die Seite `/app/finanzübersicht` kannte zwei Töpfe — Girokonten und
Darlehenskonten — und riet das „Darlehen" aus `Bank Account.account_type` und
einer Verknüpfung am Property Loan. Geschäftsanteile und Avale landeten
deshalb unter den Girokonten und zählten als Liquidität.

Diese beiden Dateien sind die Änderung. Sie liegen hier statt im Code, weil
die Seite ein **Server Script** (27 KB) und ein **Custom HTML Block** (82 KB)
ist und beide Felder über dem Schreiblimit der MCP-Schnittstelle liegen — sie
müssen im Browser eingesetzt werden.

Sie tragen die Endung `.md` und nicht `.py`/`.js`, und das ist keine Kosmetik:
Der Release-Check von Frappe Cloud kompiliert jede `.py`-Datei im Repository.
Ein Patch besteht aus Bruchstücken mitten aus einer Funktion, also
eingerücktem Code — als Modul gelesen ist das ein Syntaxfehler, und der hat
genau einmal ein Release blockiert.

## Reihenfolge

1. `1-server-script-fc_cockpit_data.md` — drei Stellen im Server Script
   `fc_cockpit_data`. Danach **im Browser öffnen und mit Strg+S speichern**,
   sonst bleibt die alte Fassung in der Redis-Whitelist stehen.
2. `2-block-axessio_finanzcockpit.md` — vier Stellen im Custom HTML Block
   `axessio_finanzcockpit`. Danach reicht ein harter Reload (Strg+F5).

Jeder Abschnitt nennt den Suchtext wörtlich; alle Ankertexte wurden gegen die
laufende Fassung geprüft.

## Was dabei herauskommt

Vier Abschnitte: **Zahlungskonten**, **Darlehen**, **Avale und Kreditlinien**,
**Geschäftsanteile**. Depots erscheinen nicht — sie stehen im WAL-Management,
und der Server nimmt sie aus der Antwort heraus, statt sie stillschweigend
unter die Zahlungskonten zu legen.

## Die Regel, und warum sie nicht „Kefiya zuerst" lautet

Naheliegend wäre: die Kontoart am Kefiya Login gewinnt immer, denn sie kommt
von der Bank. Ein Probelauf gegen die echten Daten hat das widerlegt.

`account_kind` hat den Vorgabewert `Current Account`, und `kind_of()` liefert
ihn auch für jeden Zugang, an dem nie jemand etwas eingestellt hat — von einem
echten Girokonto nicht zu unterscheiden. **Acht Darlehenskonten der Volksbank**
tragen genau das. Mit „Kefiya zuerst" wären sie unter die Zahlungskonten
gewandert und in die Liquidität eingegangen.

Deshalb zählt die Kontoart nur, wo sie **kein** Zahlungskonto sagt: das ist
eine Aussage. Alles andere entscheidet weiter der Kontotyp am Bankkonto.
Dieselbe Form wie in `duplicate_rule`: eine fehlende Antwort ist keine
Antwort. In der App steht sie als `ledger_rule.is_stated()` mit Test.

## Gegengeprüft

Probelauf über alle nicht deaktivierten Bankkonten:

| Abschnitt | Konten |
|---|---|
| Zahlungskonten | 405 |
| Darlehen | 80 |
| Avale und Kreditlinien | 12 |
| Geschäftsanteile | 3 |
| Depots | 0 |

Die Liquiditätssumme fällt um **genau 1.500,00 €** — die drei
Geschäftsanteile zu je 500 €, darunter DE52670923000833137310. Sonst bewegt
sich nichts: die Avale fielen schon vorher heraus, allerdings aus dem falschen
Grund (ihr Zwillingssatz hängt am Property Loan, nicht weil sie Avale sind).
Ein Aval ohne Zwilling zählte mit; jetzt nicht mehr.

Die Zahlen stammen aus allen Bankkonten. Die Seite filtert zusätzlich auf
Saldo ≠ 0 beziehungsweise Darlehenskonto und auf die Leserechte, ihre
Abschnitte sind also kleiner. Geprüft wurde die Einordnung, nicht die
Auswahl.

## Zwei Nebenwirkungen, die dazugehören

- **„Verfügbar"** heißt jetzt: Geld, mit dem gezahlt werden kann. Bei einem
  Geschäftsanteil und einem Aval steht dort 0,00 € statt des vollen Betrags.
  Das war Ihr Ausgangspunkt: das Guthaben ist nicht verfügbar.
- **Der Zuklapp-Zustand je Unternehmen** bekommt einen Schlüssel je Abschnitt.
  Ohne das hätte „Volksbank" zuklappen sie unter Darlehen, Avalen und
  Geschäftsanteilen gleichzeitig zugeklappt — der alte Schlüssel kannte nur
  Giro oder Darlehen.

# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Kontoauszug prueft sich selbst, dann prueft er die Buchhaltung.

Dieses Modul beantwortet drei Fragen, in dieser Reihenfolge, und jede nur,
wenn die vorige bestanden ist:

    1. Greifen die Auszugsblaetter lueckenlos ineinander?
       Jedes Blatt nennt Anfangs- (:60F:) und Schlusssaldo (:62F:). Bei einer
       vollstaendigen Folge ist der Anfangssaldo eines Blattes der
       Schlusssaldo des vorigen.

    2. Ergeben die Buchungen eines Blattes seine Saldodifferenz?
       Frage 1 zeigt nur, dass zwei Blaetter aneinanderpassen -- nicht, ob
       INNERHALB eines Blattes etwas fehlt. Dafuer muss die Summe der
       :61:-Betraege die Differenz zwischen Anfangs- und Schlusssaldo genau
       treffen.

    3. Erst dann: trifft die Buchhaltung den Kontoauszug?
       Zwischen zwei Saldenpunkten der Bank muss die Summe der Bewegungen im
       System exakt die Saldodifferenz ergeben.

Warum diese Reihenfolge und nicht gleich Frage 3: ein Vergleich taugt nur so
viel wie die Vergleichsgroesse. Ein Auszug, der selbst Luecken hat, wuerde
eine saubere Buchhaltung anklagen. Blaetter, die Frage 2 nicht bestehen,
gehoeren deshalb aus Frage 3 heraus -- dort schweigt der Auszug, also
schweigt auch das Urteil.

Warum ueberhaupt der Saldo und nicht die Anzahl der Buchungen: Zaehlen kann
taeuschen. Ein Auszug fasst vielleicht zusammen, was das System einzeln
fuehrt, oder die Bank meldet dieselbe Buchung zweimal, weil sie zweimal
stattgefunden hat. Der Kontostand kann das nicht -- er ist die eine Zahl,
die die Bank selbst nennt und die das System nirgends herleitet.

Das Modul kommt ohne frappe aus, damit es ohne Bench pruefbar ist. Die
Anbindung an die Instanz steht in kontenpruefung.py.
"""

import re
from collections import namedtuple
from decimal import Decimal

#: Ab wann zwei Betraege als verschieden gelten. Cent-genau; alles Feinere
#: waere Rundungsrauschen, alles Groebere wuerde Fehler durchlassen.
CENT = Decimal("0.005")

#: :60F: (Anfangssaldo) und :62F: (Schlusssaldo). Die M-Varianten (:60M:,
#: :62M:) sind Zwischensalden eines mehrteiligen Auszugs und markieren KEIN
#: Blattende -- sie werden bewusst nicht gelesen.
_SALDO = re.compile(r"^:6(?P<art>[02])F:(?P<vz>[CD])(?P<tag>\d{6})[A-Z]{3}(?P<wert>[\d,.]+)",
                    re.MULTILINE)

#: :61: Umsatz. Valuta (6), optional Buchungstag (4), Soll/Haben, optional
#: ein Waehrungsbuchstabe, dann der Betrag mit Komma als Dezimaltrennzeichen.
_UMSATZ = re.compile(
    r"^:61:(?P<valuta>\d{6})(?P<buchung>\d{4})?"
    r"(?P<vz>R?[CD])(?P<waehrung>[A-Z])?(?P<wert>[\d,]+)",
    re.MULTILINE)

#: Ein Kontoauszugsblatt: welches Konto, welcher Zeitraum, was drin steht.
Blatt = namedtuple("Blatt", "konto anfang_tag anfang ende_tag ende summe zeilen")

#: Ein Befund aus Frage 1 oder 2. `fehlt` ist der Betrag, um den es nicht
#: aufgeht -- bei Frage 1 die Sprungstelle, bei Frage 2 die Luecke im Blatt.
Bruch = namedtuple("Bruch", "konto tag erwartet gefunden fehlt")

#: Ein Befund aus Frage 3.
Abweichung = namedtuple("Abweichung", "konto von bis bank system fehlt")


#: Was die vier Kennzeichen in :61: bedeuten. Die beiden R-Varianten sind
#: Stornierungen und drehen das Vorzeichen um: RD storniert eine Sollbuchung,
#: ist also eine Gutschrift. Wer "endet auf D, also Abgang" rechnet, verbucht
#: jede Rueckbuchung mit dem falschen Vorzeichen -- gefunden an
#: ``:61:2302010221RDR50,00``, wo genau das 100,00 EUR Unterschied machte.
VORZEICHEN = {"C": 1, "D": -1, "RC": -1, "RD": 1}


def _zahl(kennzeichen, wert):
    """Ein MT940-Betrag als Decimal, mit dem Vorzeichen aus VORZEICHEN."""
    betrag = Decimal(wert.replace(".", "").replace(",", "."))
    return betrag * VORZEICHEN[kennzeichen]


def _tag(sechs):
    """JJMMTT aus MT940 als ISO-Datum.

    Die Jahrhundertgrenze ist bei 70 gezogen: MT940 nennt nur zwei Stellen,
    und Kontoauszuege dieser App beginnen 2004. Ein '70' waere 1970 und
    faellt damit sofort auf, statt als 2070 unbemerkt durchzugehen.
    """
    jahr = int(sechs[:2])
    return "%04d-%s-%s" % (2000 + jahr if jahr < 70 else 1900 + jahr,
                           sechs[2:4], sechs[4:6])


def blaetter(text):
    """Die Auszugsblaetter einer .sta-Datei, in ihrer Reihenfolge.

    Eine Datei kann mehrere Konten enthalten -- die Sammelexporte von
    StarMoney tun das -- deshalb traegt jedes Blatt seine Kontonummer aus
    :25: bei sich und wird nicht der Datei zugeschrieben.

    :return: Liste von Blatt
    """
    gefunden = []
    for roh in _bloecke(text):
        konto = _konto_in(roh)
        salden = list(_SALDO.finditer(roh))
        anfang = next((m for m in salden if m.group("art") == "0"), None)
        ende = next((m for m in salden if m.group("art") == "2"), None)
        if konto is None or anfang is None or ende is None:
            continue
        betraege = [_zahl(m.group("vz"), m.group("wert"))
                    for m in _UMSATZ.finditer(roh)]
        gefunden.append(Blatt(
            konto=konto,
            anfang_tag=_tag(anfang.group("tag")),
            anfang=_zahl(anfang.group("vz"), anfang.group("wert")),
            ende_tag=_tag(ende.group("tag")),
            ende=_zahl(ende.group("vz"), ende.group("wert")),
            summe=sum(betraege, Decimal(0)),
            zeilen=len(betraege)))
    return gefunden


def _bloecke(text):
    """Ein Block je :20:. Das ist die Blattgrenze in MT940."""
    aktuell = []
    for zeile in text.splitlines(True):
        if zeile.startswith(":20:") and aktuell:
            yield "".join(aktuell)
            aktuell = []
        aktuell.append(zeile)
    if aktuell:
        yield "".join(aktuell)


def _konto_in(block):
    """Die Kontonummer aus :25: -- ohne fuehrende Nullen, wie sie die IBAN
    am Ende traegt."""
    for zeile in block.splitlines():
        if zeile.startswith(":25:"):
            nummer = zeile[4:].strip().split("/")[-1]
            return nummer.lstrip("0") or "0"
    return None


def kette(blaetter_eines_kontos):
    """Frage 1: greifen die Blaetter lueckenlos ineinander?

    :param blaetter_eines_kontos: Blaetter EINES Kontos, in Reihenfolge
    :return: Liste der Bruchstellen, leer wenn lueckenlos
    """
    brueche = []
    for vorher, jetzt in zip(blaetter_eines_kontos, blaetter_eines_kontos[1:]):
        if abs(jetzt.anfang - vorher.ende) > CENT:
            brueche.append(Bruch(
                konto=jetzt.konto, tag=jetzt.anfang_tag,
                erwartet=vorher.ende, gefunden=jetzt.anfang,
                fehlt=jetzt.anfang - vorher.ende))
    return brueche


def summenprobe(blaetter_eines_kontos):
    """Frage 2: ergeben die Buchungen eines Blattes seine Saldodifferenz?

    :return: Liste der Blaetter, die nicht aufgehen
    """
    daneben = []
    for blatt in blaetter_eines_kontos:
        soll = blatt.ende - blatt.anfang
        if abs(soll - blatt.summe) > CENT:
            daneben.append(Bruch(
                konto=blatt.konto, tag=blatt.ende_tag,
                erwartet=soll, gefunden=blatt.summe,
                fehlt=soll - blatt.summe))
    return daneben


def blinde_jahre(daneben):
    """Die Jahrgaenge, ueber die der Auszug nichts sagen kann.

    Ein Blatt, das die Summenprobe nicht besteht, ist unvollstaendig. Sein
    Zeitraum gehoert aus Frage 3 heraus -- sonst klagt ein loechriger Auszug
    eine richtige Buchhaltung an.
    """
    return {bruch.tag[:4] for bruch in daneben}


def vergleiche(blaetter_eines_kontos, bewegung_zwischen, ausser=()):
    """Frage 3: trifft die Buchhaltung den Kontoauszug?

    :param bewegung_zwischen: Funktion (von, bis) -> Decimal, die Summe der
        Bewegungen im System in ``von < Datum <= bis``. Absichtlich
        hineingereicht statt hier abgefragt: so ist dieses Modul ohne Bench
        pruefbar, und der Aufrufer entscheidet, was "Bewegung" heisst.
    :param ausser: Jahrgaenge, ueber die der Auszug schweigt -- siehe
        blinde_jahre
    :return: Liste der Abweichungen, leer wenn die Buchhaltung stimmt
    """
    abweichungen = []
    for blatt in blaetter_eines_kontos:
        if blatt.ende_tag[:4] in ausser or blatt.anfang_tag[:4] in ausser:
            continue
        soll = blatt.ende - blatt.anfang
        ist = Decimal(str(bewegung_zwischen(blatt.anfang_tag, blatt.ende_tag)))
        if abs(soll - ist) > CENT:
            abweichungen.append(Abweichung(
                konto=blatt.konto, von=blatt.anfang_tag, bis=blatt.ende_tag,
                bank=soll, system=ist, fehlt=soll - ist))
    return abweichungen


def abschnitte(blaetter_eines_kontos):
    """Die Laeufe, in denen die Kette haelt.

    Ein Bruch macht nicht den ganzen Auszug unbrauchbar, sondern teilt ihn.
    Was vor dem Bruch liegt und was danach, ist jeweils fuer sich schluessig
    und kann fuer seinen eigenen Zeitraum entscheiden.

    Der Anlass ist nicht theoretisch: der Auszug des groessten Kontos
    (33080697, 56.463 Buchungen, 2004 bis 2026) hat genau einen Bruch, und
    zwar zwischen einem Fragment vom Dezember 2004 und dem Jahr 2005. Wer
    daraufhin den ganzen Auszug verwirft, wirft einundzwanzig lueckenlose
    Jahre weg, um ein Fragment von 93 Zeilen.

    :return: Liste von Listen, jede ein Lauf ohne Bruch
    """
    if not blaetter_eines_kontos:
        return []
    laeufe = [[blaetter_eines_kontos[0]]]
    for vorher, jetzt in zip(blaetter_eines_kontos, blaetter_eines_kontos[1:]):
        if abs(jetzt.anfang - vorher.ende) > CENT:
            laeufe.append([jetzt])
        else:
            laeufe[-1].append(jetzt)
    return laeufe


def urteil(blaetter_eines_kontos, bewegung_zwischen):
    """Alle drei Fragen, in der einen Reihenfolge, die sie zulassen.

    Geurteilt wird abschnittsweise: jeder Lauf ohne Bruch entscheidet fuer
    seinen eigenen Zeitraum, und wo die Kette bricht, schweigt das Urteil
    ueber die Bruchstelle -- aber nicht ueber den Rest.

    :return: {"kette", "summenprobe", "blind", "abweichungen", "brauchbar",
              "spricht_fuer"}
        ``spricht_fuer`` nennt die Zeitraeume, ueber die dieser Auszug
        ueberhaupt etwas sagen kann. Ohne diese Angabe liest sich ein leeres
        ``abweichungen`` wie ein Freispruch, obwohl es auch heissen kann,
        dass gar nicht geprueft wurde.
    """
    brueche = kette(blaetter_eines_kontos)
    daneben = summenprobe(blaetter_eines_kontos)
    blind = blinde_jahre(daneben)

    abweichungen = []
    spricht_fuer = []
    for lauf in abschnitte(blaetter_eines_kontos):
        beurteilbar = [b for b in lauf
                       if b.anfang_tag[:4] not in blind
                       and b.ende_tag[:4] not in blind]
        if not beurteilbar:
            continue
        spricht_fuer.append((beurteilbar[0].anfang_tag,
                             beurteilbar[-1].ende_tag))
        abweichungen.extend(vergleiche(lauf, bewegung_zwischen, ausser=blind))

    return {
        "kette": brueche,
        "summenprobe": daneben,
        "blind": sorted(blind),
        "brauchbar": bool(spricht_fuer),
        "spricht_fuer": spricht_fuer,
        "abweichungen": abweichungen,
    }

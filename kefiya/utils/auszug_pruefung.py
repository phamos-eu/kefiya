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

import datetime
import re
from collections import namedtuple
from decimal import Decimal

#: Ab wann zwei Betraege als verschieden gelten. Cent-genau; alles Feinere
#: waere Rundungsrauschen, alles Groebere wuerde Fehler durchlassen.
CENT = Decimal("0.005")

#: :60F: (Anfangssaldo) und :62F: (Schlusssaldo) schliessen ein Blatt ein.
#: Die M-Varianten (:60M:, :62M:) sind Zwischensalden: ein Blatt, das zu
#: lang fuer einen Block ist, geht mit :62M: zu Ende und mit :60M: im
#: naechsten Block weiter. Beide Bloecke zusammen sind EIN Blatt -- wer den
#: ersten wegen des fehlenden :62F: und den zweiten wegen des fehlenden
#: :60F: verwirft, verliert das ganze Blatt. Auf Konto 7777777 war das der
#: Jahrgang 2020: 221 Buchungen, und der Auszug schien erst 2021 zu beginnen.
_SALDO = re.compile(
    r"^:6(?P<art>[02])(?P<schluss>[FM]):(?P<vz>[CD])(?P<tag>\d{6})"
    r"[A-Z]{3}(?P<wert>[\d,.]+)", re.MULTILINE)

#: :61: Umsatz. Valuta (6), optional Buchungstag (4), Soll/Haben, optional
#: ein Waehrungsbuchstabe, dann der Betrag mit Komma als Dezimaltrennzeichen.
_UMSATZ = re.compile(
    r"^:61:(?P<valuta>\d{6})(?P<buchung>\d{4})?"
    r"(?P<vz>R?[CD])(?P<waehrung>[A-Z])?(?P<wert>[\d,]+)",
    re.MULTILINE)

#: Ein Kontoauszugsblatt: welches Konto, welcher Zeitraum, was drin steht.
#: ``erster_tag`` ist der frueheste Buchungstag auf dem Blatt (None, wenn es
#: leer ist) -- er entscheidet, wie das Fenster des Blattes beginnt, siehe
#: fenster().
Blatt = namedtuple(
    "Blatt", "konto anfang_tag anfang ende_tag ende summe zeilen erster_tag")

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


def _vortag(iso):
    return (datetime.date.fromisoformat(iso)
            - datetime.timedelta(days=1)).isoformat()


def _buchungstag(valuta, buchung):
    """Der Buchungstag eines Umsatzes als ISO-Datum.

    :61: nennt die Valuta mit Jahr und den Buchungstag ohne. Das Jahr ist
    das der Valuta -- ausser am Jahreswechsel: Valuta 30.12., gebucht am
    02.01., liegt im neuen Jahr, und umgekehrt.
    """
    if not buchung:
        return _tag(valuta)
    jahr = int(_tag(valuta)[:4])
    if valuta[2:4] == "12" and buchung[:2] == "01":
        jahr += 1
    elif valuta[2:4] == "01" and buchung[:2] == "12":
        jahr -= 1
    return "%04d-%s-%s" % (jahr, buchung[:2], buchung[2:4])


def blaetter(text):
    """Die Auszugsblaetter einer .sta-Datei, in ihrer Reihenfolge.

    Eine Datei kann mehrere Konten enthalten -- die Sammelexporte von
    StarMoney tun das -- deshalb traegt jedes Blatt seine Kontonummer aus
    :25: bei sich und wird nicht der Datei zugeschrieben.

    Ein Blatt kann ueber mehrere Bloecke gehen (:62M: ... :60M:, siehe
    _SALDO). Es beginnt mit dem :60F: und endet mit dem :62F:; was dazwischen
    an Zwischensalden steht, wird nicht gelesen, sondern nachgerechnet -- die
    Summenprobe ueber das ganze Blatt fragt genau das.

    :return: Liste von Blatt
    """
    gefunden = []
    offen = None
    for roh in _bloecke(text):
        konto = _konto_in(roh)
        salden = list(_SALDO.finditer(roh))
        anfang = next((m for m in salden if m.group("art") == "0"), None)
        ende = next((m for m in salden if m.group("art") == "2"), None)
        if konto is None or anfang is None or ende is None:
            continue

        umsaetze = list(_UMSATZ.finditer(roh))
        betraege = [_zahl(m.group("vz"), m.group("wert")) for m in umsaetze]
        tage = [_buchungstag(m.group("valuta"), m.group("buchung"))
                for m in umsaetze]

        if anfang.group("schluss") == "F":
            offen = {"konto": konto,
                     "anfang_tag": _tag(anfang.group("tag")),
                     "anfang": _zahl(anfang.group("vz"), anfang.group("wert")),
                     "betraege": [], "tage": []}
        elif offen is None or offen["konto"] != konto:
            # Eine Fortsetzung ohne Anfang: ihr Blatt ist nicht lesbar.
            offen = None
            continue

        offen["betraege"].extend(betraege)
        offen["tage"].extend(tage)
        if ende.group("schluss") != "F":
            continue

        gefunden.append(Blatt(
            konto=offen["konto"],
            anfang_tag=offen["anfang_tag"],
            anfang=offen["anfang"],
            ende_tag=_tag(ende.group("tag")),
            ende=_zahl(ende.group("vz"), ende.group("wert")),
            summe=sum(offen["betraege"], Decimal(0)),
            zeilen=len(offen["betraege"]),
            erster_tag=min(offen["tage"]) if offen["tage"] else None))
        offen = None
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


def fenster(lauf):
    """Die Zeitfenster ``von < Buchungstag <= bis`` der Blaetter eines Laufs.

    Zwei Regeln, und beide sind an echten Auszuegen gemessen:

    1. Ein Blatt, das an ein anderes anschliesst, beginnt, wo das andere
       endet: ``von`` ist der Schlusstag des Vorgaengers. Damit sind die
       Fenster lueckenlos und ueberlappen nicht -- gleich, wie die Bank den
       Anfangssaldo datiert.
    2. Das erste Blatt eines Laufs hat keinen Vorgaenger. Datiert die Bank
       den Anfangssaldo auf den Vortag der ersten Buchung, beginnt das
       Fenster hinter diesem Tag. StarMoney aber datiert ihn auf den ERSTEN
       BUCHUNGSTAG selbst: ``:60F:C210104`` und darunter sechs Buchungen
       vom 04.01. Ein Fenster, das hinter dem 04.01. beginnt, verliert sie.
       Ob das so ist, sagt das Blatt selbst -- ``erster_tag``.

    Vor dieser Regel galt ``anfang_tag < Datum`` fuer jedes Blatt. Auf Konto
    7777777 fielen damit die Buchungen jedes ersten Tages aus jedem Fenster,
    und die Pruefung meldete sechs von sechs Jahrgaengen als falsch -- auch
    dort, wo die Buchhaltung stimmte.

    :param lauf: Blaetter ohne Bruch, in Reihenfolge -- siehe abschnitte()
    :return: Liste von (von, bis), eines je Blatt, von exklusiv
    """
    ergebnis = []
    vorher = None
    for blatt in lauf:
        if vorher is not None:
            von = vorher.ende_tag
        elif blatt.erster_tag is not None \
                and blatt.erster_tag <= blatt.anfang_tag:
            von = _vortag(min(blatt.erster_tag, blatt.anfang_tag))
        else:
            von = blatt.anfang_tag
        ergebnis.append((von, blatt.ende_tag))
        vorher = blatt
    return ergebnis


def bewegung_aus(eintraege):
    """Eine Funktion (von, bis) -> Decimal aus gelesenen Eintraegen.

    Das Gegenstueck zu dem, was die Instanz fuer vergleiche() liefert -- nur
    dass die Bewegung hier aus einer Liste von Eintraegen kommt, jeder mit
    ``date`` und ``amount``. So laesst sich ein zweiter Leser derselben
    Datei gegen die Blaetter halten, bevor irgendetwas gebucht wird.
    """
    def zwischen(von, bis):
        return sum((Decimal(str(e["amount"])) for e in eintraege
                    if von < str(e["date"])[:10] <= bis), Decimal(0))
    return zwischen


def vergleiche(lauf, bewegung_zwischen, ausser=()):
    """Frage 3: trifft die Buchhaltung den Kontoauszug?

    :param lauf: Blaetter ohne Bruch, in Reihenfolge -- ihre Fenster kommen
        aus fenster()
    :param bewegung_zwischen: Funktion (von, bis) -> Decimal, die Summe der
        Bewegungen im System in ``von < Datum <= bis``. Absichtlich
        hineingereicht statt hier abgefragt: so ist dieses Modul ohne Bench
        pruefbar, und der Aufrufer entscheidet, was "Bewegung" heisst.
    :param ausser: Jahrgaenge, ueber die der Auszug schweigt -- siehe
        blinde_jahre
    :return: Liste der Abweichungen, leer wenn die Buchhaltung stimmt
    """
    abweichungen = []
    for blatt, (von, bis) in zip(lauf, fenster(lauf)):
        if blatt.ende_tag[:4] in ausser or blatt.anfang_tag[:4] in ausser:
            continue
        soll = blatt.ende - blatt.anfang
        ist = Decimal(str(bewegung_zwischen(von, bis)))
        if abs(soll - ist) > CENT:
            abweichungen.append(Abweichung(
                konto=blatt.konto, von=von, bis=bis,
                bank=soll, system=ist, fehlt=soll - ist))
    return abweichungen


def abschnitte(blaetter_eines_kontos):
    """Die Laeufe, in denen die Kette haelt.

    Ein Bruch macht nicht den ganzen Auszug unbrauchbar, sondern teilt ihn.
    Was vor dem Bruch liegt und was danach, ist jeweils fuer sich schluessig
    und kann fuer seinen eigenen Zeitraum entscheiden.

    Der Anlass ist nicht theoretisch: der Auszug des groessten Kontos
    (5555555, 56.463 Buchungen, 2004 bis 2026) hat genau einen Bruch, und
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
              "fenster", "spricht_fuer"}
        ``fenster`` sind die Zeitfenster (von, bis) der Blaetter, ueber die
        dieser Auszug urteilen kann -- eines je Blatt, von EXKLUSIV, siehe
        fenster(). ``spricht_fuer`` fasst sie zusammen, wo sie aneinander
        anschliessen. Ohne diese Angabe liest sich ein leeres
        ``abweichungen`` wie ein Freispruch, obwohl es auch heissen kann,
        dass gar nicht geprueft wurde.
    """
    brueche = kette(blaetter_eines_kontos)
    daneben = summenprobe(blaetter_eines_kontos)
    blind = blinde_jahre(daneben)

    abweichungen = []
    beurteilbar = []
    spricht_fuer = []
    for lauf in abschnitte(blaetter_eines_kontos):
        im_lauf = [(von, bis) for b, (von, bis) in zip(lauf, fenster(lauf))
                   if b.anfang_tag[:4] not in blind
                   and b.ende_tag[:4] not in blind]
        beurteilbar.extend(im_lauf)
        # Blaetter eines Laufs, die aneinander anschliessen, sprechen als ein
        # Zeitraum; ein blindes Blatt dazwischen teilt ihn -- und ein Bruch
        # sowieso, denn jeder Lauf spricht fuer sich.
        zeitraeume = []
        for von, bis in im_lauf:
            if zeitraeume and zeitraeume[-1][1] == von:
                zeitraeume[-1] = (zeitraeume[-1][0], bis)
            else:
                zeitraeume.append((von, bis))
        spricht_fuer.extend(zeitraeume)
        abweichungen.extend(vergleiche(lauf, bewegung_zwischen, ausser=blind))

    return {
        "kette": brueche,
        "summenprobe": daneben,
        "blind": sorted(blind),
        "brauchbar": bool(spricht_fuer),
        "fenster": beurteilbar,
        "spricht_fuer": spricht_fuer,
        "abweichungen": abweichungen,
    }

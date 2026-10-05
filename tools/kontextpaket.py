"""Kontextpaket: Pflichtkontext vollständig, belegt und prüfbar — rein lesend
(Vertrag: orchestriertes-team/VERTRAG.md §11, besonders §11.1–§11.11).

    python3 tools/kontextpaket.py bau PROJEKTORDNER [--auftrag TEXT |
        --auftrag-datei DATEI] [--entscheidung E-NNN ...] [--pfad PFAD ...]
        [--max-zeichen N] [--ausgabe ORDNER]
    python3 tools/kontextpaket.py pruefe PAKETDATEI [--projekt PROJEKTORDNER]
    python3 tools/kontextpaket.py protokoll PROJEKTORDNER ... | --alle [--repos DATEI]
    python3 tools/kontextpaket.py kandidaten PROJEKTORDNER
        (--auftrag TEXT | --auftrag-datei DATEI) [--merkzettel ORDNER]

Leitsatz (§11): Kürzen ist erlaubt, Verschweigen nicht. Das Paket darf lange
Entscheidungen auf ihre Entscheidungszeile verdichten, weil die Quelle mit
Zeilenangabe danebensteht — aber keine geltende Einheit fehlt, keine aktive
Bedingung wird umformuliert, nichts Unverständiges fällt still weg: Es steht
wörtlich unter „UNKLAR — selbst lesen“ und macht den Lauf rot.

Befunde nach stderr, das Paket nach stdout (oder in --ausgabe), damit `> datei`
nie Befundtext ins Paket mischt. --ausgabe ist der einzige Schreibweg.
Harte Befunde: FORMAT_UNKLAR · NUMMER_DOPPELT · DEKLARATION_UNBEKANNTER_SCHLUESSEL ·
STAND_NICHT_GEFUNDEN · STAND_NICHT_DEKLARIERT (nur bei bau) ·
CODEBLOCK_NIE_GESCHLOSSEN · QUELLE_FEHLT · QUELLE_AUSSERHALB ·
DEKLARATION_GEAENDERT · KEINE_DEKLARATION (nur bei bau hart) ·
EINHEIT_UEBER_GRENZE · EINHEIT_DOPPELT · EINHEIT_FEHLT · EINHEIT_GEAENDERT ·
STATUS_GEAENDERT · BLOCK_FEHLT · STAND_VERALTET · TEXT_FEHLT · TEIL_FEHLT.
Weiche Befunde: KEINE_DEKLARATION (nur bei protokoll) · STATUS_UNKLAR · TEILWEISE_REVIDIERT ·
FELD_FEHLT · BEDINGUNG_DOPPELT · STAND_OHNE_ENDE (nur bei protokoll) · KEINE_LISTEN ·
HISTORIE_MARKE_FEHLT · ENTSCHEIDUNG_DOPPELT · AUFTRAGSBEZUG_LEER · QUELLE_GEAENDERT.
Exit 0 = kein harter Befund · 1 = mindestens ein harter Befund · 2 = Aufruffehler ·
3 = Paket über --max-zeichen ohne --ausgabe (stdout bleibt leer; die Grenze misst
das ganze Paket, §11.12 Punkt 1).
Nur Standardbibliothek, kein Netz, kein Schreiben in Projekten (§8, §11).
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

STANDARD_REPOS_JSON = Path(__file__).resolve().parent / "repos.json"
DEKLARATIONS_NAME = "KONTEXT.json"
STUECK_GRENZE = 4000          # Zusatz-Abschnitte darüber werden geteilt, nie gekürzt (§11.7.1)
FUNDSTELLEN_MAX = 15          # Block 9 zeigt nur die ersten 15 Kandidaten (§11.7.4)
UEBERSICHT_RUMPF_ZEICHEN = 300  # ohne Feld „Entscheidung“: erste 300 Zeichen des Rumpfs (§11.5.5)

# Feste Stoppwortliste für die Suchwörter des Auftrags (§11.5 Punkt 6c) —
# Vertrag (§11.12 Punkt 8): die Liste steht wörtlich fest, Änderung ist
# Vertragsänderung. Der Manifest-Schlüssel stoppwoerter_sha macht sie prüfbar.
STOPWOERTER_LISTE = [
    "aber", "alles", "allen", "andere", "anderen", "auch", "dafür", "damit",
    "danach", "dann", "darf", "darum", "dass", "denen", "deren", "diese",
    "diesem", "diesen", "dieser", "dieses", "durch", "eines", "einem",
    "einen", "einer", "erst", "etwas", "haben", "hatte", "immer", "jetzt",
    "kann", "keine", "keinen", "können", "machen", "mehr", "muss", "nicht",
    "noch", "nur", "oder", "ohne", "schon", "sein", "seine", "sind", "soll",
    "sollen", "über", "unter", "werden", "wird", "wurde", "wieder", "weil",
    "wenn", "zwischen", "bitte", "einmal", "geht", "gibt", "heute", "hier",
    "sowie", "werkzeug",
]
STOPWOERTER = frozenset(STOPWOERTER_LISTE)

# Reihenfolge der harten Befundarten für die stabile Ausgabe
HARTE_ORDNUNG = [
    "FORMAT_UNKLAR",
    "NUMMER_DOPPELT",
    "DEKLARATION_UNBEKANNTER_SCHLUESSEL",
    "STAND_NICHT_GEFUNDEN",
    "STAND_NICHT_DEKLARIERT",
    "CODEBLOCK_NIE_GESCHLOSSEN",
    "QUELLE_FEHLT",
    "QUELLE_AUSSERHALB",
    "DEKLARATION_GEAENDERT",
    "KEINE_DEKLARATION",
    "EINHEIT_UEBER_GRENZE",
    "EINHEIT_DOPPELT",
    "EINHEIT_FEHLT",
    "EINHEIT_GEAENDERT",
    "STATUS_GEAENDERT",
    "BLOCK_FEHLT",
    "STAND_VERALTET",
    "TEXT_FEHLT",
    "TEIL_FEHLT",
]
WEICHE_ORDNUNG = [
    "KEINE_DEKLARATION",
    "STATUS_UNKLAR",
    "TEILWEISE_REVIDIERT",
    "FELD_FEHLT",
    "BEDINGUNG_DOPPELT",
    "STAND_OHNE_ENDE",
    "KEINE_LISTEN",
    "HISTORIE_MARKE_FEHLT",
    "ENTSCHEIDUNG_DOPPELT",
    "AUFTRAGSBEZUG_LEER",
    "QUELLE_GEAENDERT",
    "JEV_AUS", "JEV_NICHT_BEWERTET", "JEV_UNZUVERLAESSIG",
    "JEV_ZU_TEUER", "JEV_SCHLUESSELMUSTER",
]

HEADING_MUSTER = re.compile(r"^(#{1,6})\s+(.*)$")
BEDINGUNG_ABSCHNITT_MUSTER = re.compile(r"^#{1,4}\s+(?:Weitere\s+)?Bedingungen\b")
# Eintragskopf mit Datumszusatz (§11.13 Punkt 1): Datum, beliebiger Zusatz bis
# zum Strich (wird mitgeführt, nicht ausgewertet), Status. Ein Protokoll schreibt z. B.
# „- [2026-09-06 18:26 | aktiv]“ oder „- [2026-09-08 19:5x | aktiv]“.
BEDINGUNG_EINTRAG_MUSTER = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2})([^|\]]*)\|\s*([^\]]*)\]")
ENTSCHEIDUNG_KOPF_MUSTER = re.compile(
    r"^(#{2,4})\s+E-(\d{3,})\s*\|\s*([^|]+?)\s*\|\s*(.+)$")
FELD_MUSTER = re.compile(
    r"^- \*\*(Entscheidung|Verworfene Alternative|Grund|Betroffene Pfade|"
    r"Prüfbar durch):\*\*\s?(.*)$")
CODE_ZAUN_MUSTER = re.compile(r"^```")   # nur Zäune am Zeilenanfang (§11.12 Punkt 5)


class Aufruffehler(Exception):
    """Aufruffehler (Exit 2), z. B. Ordner fehlt oder Deklaration kein gültiges JSON."""


class Befund:
    """Ein Befund: art, hart?, datei, zeile, text — sortierbar und JSON-fähig.

    zeile_text (§11.15 Punkt 8): die wörtliche Quellzeile an datei/zeile —
    ein Befund ist bekannt an Art + Datei + Wortlaut, nicht an der Zeilennummer.
    """

    def __init__(self, art, text, datei=None, zeile=None, hart=None,
                 meldung_text=None):
        self.art = art
        self.hart = (art in HARTE_ORDNUNG) if hart is None else hart
        self.datei = datei
        self.zeile = zeile
        self.text = text
        self.zeile_text = None
        # §15.2 Punkte 3+4: abweichende Anzeige nur für die stderr-Meldung;
        # text (und damit Manifest) bleibt unverändert.
        self.meldung_text = meldung_text

    def stelle(self):
        if self.datei is None:
            return ""
        return f"{self.datei}" + (f":{self.zeile}" if self.zeile else "")

    def zeile_zeile(self):
        text = self.meldung_text if self.meldung_text is not None else self.text
        return f"  {self.art}: {self.stelle() + ' — ' if self.stelle() else ''}{text}"

    def json_dict(self):
        d = {"art": self.art, "hart": self.hart, "datei": self.datei,
             "zeile": self.zeile, "text": self.text}
        if self.zeile_text is not None:
            d["zeile_text"] = self.zeile_text
        return d


def haenge_zeile_text_an(befunde, gelesen):
    """Hängt Parser-Befunden die wörtliche Quellzeile an (§11.15 Punkt 8).

    gelesen = {deklarierter Name: Text} aus lies_quellen; Befunde ohne Fundstelle
    (Deklarations-Befunde) bleiben unverändert.
    """
    for b in befunde:
        if b.zeile is None or b.datei is None or b.zeile_text is not None:
            continue
        text = gelesen.get(b.datei)
        if not text:
            continue
        zeilen = text.splitlines()
        if 1 <= b.zeile <= len(zeilen):
            b.zeile_text = zeilen[b.zeile - 1]


def befunde_sortieren(befunde):
    """Harte zuerst (in HARTE_ORDNUNG-Reihenfolge), dann weiche, je nach Stelle."""
    rang = {k: i for i, k in enumerate(HARTE_ORDNUNG)}
    rang.update({k: 100 + i for i, k in enumerate(WEICHE_ORDNUNG)})
    return sorted(befunde, key=lambda b: (0 if b.hart else 1,
                                          rang.get(b.art, 199),
                                          b.stelle(), b.text))


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Manifest-Schlüssel stoppwoerter_sha (§11.12 Punkt 8): SHA-256 (12 Hex) über
# die Liste, mit \n verbunden. Änderung der Liste = Vertragsänderung.
STOPPOERTER_SHA = sha256_hex("\n".join(STOPWOERTER_LISTE))[:12]


def normalisierte_zeilen(zeilen):
    """Je Zeile Leerraum am Ende entfernt, Zeilenende \\n (§11.3.7)."""
    return "".join(z.rstrip() + "\n" for z in zeilen)


def leerraum_losch(text):
    """Leerraum zu einem Leerzeichen zusammenfassen (§11.9 TEXT_FEHLT-Vergleich)."""
    return re.sub(r"\s+", " ", text).strip()


def lies_datei(pfad):
    try:
        return pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise Aufruffehler(f"Datei nicht lesbar: {pfad} ({e})")


def pfad_im_projekt(ordner, deklarierter_pfad):
    """Prüft die Pfadgrenze (§11.15 Punkt 4): (ordner/pfad).resolve() — Symlinks
    aufgelöst — muss innerhalb von ordner.resolve() liegen.

    -> der aufgelöste Pfad, oder None, wenn der Pfad ausbricht oder nicht
    aufgelöst werden kann; die Datei wird in dem Fall nie geöffnet.
    """
    try:
        ziel = (ordner / deklarierter_pfad).resolve()
        ziel.relative_to(ordner.resolve())
    except (ValueError, OSError):
        return None
    return ziel


def _zaun_toggle(zeilen, ignorieren=None):
    """-> (Liste (nr, zeile, in_code), Zeile des nie geschlossenen Zauns|None)."""
    ergebnis = []
    in_code = False
    offen_bei = None
    for nr, zeile in enumerate(zeilen, 1):
        if CODE_ZAUN_MUSTER.match(zeile) and nr != ignorieren:
            if in_code:
                in_code = False
            else:
                in_code = True
                offen_bei = nr
            ergebnis.append((nr, zeile, True))
            continue
        ergebnis.append((nr, zeile, in_code))
    return ergebnis, (offen_bei if in_code else None)


def code_zaun_offen(zeilen):
    """Zeile des öffnenden Zauns, falls der Codeblock nie geschlossen wird (§11.12.5)."""
    _liste, offen = _zaun_toggle(zeilen)
    return offen


def zeilen_mit_code(zeilen):
    """-> Liste (nr_1basiert, zeile, in_code); Codezäune schalten um.

    Die Zaunzeilen selbst zählen zum Codeblock (§11.3.1) — sonst würde
    bereits der Motivationsfall (Codeblock in einer Liste)
    FORMAT_UNKLAR auf die Zäune melden. Wird das Dateiende mit offenem
    Zaun erreicht (§11.12 Punkt 5), wird ab dem offenen Zaun so gelesen,
    als gäbe es ihn nicht — Einträge dahinter kommen ins Paket.
    """
    ergebnis, offen = _zaun_toggle(zeilen)
    if offen is not None:
        ergebnis, _nix = _zaun_toggle(zeilen, ignorieren=offen)
    return ergebnis


def kopfzeilen(zeilen_mit_code_liste):
    """Alle Überschriften außerhalb von Codeblöcken -> [(nr, ebene, titel, zeile)]."""
    koepfe = []
    for nr, zeile, in_code in zeilen_mit_code_liste:
        if in_code:
            continue
        m = HEADING_MUSTER.match(zeile)
        if m:
            koepfe.append((nr, len(m.group(1)), m.group(2).strip(), zeile))
    return koepfe


def suchwoerter(auftrag):
    """Wörter aus \\w+, ≥5 Zeichen, kleingeschrieben, ohne Stoppwörter (§11.5.6c)."""
    woerter = {w.lower() for w in re.findall(r"\w+", auftrag)
               if len(w) >= 5 and w.lower() not in STOPWOERTER}
    return sorted(woerter)


def punktzahl(text_klein, woerter):
    """Anzahl verschiedener Suchwörter im Stück (§11.7.2), Wortgrenzen."""
    if not woerter:
        return 0
    return sum(1 for w in woerter
               if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text_klein))


# --- Projektdeklaration (§11.2) -------------------------------------------------

DEKL_SCHLUESSEL = {"format", "projekt", "protokoll", "stand", "pflicht_zusatz",
                   "zusatz", "jev", "merkzettel"}
STAND_SCHLUESSEL = {"datei", "beginn", "ende", "historie_ab"}


class Deklaration:
    """KONTEXT.json mit Vorgaben, wenn die Datei fehlt (§11.2.5)."""

    def __init__(self, ordner):
        self.ordner = ordner
        self.vorhanden = False
        self.projekt = ordner.name or ordner.resolve().name or str(ordner)
        self.protokoll = "PROGRESS.md"
        self.stand = None          # dict mit datei/beginn/ende/historie_ab
        self.pflicht_zusatz = []
        self.zusatz = []
        self.jev = True
        self.merkzettel = True
        self.befunde = []          # DEKLARATION_UNBEKANNTER_SCHLUESSEL u. a.


def lade_deklaration(ordner, befunde):
    """KONTEXT.json lesen; fehlt sie, gelten die Vorgaben (KEINE_DEKLARATION meldet der Aufrufer)."""
    dekl = Deklaration(ordner)
    pfad = ordner / DEKLARATIONS_NAME
    if not pfad.is_file():
        return dekl
    dekl.vorhanden = True
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Aufruffehler(f"{DEKLARATIONS_NAME} kein gültiges JSON in {ordner}: {e}")
    if not isinstance(roh, dict):
        raise Aufruffehler(f"{DEKLARATIONS_NAME} in {ordner} ist kein JSON-Objekt")
    for schluessel in sorted(set(roh) - DEKL_SCHLUESSEL):
        befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                              f"unbekannter Schlüssel „{schluessel}“ — Tippfehler wirkt "
                              f"sonst still (§11.2.6)", DEKLARATIONS_NAME))
    if roh.get("format", 1) != 1:
        befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                              f"format {roh.get('format')!r} unbekannt, erwartet 1 "
                              f"(§11.2)", DEKLARATIONS_NAME))
    dekl.projekt = roh.get("projekt") or ordner.name
    dekl.protokoll = roh.get("protokoll") or "PROGRESS.md"
    dekl.pflicht_zusatz = list(roh.get("pflicht_zusatz") or [])
    dekl.zusatz = list(roh.get("zusatz") or [])
    jev = roh.get("jev", True)
    if isinstance(jev, bool):
        dekl.jev = jev
    else:
        befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                              "jev ist kein boolescher Wert (erwartet true oder false) "
                              "(§14 Punkt 1)", DEKLARATIONS_NAME))
    merkzettel = roh.get("merkzettel", True)
    if isinstance(merkzettel, bool):
        dekl.merkzettel = merkzettel
    else:
        befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                              "merkzettel ist kein boolescher Wert (erwartet true "
                              "oder false) (§16.1 Punkt 2b)", DEKLARATIONS_NAME))
    stand = roh.get("stand")
    if stand is not None:
        if not isinstance(stand, dict):
            befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                                  "stand ist kein Objekt (§11.2)", DEKLARATIONS_NAME))
        else:
            for schluessel in sorted(set(stand) - STAND_SCHLUESSEL):
                befunde.append(Befund("DEKLARATION_UNBEKANNTER_SCHLUESSEL",
                                      f"unbekannter Schlüssel „stand.{schluessel}“ "
                                      f"(§11.2.6)", DEKLARATIONS_NAME))
            dekl.stand = {k: stand.get(k) for k in STAND_SCHLUESSEL}
    return dekl


# --- Stand lesen (§11.2.1/§11.2.2) ----------------------------------------------

class Stand:
    def __init__(self, datei, start, ende_einschl, historie_zeile, zeilen):
        self.datei = datei
        self.start = start            # 1-basiert, einschließlich
        self.ende = ende_einschl      # 1-basiert, einschließlich
        self.historie_zeile = historie_zeile or None
        self.zeilen = zeilen          # wörtliche Zeilen des Stands

    def sha(self):
        return sha256_hex(normalisierte_zeilen(self.zeilen))[:12]


def lies_stand(zeilen_mc, dateiname, stand_cfg, befunde, ohne_ende_hart=True):
    """Stand laut KONTEXT.json suchen (§11.2.1/2); None, wenn nichts gefunden.

    STAND_OHNE_ENDE ist bei bau hart, bei protokoll weich (§11.12 Punkt 18).
    """
    beginn = (stand_cfg or {}).get("beginn")
    if not beginn:
        befunde.append(Befund("STAND_NICHT_GEFUNDEN",
                              "KONTEXT.json: stand.beginn fehlt — Stand kann nicht "
                              "gefunden werden", dateiname))
        return None
    start = None
    for nr, zeile, in_code in zeilen_mc:
        if in_code:
            continue
        if re.search(beginn, zeile):
            start = nr
            break
    if start is None:
        befunde.append(Befund("STAND_NICHT_GEFUNDEN",
                              f"beginn-Muster nicht gefunden: /{beginn}/ (§11.2.1)",
                              dateiname))
        return None
    ende_cfg = (stand_cfg or {}).get("ende")
    ende = len(zeilen_mc)
    ohne_ende = True
    if ende_cfg:
        for nr, zeile, in_code in zeilen_mc:
            if nr <= start:
                continue
            if re.search(ende_cfg, zeile):
                ende = nr - 1
                ohne_ende = False
                break
    if ohne_ende:
        befunde.append(Befund("STAND_OHNE_ENDE",
                              "ende-Muster nicht gefunden — Stand reicht bis "
                              "Dateiende (§11.2.1)", dateiname, start,
                              hart=ohne_ende_hart))
    historie_ab = (stand_cfg or {}).get("historie_ab")
    historie_zeile = None
    ende_wohistorie = ende
    if historie_ab:
        for nr, zeile, in_code in zeilen_mc:
            if nr < start or nr > ende:
                continue
            if historie_ab in zeile:
                historie_zeile = nr
                ende_wohistorie = nr - 1
                break
        if historie_zeile is None:
            # §11.13 Auslegung 9: die Marke kommt im Stand nicht vor — die
            # Angabe in KONTEXT.json ist vermutlich veraltet; der Stand reicht
            # wie beschrieben bis ende. Weicher Befund.
            befunde.append(Befund("HISTORIE_MARKE_FEHLT",
                                  f"historie_ab-Marke „{historie_ab}“ kommt im "
                                  f"Stand nicht vor — Angabe vermutlich veraltet; "
                                  f"Stand reicht bis zum Ende (§11.13)", dateiname,
                                  start, hart=False))
    stueck = [z for _nr, z, _c in zeilen_mc[start - 1:ende_wohistorie]]
    return Stand(dateiname, start, ende_wohistorie, historie_zeile, stueck)


# --- Bedingungen und Entscheidungen parsen (§11.3/§11.4) -------------------------

def _ganzes_wort(wort, text):
    return re.search(r"(?<!\w)" + re.escape(wort) + r"(?!\w)", text) is not None


class Bedingung:
    def __init__(self, zeile, datum, status_wortlaut, textzeilen, befunde=None,
                 datei=None):
        self.zeile = zeile
        self.datum = datum
        self.status_wortlaut = status_wortlaut
        self.textzeilen = textzeilen          # Kopfzeile + Fortsetzungen, wörtlich
        self.gilt = True
        self.aufgehoben = False
        # Status: Storno zuerst, mit Wortgrenzen (§11.12 Punkt 6)
        s = (status_wortlaut or "").replace("*", "").lower()
        if _ganzes_wort("aufgehoben", s):
            if _ganzes_wort("wieder", s) or _ganzes_wort("nicht", s):
                self.gilt = True              # gilt weiter, aber weicher Befund
                if befunde is not None:
                    befunde.append(Befund(
                        "STATUS_UNKLAR",
                        f"Status „{status_wortlaut}“ nennt „aufgehoben“ zusammen "
                        f"mit „wieder“/„nicht“ — die Bedingung gilt weiter "
                        f"(§11.12 Punkt 6)", datei, zeile, hart=False))
            else:
                self.gilt = False
                self.aufgehoben = True
        self.sha = sha256_hex(normalisierte_zeilen(textzeilen))[:12]

    @property
    def kennung(self):
        return "B-" + self.sha

    def text(self):
        return normalisierte_zeilen(self.textzeilen)


class Anmerkung:
    """Nicht eingerückte Zeile zwischen den Einträgen, kein Befund (§11.13 Punkt 2).

    Steht wörtlich an ihrer Stelle im Bedingungsblock und ist eine eigene
    Manifest-Einheit, die `pruefe` wie eine Bedingung prüft.
    """

    def __init__(self, zeile, textzeilen):
        self.zeile = zeile
        self.textzeilen = textzeilen
        self.sha = sha256_hex(normalisierte_zeilen(textzeilen))[:12]

    @property
    def kennung(self):
        return "A-" + self.sha

    def text(self):
        return normalisierte_zeilen(self.textzeilen)


def einleitungs_kennung(zeilen):
    """Kennung einer Einleitung (§11.15 Punkt 3): I- + SHA-256 (12 Hex) über den
    normalisierten Zeilen."""
    return "I-" + sha256_hex(normalisierte_zeilen(zeilen))[:12]


def unklar_kennung(text):
    """Kennung einer UNKLAR-Zeile (§11.15 Punkt 2): U- + SHA-256 (12 Hex) über
    den normalisierten Zeilentext."""
    return "U-" + sha256_hex(normalisierte_zeilen([text]))[:12]


def _meldung_zitat(wortlaut, grenze=200):
    """Wortlaut für die stderr-Meldung aufbereiten (§15.2 Punkte 3+4):
    erst Steuerzeichen (Unicode-Kategorie Cc) sichtbar als \\xNN maskieren,
    dann auf höchstens `grenze` Zeichen kürzen — gekürzt endet das Zitat
    mit „…“. Manifest und Paket bekommen weiter den rohen Wortlaut."""
    maskiert = "".join(
        f"\\x{ord(zeichen):02x}" if unicodedata.category(zeichen) == "Cc"
        else zeichen for zeichen in wortlaut)
    if len(maskiert) > grenze:
        maskiert = maskiert[:grenze - 1] + "…"
    return maskiert


def _teilweise_revidiert_text(zitat):
    """Wortlaut der TEILWEISE_REVIDIERT-Meldung; `zitat` ist der Status-
    Wortlaut — für die Meldung durch _meldung_zitat aufbereitet (§15.2)."""
    return (f"Status „{zitat}“ nennt revidiert/überholt/"
            f"ersetzt — Entscheidung bleibt gültig, hebt eine andere "
            f"auf bzw. ersetzt nur einen Teil (§11.14)")


class Entscheidung:
    def __init__(self, nummer, zeile, ebene, datum, rest_wortlaut):
        self.nummer = f"E-{int(nummer):03d}" if int(nummer) < 1000 else f"E-{int(nummer)}"
        self.zeile = zeile
        self.ebene = ebene
        self.datum = datum
        self.rest_wortlaut = rest_wortlaut    # hinter dem zweiten |, wörtlich
        self.status = None                    # None = UNKLAR
        self.kopf_zeile = ""
        self.rumpf_zeilen = []                # Zeilen nach der Kopfzeile
        self.felder = {}                      # name -> Inhalt (Kopfrest + Fortsetzungen)

    @property
    def status_wortlaut(self):
        return self.rest_wortlaut

    def klassifiziere(self, befunde, datei):
        # Status: gültig zuerst, mit Wortgrenzen (§11.14; ersetzt §11.12
        # Punkt 6 für Entscheidungen — Bedingung §11.12 Punkt 6 bleibt)
        rest = self.rest_wortlaut.replace("*", "").lower()
        if rest.startswith("gültig") or rest.startswith("gueltig"):
            self.status = "gültig"
            if (_ganzes_wort("revidiert", rest) or _ganzes_wort("überholt", rest)
                    or _ganzes_wort("ueberholt", rest)
                    or _ganzes_wort("ersetzt", rest)):
                # weicher Hinweis: „gültig — revidiert …“ heißt, diese Ent-
                # scheidung hebt eine andere auf bzw. ersetzt nur einen Teil;
                # sie bleibt in Block 5 und ggf. Block 6 (§11.14).
                # §15.2 Punkte 3+4: nur die stderr-Meldung zeigt den Wortlaut
                # maskiert und gekürzt; der Befundtext (Manifest) bleibt roh.
                befunde.append(Befund(
                    "TEILWEISE_REVIDIERT",
                    _teilweise_revidiert_text(self.rest_wortlaut),
                    datei, self.zeile, hart=False,
                    meldung_text=_teilweise_revidiert_text(
                        _meldung_zitat(self.rest_wortlaut))))
        elif (_ganzes_wort("revidiert", rest) or _ganzes_wort("überholt", rest)
                or _ganzes_wort("ueberholt", rest)):
            self.status = "nicht gültig"
        else:
            self.status = None
            befunde.append(Befund("STATUS_UNKLAR",
                                  f"Status „{self.rest_wortlaut}“ weder gültig noch "
                                  f"revidiert/überholt — wird wie gültig behandelt (§11.4.2)",
                                  datei, self.zeile, hart=False))

    @property
    def status_feld(self):
        """Rest nach dem zweiten | bis vor die erste Folge „ — “, ohne * (§11.12 Punkt 15)."""
        return self.rest_wortlaut.split(" — ")[0].replace("*", "")

    def sha(self):
        return sha256_hex(normalisierte_zeilen(
            [self.kopf_zeile] + self.rumpf_zeilen))[:12]

    def feld(self, name):
        return self.felder.get(name)

    def entscheidungs_feld_text(self):
        return normalisierte_zeilen(self.feld("Entscheidung").splitlines()) \
            if self.feld("Entscheidung") else None


class Protokoll:
    """Ergebnis des Parsens einer Protokolldatei (§11.3/§11.4)."""

    def __init__(self):
        self.bedingungen = []
        self.entscheidungen = []
        self.anmerkungen = []                 # Anmerkung-Objekte (§11.13 Punkt 2)
        self.bedingungs_einleitungen = []     # (abschnittszeile, [Zeilen wörtlich])
        self.fremdzeilen = []                 # (zeile, text) — FORMAT_UNKLAR


def parse_protokoll(zeilen_mc, dateiname, befunde):
    """Eine Protokolldatei rein lesend parsen (§11.3/§11.4)."""
    p = Protokoll()
    koepfe = kopfzeilen(zeilen_mc)
    ents_kopf = {}   # zeilennr -> (ebene, nummer, datum, rest)

    def naechste_ueberschrift(ab, ebene):
        for nr, e, _titel, _z in koepfe:
            if nr > ab and e <= ebene:
                return nr
        return len(zeilen_mc) + 1

    for nr, ebene, titel, _z in koepfe:
        m = ENTSCHEIDUNG_KOPF_MUSTER.match(_zeile_titel(zeilen_mc, nr))
        if m:
            ents_kopf[nr] = (len(m.group(1)), m.group(2), m.group(3).strip(), m.group(4).rstrip())

    # --- Bedingungen-Abschnitte (§11.3) ---
    bed_abschnitte = [(nr, ebene) for nr, ebene, titel, _z in koepfe
                      if BEDINGUNG_ABSCHNITT_MUSTER.match(_zeile_titel(zeilen_mc, nr))]
    for abs_nr, abs_ebene in bed_abschnitte:
        ende_abs = len(zeilen_mc) + 1
        for n2, e2, _t2, _z2 in koepfe:
            if n2 > abs_nr and (e2 <= abs_ebene or n2 in ents_kopf):
                ende_abs = min(ende_abs, n2)
        einleitung = []
        erster_eintrag = None
        aktuell = None
        for nr in range(abs_nr + 1, ende_abs):
            _n, zeile, in_code = zeilen_mc[nr - 1]
            if in_code:
                continue
            eintrag = BEDINGUNG_EINTRAG_MUSTER.match(zeile)
            if eintrag:
                if erster_eintrag is None:
                    erster_eintrag = nr
                    if einleitung:
                        p.bedingungs_einleitungen.append((abs_nr, einleitung))
                aktuell = Bedingung(nr, eintrag.group(1), eintrag.group(3).strip(),
                                    [zeile], befunde, dateiname)
                p.bedingungen.append(aktuell)
                continue
            if zeile.strip() == "":
                continue
            if aktuell is None:
                einleitung.append(zeile)
                continue
            if zeile.startswith((" ", "\t")):
                aktuell.textzeilen.append(zeile)
                continue
            # Nach dem ersten Eintrag (§11.13 Punkt 2): eine Zeile, die mit
            # "- " oder "* " beginnt, aber kein gültiger Eintragskopf ist,
            # bleibt FORMAT_UNKLAR (vermutlich ein verunglückter Eintrag).
            # Jede andere nicht leere, nicht eingerückte Zeile ist eine
            # Anmerkung: wörtlich, ohne Befund, eigene Manifest-Einheit.
            if zeile.startswith(("- ", "* ")):
                p.fremdzeilen.append((nr, zeile))
            else:
                if isinstance(aktuell, Bedingung):
                    p.anmerkungen.append(Anmerkung(nr, [zeile]))
                    aktuell = p.anmerkungen[-1]
                else:
                    aktuell.textzeilen.append(zeile)
        if erster_eintrag is None and einleitung:
            p.bedingungs_einleitungen.append((abs_nr, einleitung))

    # --- Entscheidungen (§11.4) ---
    gefundene = []
    for nr in sorted(ents_kopf):
        ebene, nummer, datum, rest = ents_kopf[nr]
        e = Entscheidung(nummer, nr, ebene, datum, rest)
        e.kopf_zeile = _zeile_titel(zeilen_mc, nr)
        ende_rumpf = naechste_ueberschrift(nr, ebene)
        # §11.12 Punkt 14: Rumpfende auch vor jeder Zeile, auf die der
        # Kopf-Regex passt — egal welche Ebene die hat.
        for n2 in sorted(ents_kopf):
            if nr < n2 < ende_rumpf:
                ende_rumpf = n2
                break
        feldname = None
        feldzeilen = []
        for r in range(nr + 1, ende_rumpf):
            _n, zeile, in_code = zeilen_mc[r - 1]
            if in_code:
                continue
            fm = FELD_MUSTER.match(zeile)
            if fm:
                if feldname:
                    e.felder[feldname] = normalisierte_zeilen(feldzeilen)
                feldname = fm.group(1)
                feldzeilen = [fm.group(2)] if fm.group(2) else []
                continue
            if feldname:
                feldzeilen.append(zeile)
        if feldname:
            e.felder[feldname] = normalisierte_zeilen(feldzeilen)
        e.rumpf_zeilen = [z for _n2, z, _c2 in zeilen_mc[nr:ende_rumpf - 1]]
        gefundene.append(e)

    # Doppelte Nummern (§11.4.5, §11.12 Punkt 13): verschieden Rumpf → hart,
    # gleicher normalisierter Rumpf → weich ENTSCHEIDUNG_DOPPELT und nur der
    # erste Eintrag kommt ins Paket/Manifest.
    nach_nummer = {}
    for e in gefundene:
        erster = nach_nummer.get(e.nummer)
        if erster is None:
            nach_nummer[e.nummer] = e
            p.entscheidungen.append(e)
        elif erster.sha() != e.sha():
            befunde.append(Befund("NUMMER_DOPPELT",
                                  f"{e.nummer} mit verschiedenem Rumpf (§11.4.5)",
                                  dateiname, e.zeile))
            p.entscheidungen.append(e)
        else:
            befunde.append(Befund("ENTSCHEIDUNG_DOPPELT",
                                  f"{e.nummer} mit gleichem Rumpf wie Zeile "
                                  f"{erster.zeile} — nur der erste Eintrag kommt "
                                  f"ins Paket (§11.12 Punkt 13)", dateiname, e.zeile,
                                  hart=False))

    # Klassifikation und Feldprüfung nur für die übernommenen Einträge
    for e in p.entscheidungen:
        e.klassifiziere(befunde, dateiname)

    # --- Befunde aus dem Parsen (§11.3/§11.4) ---
    for zeile, text in p.fremdzeilen:
        befunde.append(Befund("FORMAT_UNKLAR",
                              f"Zeile nach dem ersten Eintrag, weder Eintrag noch "
                              f"Fortsetzung — steht wörtlich unter „UNKLAR“ (§11.3.6)",
                              dateiname, zeile))
    gesehen = {}
    for b in p.bedingungen:
        if b.sha in gesehen:
            befunde.append(Befund("BEDINGUNG_DOPPELT",
                                  f"gleicher Text wie Zeile {gesehen[b.sha].zeile} — "
                                  f"beide bleiben im Paket (§11.3.7)", dateiname, b.zeile))
        else:
            gesehen[b.sha] = b
    for e in p.entscheidungen:
        for pflichtfeld in ("Entscheidung", "Verworfene Alternative", "Grund",
                            "Betroffene Pfade", "Prüfbar durch"):
            if e.feld(pflichtfeld) is None:
                befunde.append(Befund("FELD_FEHLT",
                                      f"{e.nummer}: Feld „{pflichtfeld}“ fehlt — alte "
                                      f"Einträge sind historisch so (§11.4.4)",
                                      dateiname, e.zeile))
    return p


def _zeile_titel(zeilen_mc, nr):
    return zeilen_mc[nr - 1][1]


# --- Quellen einlesen -------------------------------------------------------------

def lies_quellen(ordner, dekl, befunde, ohne_ende_hart=True):
    """Protokoll + Stand + Pflicht-Zusatzdateien rein lesend einlesen (§11.2–§11.4).

    Jede Quelle wird vor dem Lesen auf den Projektordner begrenzt (§11.15
    Punkt 4): resolve() muss innerhalb von ordner.resolve() liegen, sonst
    harter Befund QUELLE_AUSSERHALB und die Datei bleibt zu. Der Stand kommt
    aus der deklarierten Datei stand.datei — Vorgabe: die Protokolldatei
    (§11.15 Punkt 5).

    -> (protokoll|None, stand|None, [(rel_pfad, text)], gelesen)
       mit gelesen = {deklarierter Name: wörtlicher Text} der gelesenen Dateien.
    """
    gelesen = {}
    p = None
    stand = None
    zeilen_mc = None
    prot_pfad = pfad_im_projekt(ordner, dekl.protokoll)
    if prot_pfad is None:
        befunde.append(Befund("QUELLE_AUSSERHALB",
                              f"Protokolldatei {dekl.protokoll} liegt außerhalb des "
                              f"Projektordners — wird nicht gelesen (§11.15 Punkt 4)",
                              dekl.protokoll))
    elif not prot_pfad.is_file():
        befunde.append(Befund("QUELLE_FEHLT",
                              f"Protokolldatei {dekl.protokoll} fehlt im Projektordner "
                              f"(§11.2.3)", dekl.protokoll))
    else:
        roh_text = lies_datei(prot_pfad)
        gelesen[dekl.protokoll] = roh_text
        roh_zeilen = roh_text.splitlines()
        offen = code_zaun_offen(roh_zeilen)
        if offen is not None:
            # §11.12 Punkt 5: Dateiende mit offenem Zaun ist hart; ab dem
            # offenen Zaun wird so gelesen, als gäbe es ihn nicht.
            befunde.append(Befund("CODEBLOCK_NIE_GESCHLOSSEN",
                                  "Codeblock wird nie geschlossen — ab diesem Zaun "
                                  "wird ohne ihn gelesen (§11.12 Punkt 5)",
                                  dekl.protokoll, offen))
        zeilen_mc = zeilen_mit_code(roh_zeilen)
        p = parse_protokoll(zeilen_mc, dekl.protokoll, befunde)

    if dekl.stand:
        stand_name = dekl.stand.get("datei") or dekl.protokoll
        stand_zeilen_mc = None
        if stand_name == dekl.protokoll:
            # Protokolldatei ist die Stand-Datei — schon gelesen (§11.15 Punkt 5)
            stand_zeilen_mc = zeilen_mc
        else:
            stand_pfad = pfad_im_projekt(ordner, stand_name)
            if stand_pfad is None:
                befunde.append(Befund("QUELLE_AUSSERHALB",
                                      f"Stand-Datei {stand_name} liegt außerhalb des "
                                      f"Projektordners — wird nicht gelesen "
                                      f"(§11.15 Punkte 4/5)", stand_name))
            elif not stand_pfad.is_file():
                befunde.append(Befund("STAND_NICHT_GEFUNDEN",
                                      f"Stand-Datei {stand_name} fehlt im "
                                      f"Projektordner (§11.2.1)", stand_name))
            else:
                stand_text = lies_datei(stand_pfad)
                gelesen[stand_name] = stand_text
                stand_zeilen_mc = zeilen_mit_code(stand_text.splitlines())
        if stand_zeilen_mc is not None:
            stand = lies_stand(stand_zeilen_mc, stand_name, dekl.stand, befunde,
                               ohne_ende_hart=ohne_ende_hart)

    pflicht = []
    for muster in dekl.pflicht_zusatz:
        ziel = pfad_im_projekt(ordner, muster)
        if ziel is None:
            befunde.append(Befund("QUELLE_AUSSERHALB",
                                  f"Pflicht-Zusatzdatei {muster} liegt außerhalb des "
                                  f"Projektordners — wird nicht gelesen (§11.15 "
                                  f"Punkt 4)", muster))
            continue
        if not ziel.is_file():
            befunde.append(Befund("QUELLE_FEHLT",
                                  f"Pflicht-Zusatzdatei {muster} fehlt — sie soll "
                                  f"vollständig in den Pflichtteil (§11.2.3)", muster))
            continue
        text = lies_datei(ziel)
        gelesen[muster] = text
        pflicht.append((muster, text))
    return p, stand, pflicht, gelesen


# --- Einheiten (Manifest, §11.6) ---------------------------------------------------

def einheiten_liste(p, stand, pflicht, prot_name, volltext_set):
    """Alle Einheiten des Pflichtteils in fester Reihenfolge (§11.6, §11.12.16).

    Enthält auch aufgehobene Bedingungen (Status „aufgehoben“), nicht gültige
    Entscheidungen (Status = Statusfeld) und Anmerkungen — damit STATUS_GEAENDERT
    in beide Richtungen greift und nichts still wegfällt.
    """
    einheiten = []
    if p is None:
        p = Protokoll()
    for abs_nr, einleitung in p.bedingungs_einleitungen:
        sha12 = einleitungs_kennung(einleitung)[2:]
        einheiten.append({"id": "I-" + sha12, "art": "einleitung",
                          "datei": prot_name, "zeile": abs_nr + 1, "sha": sha12})
    for b in p.bedingungen:
        einheiten.append({"id": b.kennung, "art": "bedingung",
                          "status": "gilt" if b.gilt else "aufgehoben",
                          "datei": prot_name, "zeile": b.zeile, "sha": b.sha})
    for a in p.anmerkungen:
        einheiten.append({"id": a.kennung, "art": "anmerkung",
                          "datei": prot_name, "zeile": a.zeile, "sha": a.sha})
    for zeile_nr, text in p.fremdzeilen:
        einheiten.append({"id": unklar_kennung(text), "art": "unklar",
                          "datei": prot_name, "zeile": zeile_nr,
                          "sha": unklar_kennung(text)[2:]})
    for e in p.entscheidungen:
        status = "gültig" if e.status == "gültig" else (
            "unklar" if e.status is None else e.status_feld)
        einheiten.append({"id": e.nummer, "art": "entscheidung",
                          "status": status,
                          "datei": prot_name, "zeile": e.zeile, "sha": e.sha(),
                          "volltext": e in volltext_set})
        if e in volltext_set:
            # §11.15 Punkt 10: der Volltext ist eine eigene Einheit — so kann
            # die Übersicht in einen anderen Teil als der Volltext fallen,
            # ohne dass eine Kennung doppelt vorkommt.
            einheiten.append({"id": f"{e.nummer}/volltext", "art": "volltext",
                              "datei": prot_name, "zeile": e.zeile, "sha": e.sha()})
    if stand is not None:
        einheiten.append({"id": "STAND", "art": "stand", "datei": stand.datei,
                          "zeile": stand.start, "sha": stand.sha()})
    for rel, _text in pflicht:
        einheiten.append({"id": f"PZ-{rel}", "art": "pflicht_zusatz", "datei": rel,
                          "zeile": 1, "sha": sha256_hex(
                              normalisierte_zeilen(_text.splitlines()))[:12]})
    # §11.15 Punkt 10: jede Kennung genau einmal — identische Texte (weiche
    # BEDINGUNG_DOPPELT) tragen dieselbe Kennung und stehen einmal im Manifest.
    eindeutig = []
    gesehen = set()
    for u in einheiten:
        if u["id"] in gesehen:
            continue
        gesehen.add(u["id"])
        eindeutig.append(u)
    return eindeutig


def quellen_liste(ordner, dekl, gelesen, pflicht):
    """Gelesene Pflichtquellen (inkl. Stand-Datei) + KONTEXT.json (§11.6,
    §11.12 Punkt 16, §11.15 Punkte 5/6)."""
    quellen = []
    schon = set()

    def aufnehmen(name, text):
        if name in schon:
            return
        schon.add(name)
        quellen.append({"datei": name, "sha256": sha256_hex(text),
                        "bytes": len(text.encode("utf-8"))})

    if dekl.protokoll in gelesen:
        aufnehmen(dekl.protokoll, gelesen[dekl.protokoll])
    if dekl.stand:
        stand_name = dekl.stand.get("datei") or dekl.protokoll
        if stand_name in gelesen:
            aufnehmen(stand_name, gelesen[stand_name])
    for rel, _text in pflicht:
        if rel in gelesen:
            aufnehmen(rel, gelesen[rel])
    dekl_pfad = ordner / DEKLARATIONS_NAME
    if dekl.vorhanden and dekl_pfad.is_file():
        aufnehmen(DEKLARATIONS_NAME, lies_datei(dekl_pfad))
    return quellen


# --- Zusatz-Abschnitte und Kandidaten (§11.7) ----------------------------------------

class Abschnitt:
    def __init__(self, pfad_abs, anzeige, zeile, bis, ueberschrift, text):
        self.pfad_abs = pfad_abs
        self.anzeige = anzeige          # Pfad relativ zum Projekt (oder Merkzettel)
        self.zeile = zeile
        self.bis = bis
        self.ueberschrift = ueberschrift
        self.text = text                # wörtlich, Überschrift eingeschlossen
        self.merkzettel = False         # True: Abschnitt aus dem Merkzettel (§16)
        self.sha = sha256_hex(str(pfad_abs) + "\n" + text)[:12]

    @property
    def id(self):
        return "Z-" + self.sha


def abschnitte_einer_datei(pfad, anzeige):
    """Markdown-Abschnitte (Überschrift bis vor nächste gleiche/höhere Ebene, §11.7.1)."""
    zeilen = lies_datei(pfad).splitlines()
    zeilen_mc = zeilen_mit_code(zeilen)
    koepfe = kopfzeilen(zeilen_mc)
    ergebnis = []
    vorspann_ende = koepfe[0][0] if koepfe else len(zeilen) + 1
    if vorspann_ende > 1:
        vorspann = [z for _n, z, _c in zeilen_mc[:vorspann_ende - 1]]
        if any(z.strip() for z in vorspann):
            ergebnis.append(Abschnitt(pfad, anzeige, 1, vorspann_ende - 1,
                                      "(Vorspann)", normalisierte_zeilen(vorspann)))
    for i, (nr, ebene, titel, zeile) in enumerate(koepfe):
        ende = len(zeilen) + 1
        for nr2, ebene2, _t2, _z2 in koepfe[i + 1:]:
            if ebene2 <= ebene:
                ende = nr2
                break
        stueck = [z for _n, z, _c in zeilen_mc[nr - 1:ende - 1]]
        ergebnis.extend(_stuecke(pfad, anzeige, nr, ende - 1, titel, zeile, stueck))
    return ergebnis


def _stuecke(pfad, anzeige, zeile, bis, titel, kopfzeile, zeilen_stueck):
    """Abschnitt über 4000 Zeichen: aufeinanderfolgende Stücke an Zeilengrenzen (§11.7.1)."""
    gesamt = normalisierte_zeilen(zeilen_stueck)
    if len(gesamt) <= STUECK_GRENZE:
        return [Abschnitt(pfad, anzeige, zeile, bis, titel, gesamt)]
    ergebnis = []
    aktuelles = []
    # zeilen_stueck beginnt mit der Kopfzeile; sie steckt in jedem Stück schon
    # über kopfzeile drin — im Rumpf würde sie sonst doppelt stehen.
    rest = zeilen_stueck[1:]
    start = zeile
    grenze = STUECK_GRENZE - len(kopfzeile) - 1
    for z in rest:
        if aktuelles and len(normalisierte_zeilen(aktuelles)) + len(z) + 1 > grenze:
            ergebnis.append(Abschnitt(pfad, anzeige, start, start + len(aktuelles),
                                      titel, kopfzeile + "\n" + normalisierte_zeilen(aktuelles)))
            start = start + len(aktuelles) + 1
            aktuelles = []
        aktuelles.append(z)
    if aktuelles:
        ergebnis.append(Abschnitt(pfad, anzeige, start, start + len(aktuelles),
                                  titel, kopfzeile + "\n" + normalisierte_zeilen(aktuelles)))
    return ergebnis


def zusatz_abschnitte(ordner, dekl, merkzettel=None):
    """Abschnitte aus zusatz-Globs und — mit --merkzettel — aus den Notizen (§11.7.1)."""
    ergebnis = {}
    ordner_echt = ordner.resolve()
    for muster in dekl.zusatz:
        for pfad in sorted(ordner.glob(muster)):
            if not pfad.is_file():
                continue
            try:
                pfad.resolve().relative_to(ordner_echt)
            except ValueError:
                continue  # Treffer außerhalb des Projektordners werden ignoriert (§11.2.4)
            ergebnis[pfad.resolve()] = pfad.resolve().relative_to(ordner_echt).as_posix()
    abschnitte = []
    for pfad_echt in sorted(ergebnis):
        abschnitte.extend(abschnitte_einer_datei(
            pfad_echt, ergebnis[pfad_echt]))
    if merkzettel is not None:
        if not merkzettel.is_dir():
            raise Aufruffehler(f"Merkzettel-Ordner existiert nicht: {merkzettel}")
        merk_echt = merkzettel.resolve()
        for pfad in sorted(merkzettel.rglob("*.md")):
            rel = pfad.relative_to(merkzettel).as_posix()
            if rel == "MEMORY.md" or rel.split("/", 1)[0] == "archiv":
                continue
            if not pfad.is_file():
                continue  # tote Links, Ordner, FIFOs still übergehen (§16.2.1)
            try:
                pfad.resolve().relative_to(merk_echt)
            except (ValueError, OSError):
                continue  # Link-Ziel außerhalb des Merkzettels (§16.2.1)
            for a in abschnitte_einer_datei(pfad, f"merkzettel/{rel}"):
                a.merkzettel = True     # Block 9 zeigt dafür den absoluten Pfad
                abschnitte.append(a)
    return abschnitte


def kandidaten_liste(abschnitte, woerter):
    """Lokale Vorauswahl: ≥1 Suchwort, sortiert nach Punkten, Datei, Zeile (§11.7.2)."""
    kandidaten = []
    for a in abschnitte:
        punkte = punktzahl(a.text.lower(), woerter)
        if punkte > 0:
            kandidaten.append((a, punkte))
    kandidaten.sort(key=lambda pa: (-pa[1], pa[0].anzeige, pa[0].zeile))
    return kandidaten


def kandidat_json(abschnitt, punkte):
    """Ein Kandidat im Format §11.7 Punkt 3 — die eine Bauweise für die
    ``kandidaten``-Ausgabe und den Jev-Eingang (§14 Punkt 4, §14.12)."""
    return {"id": abschnitt.id, "datei": str(abschnitt.pfad_abs),
            "quelle": abschnitt.anzeige, "zeile": abschnitt.zeile,
            "bis": abschnitt.bis, "ueberschrift": abschnitt.ueberschrift,
            "sha": abschnitt.sha, "punkte": punkte, "text": abschnitt.text}


# --- Pakettext (§11.5) ---------------------------------------------------------------

def uebersichtszeile(e):
    """Die eine Zeile aus Block 5 — von bau und pruefe identisch benutzt (§11.5.5/§11.9)."""
    feld = e.feld("Entscheidung")
    if feld is None:
        rumpf = leerraum_losch("\n".join(e.rumpf_zeilen))[:UEBERSICHT_RUMPF_ZEICHEN]
        inhalt = f"{rumpf} [kein Feld ‚Entscheidung‘]"
    else:
        inhalt = leerraum_losch(feld)
    return f"{e.nummer} ({e.datum}) — {inhalt} · Zeile {e.zeile}"


def volltext_auswahl(p, genannte, pfade, woerter):
    """Gültige und UNKLARE Entscheidungen für Block 6 nach (a)/(b)/(c)
    (§11.5.6, §11.15 Punkt 9): Status None (UNKLAR) ist auswählbar wie gültig."""
    auswahl = []
    for e in p.entscheidungen:
        if e.status not in ("gültig", None):
            continue
        if int(e.nummer[2:]) in genannte:
            auswahl.append((e, "genannt"))
            continue
        pfade_feld = e.feld("Betroffene Pfade") or ""
        if pfade and any(p in pfade_feld for p in pfade):
            auswahl.append((e, "pfad"))
            continue
        if woerter and punktzahl(
                (e.kopf_zeile + "\n" + "\n".join(e.rumpf_zeilen)).lower(), woerter) >= 2:
            auswahl.append((e, "suchwörter"))
    return auswahl


class Segment:
    """Unzerlegbarer Block des Pakets: Text + Einheiten-IDs, die er trägt."""

    def __init__(self, text, ids=(), einheit_art=None, einheit_id=None,
                 fixierung=None, block=None):
        self.text = text
        self.ids = list(ids)
        self.einheit_art = einheit_art     # für EINHEIT_UEBER_GRENZE
        self.einheit_id = einheit_id
        self.fixierung = fixierung         # "kopf"/"erstes"/"letztes"/None (§11.12.4)
        self.block = block                 # Blocknummer (§11.5), None = Block 1/Kopf


def baue_segmente(ordner, dekl, p, stand, pflicht, auftrag, woerter, auswahl,
                  befunde, hat_bezug=True):
    """Die Blöcke 1–9 als Segmente in fester Reihenfolge (§11.5); auswahl = Block-6-Liste."""
    seg = []
    harte = sum(1 for b in befunde if b.hart)
    weiche = len(befunde) - harte
    kopf = [f"# Kontextpaket — {dekl.projekt}", "",
            f"- Erzeugt: {{ZEIT}}",
            f"- Befunde: {harte} harte, {weiche} weiche — siehe stderr/Manifest"]
    if auftrag is not None:
        if "\n" in auftrag:
            kopf += ["- Auftrag (wörtlich):", "", "```text", auftrag, "```"]
        else:
            kopf += [f"- Auftrag: {auftrag}"]
    seg.append(Segment("\n".join(kopf) + "\n", fixierung="kopf"))

    if p is not None and p.fremdzeilen:
        unklar = ["## UNKLAR — selbst lesen", "", "```text"]
        for zeile, text in p.fremdzeilen:
            unklar.append(f"{dekl.protokoll}:{zeile}: {text}")
        unklar.append("```")
        seg.append(Segment("\n".join(unklar) + "\n",
                           [unklar_kennung(text) for _z, text in p.fremdzeilen],
                           fixierung="erstes", block=2))

    if dekl.stand:
        if stand is not None:
            text = [f"## Stand", "", f"Quelle: {stand.datei}, Zeilen {stand.start}–{stand.ende}",
                    ""]
            text += stand.zeilen
            if stand.historie_zeile:
                text += ["", f"Historie ab Zeile {stand.historie_zeile} weggelassen, "
                         f"laut KONTEXT.json (§11.2.2)"]
            seg.append(Segment("\n".join(text) + "\n", ["STAND"], "stand", "STAND",
                               block=3))
    else:
        seg.append(Segment("## Stand\n\nSTAND NICHT DEKLARIERT — selbst lesen: "
                           f"{dekl.protokoll}\n", block=3))

    # Block 4: Bedingungen (gelten) (§11.5.4, §11.13 Punkt 2)
    if p and (p.bedingungen or p.anmerkungen or p.bedingungs_einleitungen):
        seg.append(Segment("## Bedingungen (gelten)\n\n", block=4))
        for abs_nr, einleitung in p.bedingungs_einleitungen:
            kopf_text = [f"### {dekl.protokoll} ab Zeile {abs_nr + 1}", ""]
            kopf_text += einleitung + [""]
            seg.append(Segment("\n".join(kopf_text) + "\n",
                               [einleitungs_kennung(einleitung)], block=4))
        # wörtlich an ihrer Stelle: geltende Bedingungen, Anmerkungen und
        # aufgehobene (als Stummelzeile) in Dateireihenfolge
        fuer_reihenfolge = [(b.zeile, "bedingung", b) for b in p.bedingungen] + \
                           [(a.zeile, "anmerkung", a) for a in p.anmerkungen]
        for _zeile, art, obj in sorted(fuer_reihenfolge, key=lambda t: t[0]):
            if art == "anmerkung":
                text = obj.text().rstrip("\n") + f"\n[{obj.kennung} · Zeile {obj.zeile}]\n"
                seg.append(Segment(text, [obj.kennung], "anmerkung", obj.kennung,
                                   block=4))
            elif obj.gilt:
                text = obj.text().rstrip("\n") + f"\n[{obj.kennung} · Zeile {obj.zeile}]\n"
                seg.append(Segment(text, [obj.kennung], "bedingung", obj.kennung,
                                   block=4))
            else:
                stummel = f"[aufgehoben] {obj.datum} · {obj.kennung} · Zeile {obj.zeile}"
                seg.append(Segment(stummel + "\n", [obj.kennung], block=4))

    # Block 5: Entscheidungen (gültig) — Übersicht (§11.5.5)
    if p and p.entscheidungen:
        gueltige = [e for e in p.entscheidungen if e.status in ("gültig", None)]
        if gueltige:
            stueck = ["## Entscheidungen (gültig) — Übersicht", ""]
            seg.append(Segment("\n".join(stueck) + "\n", block=5))
            for e in gueltige:
                zeile = uebersichtszeile(e)
                seg.append(Segment(zeile + "\n", [e.nummer], block=5))

        # Block 6: Volltext, auftragsbezogen (§11.5.6, §11.12 Punkt 19).
        # Ohne jeden Bezug (kein Auftrag, keine --entscheidung, kein --pfad)
        # entfällt der Block mit einer Hinweiszeile.
        if not hat_bezug:
            seg.append(Segment("## Entscheidungen — Volltext, auftragsbezogen\n\n"
                               "(kein Auftrag gegeben — Block entfällt, §11.5.6)\n",
                               block=6))
        elif auswahl:
            stueck = ["## Entscheidungen — Volltext, auftragsbezogen", "",
                      "Treffer per Suchwörter des Auftrags sind eine grobe Vorauswahl; "
                      "sie entfernt nichts aus der Übersicht (§11.5.6).", ""]
            seg.append(Segment("\n".join(stueck) + "\n", block=6))
            for e, grund in auswahl:
                text = [f"### {e.nummer} ({dekl.protokoll}, Zeile {e.zeile} — "
                        f"Volltextgrund: {grund})", ""]
                text.append(normalisierte_zeilen([e.kopf_zeile] + e.rumpf_zeilen).rstrip("\n"))
                seg.append(Segment("\n".join(text) + "\n", [f"{e.nummer}/volltext"],
                                   "volltext", f"{e.nummer}/volltext", block=6))

        # Block 7: nicht gültig (§11.5.7); zeigt das Statusfeld (§11.12 Punkt 15)
        nicht_gueltige = [e for e in p.entscheidungen if e.status == "nicht gültig"]
        if nicht_gueltige:
            stueck = ["## Entscheidungen — nicht gültig", ""]
            seg.append(Segment("\n".join(stueck) + "\n", block=7))
            for e in nicht_gueltige:
                zeile = f"{e.nummer} — {e.status_feld} · Zeile {e.zeile}"
                seg.append(Segment(zeile + "\n", [e.nummer], block=7))

    # Block 8: Pflicht-Zusatzdateien (§11.5.8)
    if pflicht:
        seg.append(Segment("## Pflicht-Zusatzdateien\n\n"
                           "(vollständig und wörtlich, §11.2.3)\n", block=8))
        for rel, text in pflicht:
            seg.append(Segment(f"### {rel}\n\n{text.rstrip(chr(10))}\n",
                               [f"PZ-{rel}"], "pflicht_zusatz", f"PZ-{rel}",
                               block=8))
    return seg


# --- Manifest und Teilen (§11.6/§11.8) ---------------------------------------------

MANIFEST_BLOCK_MUSTER = re.compile(r"^```kontext-manifest[ \t]*\n(.*?)\n^```[ \t]*$",
                                   re.M | re.S)

# Feste Blocküberschriften (§11.5); Block 1 (Kopf), Block 7 (nicht gültig) und
# Block 9 (Zusatz) tragen keine blockbezogene Textpflicht, werden aber beim
# Teilen mitgeführt (§11.15 Punkt 1).
BLOCK_UEBERSCHRIFTEN = {
    2: "## UNKLAR — selbst lesen",
    3: "## Stand",
    4: "## Bedingungen (gelten)",
    5: "## Entscheidungen (gültig) — Übersicht",
    6: "## Entscheidungen — Volltext, auftragsbezogen",
    7: "## Entscheidungen — nicht gültig",
    8: "## Pflicht-Zusatzdateien",
    9: "## Zusatz — Fundstellen",
}


def paket_bloecke(koerper):
    """Paketkörper an den festen Blocküberschriften zerlegen (§11.15 Punkt 1).

    Rückgabe: {blocknr: leerraum-normalisierter Blocktext}. Zeilen vor der ersten
    bekannten Überschrift (Kopf, Block 1, Auftrag) gehören zu keinem geprüften
    Block. „(Fortsetzung)“ hinter der Überschrift ist erlaubt (Teil 2+). Kommt
    dieselbe Überschrift mehrfach vor, zählen die Vorkommen zusammen (getrennt
    durch ein Steuerzeichen, damit über die Grenze hinweg nichts zusammenwächst).
    """
    stuecke = []   # (blocknr, [zeilen])
    aktuell = None
    for zeile in koerper.splitlines():
        gestrichen = zeile.strip()
        kopf = None
        if gestrichen.startswith("## "):
            for nr, kopf_text in BLOCK_UEBERSCHRIFTEN.items():
                if gestrichen == kopf_text or gestrichen == kopf_text + " (Fortsetzung)":
                    kopf = nr
                    break
        if kopf is not None:
            aktuell = kopf
            stuecke.append((kopf, []))
        elif aktuell is not None:
            stuecke[-1][1].append(zeile)
    bloecke = {}
    for nr, zeilen in stuecke:
        text = leerraum_losch("\n".join(zeilen))
        bloecke[nr] = bloecke[nr] + " \x00 " + text if nr in bloecke else text
    return bloecke


def teile_kopf_ergaenzen(stuecke):
    """§11.15 Punkt 1: jeder Teil trägt die Überschrift jedes Blocks, von dem er
    Einheiten enthält. Die Überschrift wird wörtlich gesetzt — der Zusatz
    „(Fortsetzung)“ ist erlaubt, nicht nötig. Segmente, die ihre Überschrift
    selbst tragen (z. B. der UNKLAR-Block), bleiben unverändert.
    """
    erg = []
    aktueller = None
    for i, s in enumerate(stuecke):
        b = s.block
        if b != aktueller:
            aktueller = b
            if b is not None and b in BLOCK_UEBERSCHRIFTEN \
                    and not s.text.lstrip().startswith(BLOCK_UEBERSCHRIFTEN[b]) \
                    and any(x.ids for x in stuecke if x.block == b):
                erg.append(Segment(BLOCK_UEBERSCHRIFTEN[b] + "\n\n", block=b))
        erg.append(s)
    return erg


def manifest_json(projekt, ordner_abs, erzeugt, auftrag, quellen, einheiten,
                  befunde_json, teil, volltext_auswahl=(), jev=None,
                  merkzettel=None):
    man = {"format": 1, "projekt": projekt, "projektordner": ordner_abs,
           "erzeugt": erzeugt,
           "auftrag_sha256": sha256_hex(auftrag) if auftrag is not None else None,
           "stoppwoerter_sha": STOPPOERTER_SHA,
           "quellen": quellen, "einheiten": einheiten,
           "volltext_auswahl": list(volltext_auswahl),
           "befunde": befunde_json, "teil": teil}
    if jev is not None:
        man["jev"] = jev
    if merkzettel is not None:
        # §16.1 Punkt 5: benutzter Ordner (absolut) oder null + Grund
        man["merkzettel"] = merkzettel["pfad"]
        man["merkzettel_grund"] = merkzettel["grund"]
    return "```kontext-manifest\n" + json.dumps(man, ensure_ascii=False, indent=2) + "\n```"


def blockgroessen(seg):
    """Größe je Block (für die Meldung bei Exit 3, §11.8)."""
    bloecke = []
    for s in seg:
        erste = s.text.split("\n", 1)[0].strip()
        if erste.startswith("## "):
            bloecke.append((erste[3:].strip(), len(s.text)))
        elif not bloecke:
            bloecke.append(("Kopf (Block 1)", len(s.text)))
        else:
            name, n = bloecke[-1]
            bloecke[-1] = (name, n + len(s.text))
    return bloecke


def teile_packen(seg, max_zeichen, manifest_basis_laenge, einheiten, befunde,
                 quellen_laenge=0, befunde_laenge=0):
    """Segmente nur zwischen Einheiten auf Teile verteilen, jede ≤ max_zeichen.

    §11.8 und §11.12 Punkte 1/4: Jede Teildatei zählt komplett (Kopf, Teilvermerk,
    Block 9, Manifest). Jeder Teil beginnt mit dem Kopf (Block 1) und dem
    Teilvermerk; der UNKLAR-Block steht in Teil 1, Block 9 im letzten Teil.
    Das eigene Manifest je Teil wird beim Füllen reserviert (Grundlast einmal
    gemessen, je Einheit JSON-Länge plus Sicherheitsabstand; die vollen quellen
    und — seit §11.15 Punkt 7 — die volle Befundliste von Teil 1 gehen
    konservativ in jeden Teil ein).
    Rückgabe: je Teil die komplette Segmentliste.
    """
    einheit_laenge = {e["id"]: len(json.dumps(e, ensure_ascii=False)) + 8
                      for e in einheiten}
    teilkopf = len("TEIL 99 VON 99 — ohne die anderen Teile unvollständig\n\n")
    kopf_seg = next((s for s in seg if s.fixierung == "kopf"), None)
    erstes = [s for s in seg if s.fixierung == "erstes"]
    letztes = [s for s in seg if s.fixierung == "letztes"]
    frei = [s for s in seg if s.fixierung is None]
    fest = sum(len(s.text) for s in [kopf_seg, *erstes, *letztes] if s is not None)

    def groesse(mitte):
        ids = {i for s in mitte for i in s.ids}
        return (teilkopf + fest + kopf_reserve + sum(len(s.text) for s in mitte)
                + manifest_basis_laenge + quellen_laenge + befunde_laenge
                + sum(einheit_laenge.get(i, 300) for i in ids) + 128)

    # Reserve für Blocküberschriften, die ein Teil nach §11.15 Punkt 1 zusätzlich
    # trägt, wenn der Umbruch mitten in einen Block fällt
    kopf_reserve = sum(len(k) + 2 for k in BLOCK_UEBERSCHRIFTEN.values())

    teile, aktuell = [], []
    for s in frei:
        if aktuell and groesse(aktuell + [s]) > max_zeichen:
            teile.append(aktuell)
            aktuell = [s]
        else:
            aktuell.append(s)
    if aktuell:
        teile.append(aktuell)
    von = len(teile)
    komplett = []
    for k, mitte in enumerate(teile, 1):
        stuecke = []
        if kopf_seg is not None:
            stuecke.append(kopf_seg)
        if k == 1:
            stuecke += erstes
        stuecke += mitte
        if k == von:
            stuecke += letztes
        stuecke = teile_kopf_ergaenzen(stuecke)
        if groesse(mitte) > max_zeichen:
            for s in stuecke:
                if s.einheit_art:
                    befunde.append(Befund("EINHEIT_UEBER_GRENZE",
                                          f"{s.einheit_id} ({s.einheit_art}) größer als "
                                          f"--max-zeichen — eigener Teil, nicht gekürzt "
                                          f"(§11.8)"))
        komplett.append(stuecke)
    return komplett


def fundstellen_segment(kand, hat_bezug=True, reihenfolge_zeile=None):
    """Block 9: höchstens 15 Zeilen „Datei:Zeile — Überschrift“ (§11.5.9, §11.12.19)."""
    zeilen = ["## Zusatz — Fundstellen", ""]
    if not hat_bezug:
        zeilen.append("(kein Auftrag gegeben — Block entfällt, §11.7.4)")
    elif not kand:
        zeilen.append("(keine Kandidaten — Zusatz ist nie Pflicht, §11.7.4)")
    else:
        if reihenfolge_zeile:
            zeilen.append(reihenfolge_zeile)
            zeilen.append("")
        for a, _punkte in kand[:FUNDSTELLEN_MAX]:
            # §16.1 Punkt 3: Merkzettel-Fundstellen tragen den absoluten Pfad,
            # damit ein Agent in einer Arbeitskopie die Notiz findet.
            ort = str(a.pfad_abs) if a.merkzettel else a.anzeige
            zeilen.append(f"{ort}:{a.zeile} — {a.ueberschrift}")
    return Segment("\n".join(zeilen) + "\n", fixierung="letztes", block=9)


def auftrag_text(args):
    if getattr(args, "auftrag", None) is not None:
        return args.auftrag
    if getattr(args, "auftrag_datei", None):
        pfad = Path(args.auftrag_datei)
        if not pfad.is_file():
            raise Aufruffehler(f"Auftrag-Datei existiert nicht: {pfad}")
        return lies_datei(pfad).rstrip("\n")
    return None


def nummer_int(wert):
    w = wert.strip()
    if w.upper().startswith("E-"):
        w = w[2:]
    if not w.isdigit():
        raise Aufruffehler(f"--entscheidung erwartet E-NNN, bekommen: {wert!r}")
    return int(w)


# Tests ersetzen diesen Transport; der Standard bleibt Jevs eingebauter Transport.
JEV_TRANSPORT = None


def _lade_jev():
    """Lädt jev.py erst beim benötigten Live- oder Schlüsselprüfpfad (§14)."""
    name = "_kontextpaket_jev"
    if name in sys.modules:
        return sys.modules[name]
    pfad = Path(__file__).resolve().with_name("jev.py")
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return modul


def _jev_aus_grund(args):
    """Grundangabe für JEV_AUS — an beiden Meldungsstellen gleich (§14/§15.2)."""
    return "--ohne-jev" if args.ohne_jev else "KONTEXT.json"


def _jev_reihenfolge(ordner, dekl, auftrag, kand, args, befunde):
    """Entscheidet lokal oder Jev-Sortierung; Fehler beeinflussen nie den Exit."""
    aktiviert = dekl.jev and not args.ohne_jev
    zustand = {"aktiv": bool(aktiviert), "reihenfolge": "lokal",
               "status": None, "grund": None, "modell": None,
               "eingabe_token": 0, "kosten_usd": 0.0, "kontrolle": None,
               "zurueckgehalten": 0}
    if not auftrag or not kand:
        return kand, zustand, None

    if not aktiviert:
        grund = _jev_aus_grund(args)
        befunde.append(Befund("JEV_AUS", f"Jev aus: {grund} (§14 Punkt 1)", hart=False))
        return kand, zustand, f"Reihenfolge: lokal (Jev aus: {grund})"

    zeile = lambda grund: f"Reihenfolge: lokal (Jev: {grund})"
    try:
        jev = _lade_jev()
    except Exception:
        grund = "jev_laden"
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        zustand["grund"] = grund
        return kand, zustand, zeile(grund)

    if not os.environ.get("TYPESAFE_API_KEY"):
        grund = "kein_schluessel"
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        zustand["grund"] = grund
        return kand, zustand, zeile(grund)

    # §14.12 (B4): eine Format-Bauweise — der Jev-Pfad nutzt nur diese vier
    # Felder aus kandidat_json (quelle relativ, nie der absolute Pfad).
    kandidaten = [
        {f: eintrag[f] for f in ("id", "text", "ueberschrift", "quelle")}
        for eintrag in (kandidat_json(a, punkte) for a, punkte in kand)]
    scan = [("Auftrag", auftrag)]
    for kandidat in kandidaten:
        for feld in ("text", "ueberschrift", "quelle"):
            scan.append((f"{kandidat['id']}:{feld}", kandidat[feld]))
    treffer = jev.schluessel_treffer(scan)
    zurueck = []
    gesendet_kand = kand
    if treffer:
        # §14.11 Punkt 2: Treffer im Auftrag schaltet Jev ganz ab; Treffer
        # in einzelnen Kandidaten hält nur diese zurück — nichts entfernt.
        befunde.append(Befund("JEV_SCHLUESSELMUSTER",
                              "Schlüsselmuster erkannt; Fundstellen: " + ", ".join(treffer),
                              hart=False))
        grund = "schluesselmuster"
        if "Auftrag" in treffer:
            zustand["grund"] = grund
            return kand, zustand, zeile(grund)
        getroffen = {t.rsplit(":", 1)[0] for t in treffer}
        zurueck = [(a, punkte) for a, punkte in kand if a.id in getroffen]
        gesendet_kand = [(a, punkte) for a, punkte in kand
                         if a.id not in getroffen]
        zustand["zurueckgehalten"] = len(zurueck)
        kandidaten = [k for k in kandidaten if k["id"] not in getroffen]
        if not gesendet_kand:
            zustand["grund"] = grund
            return kand, zustand, zeile(grund)

    obergrenze = args.jev_obergrenze_usd
    try:
        schaetzung = jev.schaetze_kosten(auftrag, kandidaten, max_kandidaten=30)
        zustand["kosten_usd"] = float(schaetzung.get("usd_geschaetzt", 0.0))
    except Exception:
        grund = "schaetzung_fehler"
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        zustand["grund"] = grund
        return kand, zustand, zeile(grund)
    if zustand["kosten_usd"] > obergrenze:
        grund = "kostenobergrenze"
        zustand["grund"] = grund
        befunde.append(Befund("JEV_ZU_TEUER",
                              f"Jev nicht aufgerufen: geschätzte Kosten "
                              f"{zustand['kosten_usd']:.8f} $ über Obergrenze "
                              f"{obergrenze:.8f} $ (§14 Punkt 4d)", hart=False))
        return kand, zustand, zeile(grund)

    try:
        ordnung = jev.ordne_kandidaten(
            auftrag, kandidaten, max_kandidaten=30, kontrolle=True, live=True,
            transport=JEV_TRANSPORT)
    except ValueError:
        # §14.12 (B3): doppelte Kandidaten-ID ist ein Eingabefehler, kein
        # Transportfehler — eigener Grund, Rückfall bleibt lokal.
        grund = "doppelte_id"
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        zustand["grund"] = grund
        return kand, zustand, zeile(grund)
    except Exception:
        grund = "transport_fehler"
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        zustand["grund"] = grund
        return kand, zustand, zeile(grund)

    zustand.update(status=ordnung.status_gesamt, modell=ordnung.modell,
                    eingabe_token=int(ordnung.eingabe_token or 0))
    # §14 Punkt 7: nach dem Aufruf die echten Kosten statt der Schätzung (Orchestrator-Review U5-1)
    zustand["kosten_usd"] = zustand["eingabe_token"] * jev.PREIS_JE_MIO_TOKEN / 1_000_000
    kontrolle = ordnung.kontrolle or {}
    zustand["kontrolle"] = {"bestanden": kontrolle.get("bestanden")}
    if ordnung.status_gesamt in ("bewertet", "teilweise"):
        nach_id = {a.id: (a, punkte) for a, punkte in gesendet_kand}
        sortiert = [nach_id[e.id] for e in ordnung.eintraege if e.id in nach_id]
        if len(sortiert) == len(gesendet_kand) \
                and len({a.id for a, _ in sortiert}) == len(gesendet_kand):
            zustand["reihenfolge"] = "jev"
            anzahl = sum(1 for e in ordnung.eintraege if e.status == "bewertet")
            zeile_text = (f"Reihenfolge: Jev ({ordnung.modell or 'Modell unbekannt'}, "
                          f"{anzahl} von {len(kandidaten)} bewertet, Kontrolle "
                          f"{'bestanden' if kontrolle.get('bestanden') is True else 'nicht bestanden'}"
                          + (f", {len(zurueck)} zurückgehalten (Schlüsselmuster)"
                             if zurueck else "")
                          + ") — ordnet nur, entfernt nichts")
            # §14.11 Punkt 2: Zurückgehaltene stehen hinter allen gesendeten,
            # in lokaler Reihenfolge.
            return sortiert + zurueck, zustand, zeile_text
        grund = "ordnung_unvollstaendig"
        zustand["reihenfolge"], zustand["grund"] = "lokal", grund
        befunde.append(Befund("JEV_NICHT_BEWERTET", f"Jev nicht bewertet: {grund}", hart=False))
        return kand, zustand, zeile(grund)

    grund = "unbekannt"
    if ordnung.status_gesamt == "unzuverlaessig":
        grund = "kontrolle"
        art = "JEV_UNZUVERLAESSIG"
    else:
        for eintrag in ordnung.eintraege:
            if eintrag.grund:
                grund = str(eintrag.grund).split(":", 1)[0]
                break
        art = "JEV_NICHT_BEWERTET"
    zustand["grund"] = grund
    befunde.append(Befund(art, f"Jev nicht verwendet: {grund} (§14 Punkt 5)", hart=False))
    return kand, zustand, zeile(grund)


# --- Unterbefehl: bau ---------------------------------------------------------------

MERKZETTEL_VORGABE = ".config/orchestrated-team/memory"


def _merkzettel_quelle(args, dekl):
    """Welcher Merkzettel-Ordner gilt — die erste Regel, die greift (§16.1.2).

    Gibt (ordner|None, grund, fehlender_pfad|None) zurück. Ein fehlender
    Ordner hinter --merkzettel ist Aufruffehler (Exit 2, wie bei kandidaten);
    fehlt der Ordner aus Umgebung oder Vorgabe, ist das nur ein Hinweis.
    """
    if args.ohne_merkzettel:
        return None, "--ohne-merkzettel", None
    if dekl.merkzettel is False:
        return None, "KONTEXT.json", None
    if args.merkzettel:
        pfad = Path(args.merkzettel).expanduser()
        if not pfad.is_dir():
            raise Aufruffehler(f"Merkzettel-Ordner existiert nicht: {pfad}")
        return pfad.resolve(), "--merkzettel", None
    umgebung = os.environ.get("KONTEXTPAKET_MERKZETTEL")
    if umgebung:
        pfad = Path(umgebung).expanduser()
        if pfad.is_dir():
            return pfad.resolve(), "umgebung", None
        return None, "fehlt", pfad
    pfad = Path.home() / MERKZETTEL_VORGABE
    if pfad.is_dir():
        return pfad.resolve(), "vorgabe", None
    return None, "fehlt", pfad


def run_bau(args):
    ordner = Path(args.projektordner)
    if not ordner.is_dir():
        raise Aufruffehler(f"Projektordner existiert nicht: {ordner}")
    auftrag = auftrag_text(args)
    befunde = []
    dekl = lade_deklaration(ordner, befunde)
    if not dekl.vorhanden:
        befunde.append(Befund("KEINE_DEKLARATION",
                              f"keine {DEKLARATIONS_NAME} im Projektordner — Vorgaben "
                              f"wirken, Stand fehlt (§11.2.5)"))
    elif dekl.stand is None:
        # §11.12 Punkt 7: vorhandene KONTEXT.json ohne Schlüssel „stand“ ist
        # beim bau ein harter Befund (bei protokoll weich).
        befunde.append(Befund("STAND_NICHT_DEKLARIERT",
                              f"{DEKLARATIONS_NAME} deklariert keinen Stand — der "
                              f"Block „STAND NICHT DEKLARIERT“ verweist auf "
                              f"{dekl.protokoll} (§11.12 Punkt 7)"))
    merk_ordner, merk_grund, merk_fehlt = _merkzettel_quelle(args, dekl)
    if merk_fehlt is not None:
        print(f"kontextpaket bau: Hinweis — Merkzettel-Ordner {merk_fehlt} "
              "fehlt, ohne Merkzettel gebaut", file=sys.stderr)
    merk_info = {"pfad": str(merk_ordner) if merk_ordner else None,
                 "grund": merk_grund}
    p, stand, pflicht, gelesen = lies_quellen(ordner, dekl, befunde)
    haenge_zeile_text_an(befunde, gelesen)   # §11.15 Punkt 8
    woerter = suchwoerter(auftrag) if auftrag else []
    genannte = {nummer_int(w) for w in (args.entscheidung or [])}
    auswahl = volltext_auswahl(p, genannte, list(args.pfad or []), woerter) if p else []
    # §11.12 Punkt 19: genannte Nummer oder Pfad ohne Treffer → weicher Befund
    if p is not None:
        bekannte = {int(e.nummer[2:]) for e in p.entscheidungen}
        for nr in sorted(genannte):
            if nr not in bekannte:
                befunde.append(Befund("AUFTRAGSBEZUG_LEER",
                                      f"--entscheidung E-{nr:03d} gibt es nicht — "
                                      f"kein Volltext dafür (§11.12 Punkt 19)",
                                      hart=False))
        alle_pfadfelder = " ".join(e.feld("Betroffene Pfade") or ""
                                   for e in p.entscheidungen)
        for pfad in (args.pfad or []):
            if pfad not in alle_pfadfelder:
                befunde.append(Befund("AUFTRAGSBEZUG_LEER",
                                      f"--pfad {pfad} trifft kein „Betroffene Pfade“-"
                                      f"Feld — kein Volltext dafür (§11.12 Punkt 19)",
                                      hart=False))
    einheiten = einheiten_liste(p, stand, pflicht, dekl.protokoll,
                                {e for e, _g in auswahl})
    # §11.12 Punkt 19: „Ohne Auftrag“ heißt ohne jeden Volltext-Bezug —
    # --entscheidung/--pfad bleiben auch ohne Auftrag wirksam (§11.5.6 a/b).
    hat_bezug = bool(auftrag) or bool(genannte) or bool(args.pfad)
    quellen = quellen_liste(ordner, dekl, gelesen, pflicht)
    seg = baue_segmente(ordner, dekl, p, stand, pflicht, auftrag, woerter, auswahl,
                        befunde, hat_bezug=hat_bezug)
    kand = kandidaten_liste(zusatz_abschnitte(ordner, dekl, merk_ordner),
                            woerter)
    kand, jev_status, reihenfolge_zeile = _jev_reihenfolge(
        ordner, dekl, auftrag, kand, args, befunde)
    seg.append(fundstellen_segment(kand, hat_bezug=hat_bezug,
                                   reihenfolge_zeile=reihenfolge_zeile))

    ordner_abs = str(ordner.resolve())
    erzeugt = datetime.now().astimezone().isoformat(timespec="seconds")
    # §11.15 Punkt 10: das ungeteilte Manifest bzw. Teil 1 trägt die Auswahl
    volltext_nrn = [e.nummer for e, _grund in auswahl]

    def man_basis_fn():
        return (dekl.projekt, ordner_abs, erzeugt, auftrag, quellen,
                [b.json_dict() for b in befunde])

    def ganzes(teil=None, einheiten_teil=None):
        projekt, o_abs, ez, af, qu, be = man_basis_fn()
        koerper = "".join(s.text for s in seg).replace("{ZEIT}", ez)
        teil1 = teil is None or teil.get("nr") == 1
        return koerper + "\n" + manifest_json(projekt, o_abs, ez, af, qu,
                                              einheiten_teil or einheiten, be,
                                              teil,
                                              volltext_auswahl=volltext_nrn,
                                              jev=jev_status if teil1 else None,
                                              merkzettel=merk_info if teil1 else None) + "\n"

    grenze = args.max_zeichen
    # §11.12 Punkt 1 (B1): ohne --ausgabe misst --max-zeichen das GANZE Paket
    # (Blöcke 1–10 inklusive Manifest); mit --ausgabe muss jede Teildatei
    # komplett (Kopf, Teilvermerk, Block 9, Manifest) unter die Grenze.
    ueber_grenze = grenze is not None and len(ganzes()) > grenze
    if ueber_grenze and not args.ausgabe:
        print(f"kontextpaket: Paket {len(ganzes())} Zeichen "
              f"über der Grenze {grenze} (§11.8, §11.12 Punkt 1):", file=sys.stderr)
        for name, n in blockgroessen(seg):
            print(f"  Block „{name}“: {n} Zeichen", file=sys.stderr)
        print("  Vorschlag: --ausgabe ORDNER — das Paket wird geteilt, nie abgeschnitten.",
              file=sys.stderr)
        return 3

    if args.ausgabe:
        aus = Path(args.ausgabe)
        try:
            aus.resolve().relative_to(ordner.resolve())
        except ValueError:
            pass
        else:
            raise Aufruffehler(f"--ausgabe liegt innerhalb des Projektordners: {aus} (§11.1)")
        if aus.exists() and any(aus.iterdir()):
            raise Aufruffehler(f"--ausgabe existiert und ist nicht leer: {aus}")
        aus.mkdir(parents=True, exist_ok=True)
        if ueber_grenze:
            projekt, o_abs, ez, af, _qu, _be = man_basis_fn()
            # Teil-Manifeste sind schlank (§11.13 Auslegung 2): je Teil projekt,
            # erzeugt, teil, stoppwoerter_sha; die vollen quellen und die volle
            # befunde-Liste (§11.15 Punkt 7) stehen nur in Teil 1 — aber die
            # Länge beider geht konservativ in jeden Teil ein, sonst könnte
            # keine Teildatei die Grenze einhalten.
            grundlast = len(manifest_json(projekt, o_abs, ez, af, [], [], [],
                                          {"nr": 1, "von": 1}, jev=jev_status,
                                          merkzettel=merk_info))
            quellen_laenge = len(json.dumps(quellen, ensure_ascii=False, indent=2))
            befunde_laenge = len(json.dumps(_be, ensure_ascii=False, indent=2))
            teile = teile_packen(seg, grenze, grundlast, einheiten, befunde,
                                 quellen_laenge=quellen_laenge,
                                 befunde_laenge=befunde_laenge)
            texte = []
            von = len(teile)
            vergeben = set()   # §11.15 Punkt 10: jede Kennung steht nur im
            # ersten Teil, der sie trägt — sonst wäre die Vereinigung der
            # Manifeste nicht eindeutig (EINHEIT_DOPPELT bei pruefe).
            for k, segs in enumerate(teile, 1):
                ids = []
                for s in segs:
                    for i in s.ids:
                        if i not in ids and i not in vergeben:
                            ids.append(i)
                vergeben.update(ids)
                e_teil = [e for e in einheiten if e["id"] in ids]
                projekt, o_abs, ez, af, _qu, _be = man_basis_fn()
                koerper = "".join(s.text for s in segs).replace("{ZEIT}", ez)
                kopf = f"TEIL {k} VON {von} — ohne die anderen Teile unvollständig\n\n"
                texte.append(kopf + koerper + "\n" + manifest_json(
                    projekt, o_abs, ez, af, quellen if k == 1 else [], e_teil,
                    _be if k == 1 else [],
                    {"nr": k, "von": von},
                    volltext_auswahl=volltext_nrn if k == 1 else [],
                    jev=jev_status if k == 1 else None,
                    merkzettel=merk_info if k == 1 else None) + "\n")
            for k, t in enumerate(texte, 1):
                (aus / f"paket-{k}-von-{von}.md").write_text(t, encoding="utf-8")
            print(f"kontextpaket: {von} Teil(e) geschrieben nach {aus} "
                  f"(geteilt, nie abgeschnitten — §11.8)", file=sys.stderr)
        else:
            (aus / "paket.md").write_text(ganzes(), encoding="utf-8")
            print(f"kontextpaket: Paket geschrieben: {aus / 'paket.md'}", file=sys.stderr)
    else:
        sys.stdout.write(ganzes())

    harte = sum(1 for b in befunde if b.hart)
    print(f"kontextpaket bau: {harte} harte, {len(befunde) - harte} weiche Befunde "
          f"(siehe Manifest)", file=sys.stderr)
    if auftrag and kand:
        if not jev_status["aktiv"]:
            # §15.2 Punkt 2: bei abgeschaltetem Jev nennt die stderr-Zeile
            # den Grund — Manifest, Paketzeile und Exit bleiben unverändert.
            print(f"kontextpaket bau: Jev aus ({_jev_aus_grund(args)}) — "
                  f"Reihenfolge lokal, 0 $", file=sys.stderr)
        else:
            status_text = (jev_status["status"] or jev_status["grund"]
                           or "nicht_bewertet")
            print(f"kontextpaket bau: Jev — Reihenfolge "
                  f"{jev_status['reihenfolge']} ({status_text}), "
                  f"{jev_status['kosten_usd']:.8f} $", file=sys.stderr)
    for b in befunde_sortieren(befunde):
        print(b.zeile_zeile(), file=sys.stderr)
    return 1 if harte else 0


# --- Unterbefehl: pruefe -------------------------------------------------------------

def run_pruefe(args):
    paket = Path(args.paketdatei)
    if not paket.is_file():
        raise Aufruffehler(f"Paketdatei existiert nicht: {paket}")
    text = lies_datei(paket)
    roh_bloecke = MANIFEST_BLOCK_MUSTER.findall(text)
    if not roh_bloecke:
        raise Aufruffehler(f"Paketdatei ohne kontext-manifest-Block: {paket} (§11.9)")
    manifeste = []
    for roh in roh_bloecke:
        try:
            m_roh = json.loads(roh)
        except ValueError as e:
            raise Aufruffehler(f"Manifest in {paket} kein gültiges JSON: {e}")
        if m_roh.get("format") != 1:
            raise Aufruffehler(f"Manifest in {paket} hat unbekanntes format "
                               f"{m_roh.get('format')!r}, erwartet 1 (§11.12 Punkt 18)")
        manifeste.append(m_roh)

    befunde = []
    teil_manifeste = [m for m in manifeste if m.get("teil")]
    quell_manifeste = []    # alle Manifeste, aus denen dieses Paket besteht
    if teil_manifeste:
        man0 = teil_manifeste[0]
        von = man0["teil"].get("von")
        # §11.12 Punkt 4 (B4): Teile gehören zusammen, wenn sie im selben Ordner
        # liegen und ihre Manifeste gleiches projekt, gleiches erzeugt und
        # gleiches teil.von tragen — der Dateiname ist egal. Fehlt eine Nummer
        # 1…n → TEIL_FEHLT.
        schlessel = (man0.get("projekt"), man0.get("erzeugt"), von)
        gefundene_teile = {}
        for nachbar in sorted(paket.parent.glob("*.md")):
            try:
                nachbar_text = lies_datei(nachbar)
            except Aufruffehler:
                continue
            for roh2 in MANIFEST_BLOCK_MUSTER.findall(nachbar_text):
                try:
                    m2 = json.loads(roh2)
                except ValueError:
                    continue
                if not m2.get("teil"):
                    continue
                if (m2.get("projekt"), m2.get("erzeugt"),
                        m2.get("teil", {}).get("von")) == schlessel:
                    gefundene_teile.setdefault(m2["teil"].get("nr"), nachbar_text)
        koerper = ""
        man_einheiten, gesehen = [], set()
        for k in range(1, (von or 0) + 1):
            t = gefundene_teile.get(k)
            if t is None:
                befunde.append(Befund("TEIL_FEHLT",
                                      f"Teil {k} von {von} fehlt — gesucht über gleiche "
                                      f"Manifeste (projekt, erzeugt, teil.von), Dateiname "
                                      f"egal (§11.12 Punkt 4)"))
                continue
            koerper += MANIFEST_BLOCK_MUSTER.sub("", t)
            for roh2 in MANIFEST_BLOCK_MUSTER.findall(t):
                try:
                    m2 = json.loads(roh2)
                except ValueError:
                    continue
                quell_manifeste.append(m2)
                for u in m2.get("einheiten", []):
                    if u["id"] not in gesehen:
                        gesehen.add(u["id"])
                        man_einheiten.append(u)
        # §11.15 Punkt 7: quellen, befunde und volltext_auswahl stehen in Teil 1 —
        # geprüft wird also gegen Teil 1, egal welcher Teil übergeben wurde.
        man = next((m2 for m2 in quell_manifeste
                    if m2.get("teil", {}).get("nr") == 1), man0)
    else:
        man = manifeste[-1]
        quell_manifeste = [man]
        man_einheiten = man.get("einheiten", [])
        koerper = MANIFEST_BLOCK_MUSTER.sub("", text)

    # §11.15 Punkt 10 (R1-08): jede Kennung kommt über alle Manifeste des
    # Pakets genau einmal vor — sonst harter Befund EINHEIT_DOPPELT.
    id_anzahl, id_erste = {}, {}
    for m2 in quell_manifeste:
        for u in m2.get("einheiten", []):
            uid = u.get("id")
            id_anzahl[uid] = id_anzahl.get(uid, 0) + 1
            id_erste.setdefault(uid, u)
    for uid in sorted(id_anzahl, key=str):
        if id_anzahl[uid] > 1:
            u = id_erste[uid]
            befunde.append(Befund("EINHEIT_DOPPELT",
                                  f"{uid} steht {id_anzahl[uid]}× in den Manifesten — "
                                  f"jede Einheit genau einmal (§11.15 Punkt 10)",
                                  u.get("datei"), u.get("zeile")))

    projekt = Path(args.projekt) if args.projekt else Path(man["projektordner"])
    if not projekt.is_dir():
        raise Aufruffehler(f"Projektordner existiert nicht: {projekt} "
                           f"(--projekt oder Manifest)")
    quellen_befunde = []
    dekl = lade_deklaration(projekt, quellen_befunde)
    if not dekl.vorhanden:
        quellen_befunde.append(Befund("KEINE_DEKLARATION",
                                      f"keine {DEKLARATIONS_NAME} im Projektordner — "
                                      f"Vorgaben wirken (§11.2.5)"))
    elif dekl.stand is None:
        quellen_befunde.append(Befund("STAND_NICHT_DEKLARIERT",
                                      f"{DEKLARATIONS_NAME} deklariert keinen Stand "
                                      f"(§11.12 Punkt 7)"))
    p, stand, pflicht, gelesen = lies_quellen(projekt, dekl, quellen_befunde)
    haenge_zeile_text_an(quellen_befunde, gelesen)   # §11.15 Punkt 8
    # Quellen-Drift (§11.15 Punkt 6): die Deklaration bestimmt, was Pflicht ist —
    # weicht KONTEXT.json ab (oder ist hinzugekommen/verschwunden), ist das hart;
    # jede andere Quelle nur weicher Hinweis, die Einheitenprüfung entscheidet.
    aktuelle_quellen = quellen_liste(projekt, dekl, gelesen, pflicht)
    jetzt_sha = {q["datei"]: q["sha256"] for q in aktuelle_quellen}
    manifest_sha = {q.get("datei"): q.get("sha256")
                    for q in man.get("quellen") or []}
    for name in sorted(set(jetzt_sha) | set(manifest_sha)):
        if jetzt_sha.get(name) == manifest_sha.get(name):
            continue
        if name == DEKLARATIONS_NAME:
            befunde.append(Befund("DEKLARATION_GEAENDERT",
                                  f"{name} hat sich seit dem Bau geändert — die "
                                  f"Deklaration bestimmt, was Pflicht ist (§11.15 "
                                  f"Punkt 6)", name))
        else:
            befunde.append(Befund("QUELLE_GEAENDERT",
                                  f"Quelle {name} hat sich seit dem Bau geändert — "
                                  f"die Einheitenprüfung entscheidet, ob das Paket "
                                  f"veraltet ist (§11.15 Punkt 6)", name, hart=False))
    # §11.13 Auslegung 4 (Mittelweg, ersetzt §11.12 Punkt 3 Satz 2), mit
    # §11.15 Punkt 8: alle Parser-Befunde werden beim Neu-Einlesen ausgegeben;
    # hart (Exit 1) sind nur die, deren Fundstelle (Art + Datei + Wortlaut der
    # Zeile) nicht schon im Manifest stand — bekannte stehen als weicher Hinweis
    # „schon beim Bau bekannt“ da. Die Zeilennummer ist nur Fundstelle.
    # QUELLE_FEHLT und CODEBLOCK_NIE_GESCHLOSSEN sind immer hart (§11.12 Punkt 3).
    alt = {(b.get("art"), b.get("datei"), b.get("zeile_text"))
           for b in man.get("befunde", [])}
    immer_hart = {"QUELLE_FEHLT", "CODEBLOCK_NIE_GESCHLOSSEN"}
    for b in quellen_befunde:
        bekannt = (b.art, b.datei, b.zeile_text) in alt
        if bekannt and b.art not in immer_hart:
            b.hart = False
            b.text += " — schon beim Bau bekannt"
        elif bekannt:
            b.text += " — schon beim Bau bekannt"
        befunde.append(b)
    neue = einheiten_liste(p, stand, pflicht, dekl.protokoll, set())
    src = {u["id"]: u for u in neue}
    man_ids = {u["id"] for u in man_einheiten}
    bloecke = paket_bloecke(koerper)   # §11.15 Punkt 1: blockbezogene Textprüfung

    # EINHEIT_FEHLT: heute geltende Einheiten müssen im Manifest stehen (§11.9)
    for u in neue:
        geltend = ((u["art"] == "bedingung" and u["status"] == "gilt")
                   or (u["art"] == "entscheidung" and u["status"] in ("gültig", "unklar"))
                   or u["art"] in ("stand", "pflicht_zusatz", "anmerkung",
                                   "einleitung", "unklar"))
        if geltend and u["id"] not in man_ids:
            befunde.append(Befund("EINHEIT_FEHLT",
                                  f"{u['id']} ({u['art']}) steht nicht im Manifest — neu "
                                  f"hinzugekommen oder entfernt (§11.9)", u["datei"],
                                  u["zeile"]))

    for u in man_einheiten:
        if u["art"] in ("volltext", "unklar"):
            # §11.15 Punkt 10: der Volltext hängt an der E-NNN-Einheit (Status,
            # Wortlaut); seine Anwesenheit im Körper wird separat geprüft.
            # §11.15 Punkt 2: verschwindet eine UNKLAR-Zeile aus der Quelle, ist
            # das kein Fehler — dann greift der Parser-Befund-Vergleich (Punkt 8).
            continue
        s = src.get(u["id"])
        if s is None:
            befunde.append(Befund("EINHEIT_FEHLT",
                                  f"{u['id']} steht im Manifest, ist in der Quelle aber "
                                  f"verschwunden (§11.9)", u.get("datei"), u.get("zeile")))
            continue
        if s.get("status") != u.get("status"):
            befunde.append(Befund("STATUS_GEAENDERT",
                                  f"{u['id']}: Manifest „{u.get('status')}“, Quelle jetzt "
                                  f"„{s.get('status')}“ (§11.9)", s["datei"], s["zeile"]))
        if u["art"] == "stand":
            if s["sha"] != u["sha"]:
                befunde.append(Befund("STAND_VERALTET",
                                      f"Stand-Hash weicht ab (Manifest {u['sha']}, Quelle "
                                      f"{s['sha']}) (§11.9)", s["datei"], s["zeile"]))
            continue
        if s["sha"] != u["sha"]:
            befunde.append(Befund("EINHEIT_GEAENDERT",
                                  f"{u['id']}: Wortlaut geändert (Manifest {u['sha']}, "
                                  f"Quelle {s['sha']}) (§11.9)", s["datei"], s["zeile"]))

    # TEXT_FEHLT (Gate 1), blockbezogen (§11.15 Punkt 1): der Pflichttext jeder
    # Einheit wird nur in ihrem eigenen Block gesucht — eine Kopie in einem
    # anderen Block (Köder) reicht nicht. Tragen mehrere Einheiten eines Blocks
    # denselben normalisierten Text, muss er dort mindestens so oft vorkommen,
    # wie es solche Einheiten gibt. Fehlt der ganze Block, meldet BLOCK_FEHLT
    # ihn einmal, statt je Einheit TEXT_FEHLT zu werfen.
    brauch = {}      # (blocknr, normalisierter Text) -> nötige Vorkommen
    ausloeser = {}   # (blocknr, Text) -> (Meldung, datei, zeile) des ersten Auslösers

    def fordere(nr, text, meldung, datei, zeile):
        key = (nr, leerraum_losch(text))
        brauch[key] = brauch.get(key, 0) + 1
        ausloeser.setdefault(key, (meldung, datei, zeile))

    if p is not None:
        for b in p.bedingungen:
            if b.gilt and b.kennung in man_ids:
                fordere(4, b.text(),
                        f"{b.kennung} (Bedingung): Eintragstext steht nicht im Block "
                        f"„{BLOCK_UEBERSCHRIFTEN[4][3:]}“ — gelöscht oder "
                        f"umformuliert? (§11.9, §11.15 Punkt 1)",
                        dekl.protokoll, b.zeile)
        # Anmerkungen prüft pruefe wie Bedingungen (§11.13 Punkt 2)
        for a in p.anmerkungen:
            if a.kennung in man_ids:
                fordere(4, a.text(),
                        f"{a.kennung} (Anmerkung): Text steht nicht im Block "
                        f"„{BLOCK_UEBERSCHRIFTEN[4][3:]}“ — gelöscht oder "
                        f"umformuliert? (§11.13, §11.15 Punkt 1)",
                        dekl.protokoll, a.zeile)
        # Einleitungen prüft pruefe wie Anmerkungen (§11.15 Punkt 3)
        for abs_nr, einleitung in p.bedingungs_einleitungen:
            iid = einleitungs_kennung(einleitung)
            if iid in man_ids:
                fordere(4, "\n".join(einleitung),
                        f"{iid} (Einleitung): Text steht nicht im Block "
                        f"„{BLOCK_UEBERSCHRIFTEN[4][3:]}“ — gelöscht oder "
                        f"umformuliert? (§11.15 Punkt 3)",
                        dekl.protokoll, abs_nr + 1)
        # UNKLAR-Zeilen sind Einheiten; ihre wörtliche Zeile gehört in Block 2
        # (§11.15 Punkt 2)
        for zeile_nr, text in p.fremdzeilen:
            uid = unklar_kennung(text)
            if uid in man_ids:
                fordere(2, text,
                        f"{uid} (UNKLAR): Zeile steht wörtlich nicht in Block 2 "
                        f"(§11.15 Punkt 2)", dekl.protokoll, zeile_nr)
        # §11.12 Punkt 2 (B2), mit §11.15 Punkt 10: die Auswahl steht in
        # volltext_auswahl (Teil 1 bzw. ungeteilt); zu jedem Eintrag muss die
        # Volltext-Einheit in der Vereinigung existieren, und ihr Kopf+Rumpf
        # (leerraum-normalisiert) muss in Block 6 stehen.
        ents_nach_nummer = {e.nummer: e for e in p.entscheidungen}
        for nr_txt in man.get("volltext_auswahl") or []:
            if f"{nr_txt}/volltext" not in man_ids:
                e = ents_nach_nummer.get(nr_txt)
                befunde.append(Befund("EINHEIT_FEHLT",
                                      f"{nr_txt}: volltext_auswahl nennt die Entscheidung, "
                                      f"aber die Volltext-Einheit {nr_txt}/volltext fehlt "
                                      f"in der Vereinigung der Manifeste (§11.15 Punkt 10)",
                                      dekl.protokoll, e.zeile if e else None))
        for u in man_einheiten:
            if u.get("art") != "volltext":
                continue
            e = ents_nach_nummer.get(u["id"][:-len("/volltext")])
            if e is not None:
                fordere(6, normalisierte_zeilen([e.kopf_zeile] + e.rumpf_zeilen),
                        f"{u['id']}: Volltext (Kopf+Rumpf) steht nicht in Block 6 "
                        f"(§11.12 Punkt 2, §11.15 Punkt 10)", dekl.protokoll, e.zeile)
        for e in p.entscheidungen:
            if e.status in ("gültig", None) and e.nummer in man_ids:
                # ohne den Zeilen-Anhang vergleichen: Zeilen verschieben sich, sobald
                # oben im Protokoll etwas wächst — das ist kein TEXT_FEHLT (§11.9)
                probe = re.sub(r"\s*·\s*Zeile \d+\s*$", "", uebersichtszeile(e))
                fordere(5, probe,
                        f"{e.nummer} (Entscheidung): Übersichtszeile steht nicht in "
                        f"Block 5 (§11.9, §11.15 Punkt 1)", dekl.protokoll, e.zeile)
    if stand is not None and "STAND" in man_ids:
        st_text = normalisierte_zeilen(stand.zeilen)
        if stand.zeilen and stand.zeilen[0].strip() in BLOCK_UEBERSCHRIFTEN.values():
            # Die erste Standzeile ist zugleich feste Blocküberschrift — sie startet
            # im Paketkörper einen eigenen Block und kann dort nie zusammenhängend
            # mit dem Inhalt geprüft werden; die Prüfung zielt auf den Standinhalt
            # ab Zeile 2 (§11.15 Punkt 1).
            st_text = normalisierte_zeilen(stand.zeilen[1:])
        fordere(3, st_text,
                "STAND: Standtext steht nicht in Block 3 (§11.9, §11.15 Punkt 1)",
                stand.datei, stand.start)
    for rel, inhalt in pflicht:
        if f"PZ-{rel}" in man_ids:
            fordere(8, normalisierte_zeilen(inhalt.splitlines()),
                    f"PZ-{rel}: Text der Pflicht-Zusatzdatei steht nicht in Block 8 "
                    f"(§11.9, §11.15 Punkt 1)", rel, None)

    for nr in sorted({nr for nr, _t in brauch} - set(bloecke)):
        meldung, datei, zeile = next(a for (nr2, _t), a in ausloeser.items()
                                     if nr2 == nr)
        befunde.append(Befund("BLOCK_FEHLT",
                              f"Block {nr} ({BLOCK_UEBERSCHRIFTEN[nr][3:]}) fehlt im "
                              f"Paketkörper, obwohl Einheiten daraus im Manifest stehen "
                              f"(§11.15 Punkt 1)", datei, zeile))
    for (nr, text), n in sorted(brauch.items()):
        if nr not in bloecke:
            continue   # BLOCK_FEHLT deckt den ganzen Block ab
        if bloecke[nr].count(text) < n:
            meldung, datei, zeile = ausloeser[(nr, text)]
            befunde.append(Befund("TEXT_FEHLT", meldung, datei, zeile))

    harte = [b for b in befunde if b.hart]
    if not harte:
        print("kontextpaket pruefe: OK — Paket vollständig und aktuell (0 harte Befunde)",
              file=sys.stderr)
    else:
        print(f"kontextpaket pruefe: {len(harte)} harte Befunde:", file=sys.stderr)
    for b in befunde_sortieren(befunde):
        print(b.zeile_zeile(), file=sys.stderr)
    return 1 if harte else 0


# --- Unterbefehl: protokoll -----------------------------------------------------------

def run_protokoll(args):
    if args.alle:
        repos_pfad = Path(args.repos) if args.repos else STANDARD_REPOS_JSON
        try:
            daten = json.loads(repos_pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Aufruffehler(f"repos.json nicht lesbar ({repos_pfad}): {e}")
        ordner_liste = [Path(e["pfad"]) for e in daten.get("repos", [])]
    elif args.projektordner:
        ordner_liste = [Path(pfad) for pfad in args.projektordner]
    else:
        raise Aufruffehler("protokoll braucht Projektordner oder --alle")
    if not ordner_liste:
        raise Aufruffehler("keine Projektordner angegeben")

    harte_gesamt = 0
    for ordner in ordner_liste:
        if not ordner.is_dir():
            print(f"Projekt: {ordner} — übersprungen: Ordner existiert nicht")
            continue
        befunde = []
        dekl = lade_deklaration(ordner, befunde)
        if not dekl.vorhanden:
            befunde.append(Befund("KEINE_DEKLARATION",
                                  f"keine {DEKLARATIONS_NAME} — Vorgaben wirken; Altprojekt "
                                  f"wird beim nächsten Einstieg übernommen (§11.2.5)",
                                  hart=False))
        elif dekl.stand is None:
            # §11.12 Punkt 7: bei protokoll weich (bei bau hart)
            befunde.append(Befund("STAND_NICHT_DEKLARIERT",
                                  f"{DEKLARATIONS_NAME} deklariert keinen Stand (§11.12 "
                                  f"Punkt 7)", hart=False))
        if args.alle and not (ordner / dekl.protokoll).is_file():
            print(f"Projekt: {dekl.projekt} ({ordner}) — übersprungen: keine "
                  f"Protokolldatei {dekl.protokoll} (§11.10)")
            continue
        p, stand, pflicht, _gelesen = lies_quellen(ordner, dekl, befunde,
                                                   ohne_ende_hart=False)
        if p is not None and not p.bedingungen and not p.entscheidungen:
            befunde.append(Befund("KEINE_LISTEN",
                                  "weder Bedingungen noch Entscheidungen gefunden (§11.10)",
                                  hart=False))
        bed_gilt = sum(1 for b in p.bedingungen if b.gilt) if p else 0
        e_gueltig = sum(1 for e in p.entscheidungen if e.status == "gültig") if p else 0
        e_nicht = sum(1 for e in p.entscheidungen if e.status == "nicht gültig") if p else 0
        e_unklar = sum(1 for e in p.entscheidungen if e.status is None) if p else 0
        print(f"Projekt: {dekl.projekt} ({ordner})")
        print(f"  Bedingungen: {bed_gilt} gelten, "
              f"{(len(p.bedingungen) if p else 0) - bed_gilt} aufgehoben")
        print(f"  Entscheidungen: {e_gueltig} gültig, {e_nicht} nicht gültig, "
              f"{e_unklar} unklar")
        if befunde:
            print("  Befunde: siehe stderr")
            for b in befunde_sortieren(befunde):
                print(f"kontextpaket protokoll {dekl.projekt}: {b.zeile_zeile()}",
                      file=sys.stderr)
        else:
            print("  OK: keine Befunde")
        harte_gesamt += sum(1 for b in befunde if b.hart)
    return 1 if harte_gesamt else 0


# --- Unterbefehl: kandidaten -----------------------------------------------------------

def run_kandidaten(args):
    ordner = Path(args.projektordner)
    if not ordner.is_dir():
        raise Aufruffehler(f"Projektordner existiert nicht: {ordner}")
    auftrag = auftrag_text(args)
    befunde = []
    dekl = lade_deklaration(ordner, befunde)
    merk = Path(args.merkzettel) if args.merkzettel else None
    if not dekl.vorhanden:
        print(f"kontextpaket kandidaten: Hinweis — keine {DEKLARATIONS_NAME}, keine "
              f"zusatz-Quellen deklariert (§11.2.5)", file=sys.stderr)
    abschnitte = zusatz_abschnitte(ordner, dekl, merk)
    woerter = suchwoerter(auftrag or "")
    if not woerter:
        print("kontextpaket kandidaten: Hinweis — Auftrag liefert keine Suchwörter, "
              "damit gibt es keine Kandidaten (§11.7.2)", file=sys.stderr)
    kand = kandidaten_liste(abschnitte, woerter)
    ausgabe = {"format": 1, "auftrag": auftrag, "kandidaten": [
        kandidat_json(a, punkte) for a, punkte in kand]}
    print(json.dumps(ausgabe, ensure_ascii=False, indent=2))
    # §11.15 Punkt 11: Deklarationsbefunde auf stderr wie bau; hart → Exit 1,
    # die Ausgabe kommt trotzdem.
    harte = [b for b in befunde if b.hart]
    if not harte:
        if befunde:
            print("kontextpaket kandidaten: 0 harte Befunde:", file=sys.stderr)
    else:
        print(f"kontextpaket kandidaten: {len(harte)} harte Befunde:",
              file=sys.stderr)
    for b in befunde_sortieren(befunde):
        print(f"kontextpaket kandidaten: {b.zeile_zeile()}", file=sys.stderr)
    return 1 if harte else 0


# --- Kommandozeile ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kontextpaket.py",
        description="Kontextpaket: Pflichtkontext vollständig, belegt und prüfbar "
                    "(Vertrag §11). Liest nur, ändert nichts in Projekten.")
    sub = parser.add_subparsers(dest="befehl", required=True)

    p_bau = sub.add_parser("bau", help="Paket für einen Projektordner bauen")
    p_bau.add_argument("projektordner", metavar="PROJEKTORDNER")
    gruppe = p_bau.add_mutually_exclusive_group()
    gruppe.add_argument("--auftrag", metavar="TEXT")
    gruppe.add_argument("--auftrag-datei", metavar="DATEI")
    p_bau.add_argument("--entscheidung", metavar="E-NNN", action="append")
    p_bau.add_argument("--pfad", metavar="PFAD", action="append")
    p_bau.add_argument("--max-zeichen", type=int, metavar="N")
    p_bau.add_argument("--ausgabe", metavar="ORDNER")
    p_bau.add_argument("--ohne-jev", action="store_true",
                       help="Jev für diesen Bau nicht verwenden.")
    p_bau.add_argument("--jev-obergrenze-usd", type=float, default=0.02,
                       metavar="USD", help="Jev-Kostenobergrenze (Standard: 0.02).")
    gruppe_m = p_bau.add_mutually_exclusive_group()
    gruppe_m.add_argument("--merkzettel", metavar="ORDNER",
                          help="Diesen Merkzettel-Ordner verwenden (§16.1).")
    gruppe_m.add_argument("--ohne-merkzettel", action="store_true",
                          help="Merkzettel für diesen Bau nicht verwenden.")

    p_pruefe = sub.add_parser("pruefe", help="ist ein Paket noch vollständig und aktuell?")
    p_pruefe.add_argument("paketdatei", metavar="PAKETDATEI")
    p_pruefe.add_argument("--projekt", metavar="PROJEKTORDNER")

    p_prot = sub.add_parser("protokoll", help="Form der Listen in allen Projekten berichten")
    p_prot.add_argument("projektordner", nargs="*", metavar="PROJEKTORDNER")
    p_prot.add_argument("--alle", action="store_true")
    p_prot.add_argument("--repos", metavar="DATEI")

    p_kand = sub.add_parser("kandidaten", help="Zusatz-Kandidaten für einen Auftrag (§12)")
    p_kand.add_argument("projektordner", metavar="PROJEKTORDNER")
    gruppe_k = p_kand.add_mutually_exclusive_group(required=True)
    gruppe_k.add_argument("--auftrag", metavar="TEXT")
    gruppe_k.add_argument("--auftrag-datei", metavar="DATEI")
    p_kand.add_argument("--merkzettel", metavar="ORDNER")

    args = parser.parse_args(argv)
    laeufe = {"bau": run_bau, "pruefe": run_pruefe,
              "protokoll": run_protokoll, "kandidaten": run_kandidaten}
    try:
        return laeufe[args.befehl](args)
    except Aufruffehler as e:
        print(f"kontextpaket: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

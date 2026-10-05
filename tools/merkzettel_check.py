"""Merkzettel-Check: hält die Form des Gedächtnisses gesund — rein lesend
(Vertrag: orchestriertes-team/VERTRAG.md §10, besonders §10.4 und §10.7).

    python3 tools/merkzettel_check.py [ORDNER]

Ohne Argument gilt der Merkzettel-Ordner aus §10.1
(`~/.config/orchestrated-team/memory`). Der Unterordner
`archiv/` zählt nicht zum Bestand (§10.6); er wird weder bei Index noch
Verweisen noch Pfaden geprüft. Verweise **auf** archivierte Notizen gelten
als tot — sie soll ja nicht mehr benutzt werden.

Harte Befunde (Exit 1, jeder einzeln mit Datei und Zeile benannt, §10.4):
  KOPF_UNVOLLSTAENDIG · NAME_UNGLEICH_DATEI · VERWAIST ·
  INDEX_ZEIGT_INS_LEERE · VERWEIS_TOT · VERWEIS_MIT_ENDUNG ·
  VERWEIS_KEIN_SLUG · INDEX_ZU_GROSS · PFAD_TOT
Weiche Befunde (nur Bericht, Exit bleibt 0):
  INDEX_FUELLSTAND · ALTERUNGS_SIGNAL · NAEHE

Liest nur, ändert nichts und legt nichts an (§10.1 Punkt 3). Ein Gedächtnis,
das niemand nachmisst, verfällt lautlos — dieser Check misst.
Exit 0 = kein harter Befund, 1 = mindestens ein harter Befund, 2 = Aufruffehler.
"""

import argparse
import os
import re
import sys
from pathlib import Path

STANDARD_ORDNER = Path.home() / ".config" / "orchestrated-team" / "memory"
INDEX_NAME = "MEMORY.md"
ARCHIV_NAME = "archiv"

ERLAUBTE_TYPEN = ("user", "feedback", "project", "reference")

ZEILEN_GRENZE = 200       # Ladegrenze des Index, §10.3 Punkt 2
GROESSE_GRENZE = 25_000   # Bytes; §10.3 rechnet mit 1 KB = 1000 B (19,6 KB ≈ 78 % von 25 KB)
FUELLSTAND_AB = 0.85      # weiche Warnung ab 85 % einer Schranke, §10.4 Punkt 7

PFAD_PRAEFIXE = ("~/", "/" + "home/", "/" + "media/")  # §10.4 Punkt 6
PFAD_PLATZHALTER = "<>{}*?$…"                # … und die doppelten Doppelpunkte
PFAD_AUSLASSUNG = "..."                      # ASCII-Auslassung wie in ~/.nvm/.../bin/x
PFAD_SATZZEICHEN = ".,;:)"                   # am Ende gehört nicht zum Pfad

# §10.4 Punkt 8 (Stand 2026-09-21): ganzes Wort, geschärfte Liste — „fertig“ und
# „erledigt“ beschreiben einen Zustand, kein Altern, und zählen nicht mehr dazu.
ALTERUNG_MARKIERUNGEN = ("überholt", "ersetzt durch", "veraltet",
                         "gilt nicht mehr", "hinfällig")
ALTERUNG_MUSTER = [re.compile(r"(?<!\w)" + re.escape(m) + r"(?!\w)")
                   for m in ALTERUNG_MARKIERUNGEN]  # kein Buchstabe/Ziffer davor oder danach

NAEHE_ANZAHL = 10     # die zehn höchsten Paare, ohne Urteil, §10.4 Punkt 9
NAEHE_MIN_LAENGE = 4  # Wörter ab vier Zeichen

VERWEIS_MUSTER = re.compile(r"\[\[([^\[\]\n]+)\]\]")
BACKTICK_MUSTER = re.compile(r"`([^`\n]+)`")
WORT_MUSTER = re.compile(r"\w+")

# Reihenfolge der Befundarten wie in §10.4 (für die stabile Ausgabe)
HARTE_ORDNUNG = [
    "KOPF_UNVOLLSTAENDIG",
    "NAME_UNGLEICH_DATEI",
    "VERWAIST",
    "INDEX_ZEIGT_INS_LEERE",
    "VERWEIS_TOT",
    "VERWEIS_MIT_ENDUNG",
    "VERWEIS_KEIN_SLUG",
    "INDEX_ZU_GROSS",
    "PFAD_TOT",
]


class Aufruffehler(Exception):
    """Aufruffehler (Exit 2), z. B. Ordner ohne MEMORY.md."""


def _ohne_anfuehrung(wert):
    """Anführungszeichen um einem YAML-Wert ablegen (oder None bei leer)."""
    wert = wert.strip()
    if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
        wert = wert[1:-1].strip()
    return wert or None


def lies_kopf(text):
    """Zerlegt den YAML-Kopf einer Notiz (§10.2 Punkt 1).

    -> (felder, probleme, kopf_ende, koerper_ab)
       felder:     {"name": str|None, "description": str|None, "type": str|None}
       probleme:   Liste von Texten — je einer ein KOPF_UNVOLLSTAENDIG
       kopf_ende:  1-basierte Zeile der schließenden --- (0 ohne geschlossenen Kopf)
       koerper_ab: 1-basierte erste Zeile nach dem Kopf; 1 ohne geschlossenen
                   Kopf, damit dann die ganze Datei geprüft wird
    """
    zeilen = text.splitlines()
    felder = {"name": None, "description": None, "type": None}
    if not zeilen or zeilen[0].strip() != "---":
        return felder, ["kein YAML-Kopf — die Datei beginnt nicht mit ---"], 0, 1
    ende = None
    for nr in range(1, len(zeilen)):
        if zeilen[nr].strip() == "---":
            ende = nr
            break
    if ende is None:
        return felder, ["YAML-Kopf nicht geschlossen — zweite --- Zeile fehlt"], 0, 1
    in_metadata = False
    for zeile in zeilen[1:ende]:
        if not zeile.strip():
            continue
        oben = re.match(r"([\w-]+):\s*(.*)$", zeile)
        if oben:  # Zeile ohne Einzug
            schluessel, wert = oben.group(1), oben.group(2).strip()
            in_metadata = schluessel == "metadata"
            if schluessel in ("name", "description"):
                felder[schluessel] = _ohne_anfuehrung(wert)
            elif schluessel == "metadata" and wert:
                kompakt = re.search(r"\btype:\s*([\w-]+)", wert)
                if kompakt:  # Kurzform metadata: {type: user}
                    felder["type"] = kompakt.group(1)
            # ein type: auf oberster Ebene zählt als fehlend (§10.2: "unter metadata:")
        elif in_metadata:
            unten = re.match(r"\s+([\w-]+):\s*(.*)$", zeile)
            if unten and unten.group(1) == "type":
                felder["type"] = _ohne_anfuehrung(unten.group(2))
    return felder, [], ende + 1, ende + 2


def kopf_befunde(felder, probleme, datei, kopf_ende):
    """KOPF_UNVOLLSTAENDIG je fehlendem oder unzulässigem Feld."""
    befunde = []
    stelle = f"{datei}:{kopf_ende}" if kopf_ende else datei
    for problem in probleme:
        befunde.append(("KOPF_UNVOLLSTAENDIG", f"{stelle} — {problem}"))
    if not probleme:  # bei kaputtem Kopf sind die Felder nicht auswertbar
        if not felder["name"]:
            befunde.append(("KOPF_UNVOLLSTAENDIG", f"{stelle} — name: fehlt im Kopf"))
        if not felder["description"]:
            befunde.append(("KOPF_UNVOLLSTAENDIG", f"{stelle} — description: fehlt im Kopf"))
        if not felder["type"]:
            befunde.append(("KOPF_UNVOLLSTAENDIG", f"{stelle} — metadata.type: fehlt im Kopf"))
        elif felder["type"] not in ERLAUBTE_TYPEN:
            befunde.append(("KOPF_UNVOLLSTAENDIG",
                            f"{stelle} — type „{felder['type']}“ ist keiner der vier "
                            f"erlaubten Werte ({', '.join(ERLAUBTE_TYPEN)})"))
    return befunde


def verweis_befunde(datei, zeilen, erste_nr, vorhandene, archivierte):
    """[[…]]-Verweise prüfen: Endung, Slug-Form, Tod (§10.2 Punkt 4/5)."""
    befunde = []
    for i, zeile in enumerate(zeilen):
        for m in VERWEIS_MUSTER.finditer(zeile):
            ziel = m.group(1).strip()
            stelle = f"{datei}:{erste_nr + i}"
            if ziel.lower().endswith(".md"):
                befunde.append(("VERWEIS_MIT_ENDUNG",
                                f"{stelle} — [[{ziel}]] — die Endung .md gehört "
                                f"nicht in den Verweis (§10.2 Punkt 4)"))
            elif ziel != ziel.lower() or re.search(r"\s", ziel):
                befunde.append(("VERWEIS_KEIN_SLUG",
                                f"{stelle} — [[{ziel}]] — Leer- oder Großbuchstaben: "
                                f"Betonung statt Verweis? (§10.2 Punkt 5)"))
            elif ziel in vorhandene:
                continue
            elif ziel in archivierte:
                befunde.append(("VERWEIS_TOT",
                                f"{stelle} — [[{ziel}]] — verweist auf eine archivierte "
                                f"Notiz; die soll nicht mehr benutzt werden (§10.6)"))
            else:
                befunde.append(("VERWEIS_TOT",
                                f"{stelle} — [[{ziel}]] — keine Notiz dieses Namens "
                                f"(Lücke oder Tippfehler, §10.2 Punkt 4)"))
    return befunde


def _existiert(pfad):
    """True, wenn der Pfad (mit ~ expandiert) existiert."""
    echt = Path(os.path.expanduser(pfad)) if pfad.startswith("~/") else Path(pfad)
    return echt.exists()


def pfad_befunde(datei, zeilen, erste_nr):
    """Pfade in Backticks auf Existenz prüfen, mit Platzhalter-Ausnahme (§10.4 Punkt 6)."""
    befunde = []
    for i, zeile in enumerate(zeilen):
        for m in BACKTICK_MUSTER.finditer(zeile):
            roh = m.group(1).strip()
            if not roh.startswith(PFAD_PRAEFIXE):
                continue
            if "::" in roh or PFAD_AUSLASSUNG in roh or any(z in roh for z in PFAD_PLATZHALTER):
                continue  # Platzhalter, Auslassungen, Slash-Befehle und URL-Stücke sind keine Pfade
            pfad = roh.rstrip(PFAD_SATZZEICHEN)
            if " " in pfad and _existiert(pfad.split(" ", 1)[0]):
                continue  # Kommandozeile: das Programm existiert, der Rest sind Argumente
            if not _existiert(pfad):
                befunde.append(("PFAD_TOT", f"{datei}:{erste_nr + i} — `{pfad}` existiert nicht"))
    return befunde


def alterung_befund(datei, zeilen):
    """Erster Alterungsmarker der Notiz — einmal je Notiz, weich (§10.4 Punkt 8).

    Gesucht wird als ganzes Wort (§10.4 Punkt 8): `unfertig` und `gefertigt`
    dürfen nicht auslösen, `überholt.` und „überholt“ schon.
    """
    for nr, zeile in enumerate(zeilen, 1):
        klein = zeile.lower()
        for marker, muster in zip(ALTERUNG_MARKIERUNGEN, ALTERUNG_MUSTER):
            if muster.search(klein):
                return ("ALTERUNGS_SIGNAL",
                        f"{datei}:{nr} — enthält „{marker}“ — Kandidat fürs Archiv (§10.6)")
    return None


def index_eintraege(text):
    """Einträge des Index -> Liste (zeilennr, ziel|None).

    Eintrag ist eine Listenzeile ohne Einzug; **jeder** Markdown-Link der
    Zeile ist ein eigener Eintrag — eine Zeile mit fünf Links liefert fünf
    Paare mit derselben Zeilennummer (§10.3: mehrere Verweise je Zeile).
    Eine Listenzeile ohne jeden Link liefert genau ein Paar (zeilennr, None)
    und zeigt damit ins Leere. Überschriften, Einzüge und Kommentare gelten
    nicht als Eintrag.
    """
    eintraege = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        if not re.match(r"[-*]\s", zeile):
            continue
        ziele = [m.group(1).strip()
                 for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", zeile)]
        if ziele:
            eintraege.extend((nr, ziel) for ziel in ziele)
        else:
            eintraege.append((nr, None))
    return eintraege


def naehe_werte(paar_worte):
    """Die zehn höchsten Paare nach Wortüberlappung (Jaccard) aus name+description.

    Ohne Schwelle und ohne Urteil (§10.4 Punkt 9): Die Verteilung am echten
    Bestand fällt glatt ab, jede Schwelle würde Treffer bestimmen statt finden.
    -> Liste (wert, name_a, name_b), absteigend.
    """
    rels = sorted(paar_worte)
    paare = []
    for i, a in enumerate(rels):
        for b in rels[i + 1:]:
            schnitt = paar_worte[a] & paar_worte[b]
            if not schnitt:
                continue
            verein = paar_worte[a] | paar_worte[b]
            paare.append((len(schnitt) / len(verein), a, b))
    paare.sort(key=lambda p: (-p[0], p[1], p[2]))
    return paare[:NAEHE_ANZAHL]


def pruefe_merkzettel(ordner):
    """Einen Merkzettel-Ordner rein lesend prüfen.

    -> (harte, weiche, anzahl_notizen); harte/weiche sind Listen (art, text),
       der Text nennt Datei und — wo es eine gibt — Zeilennummer.
    """
    harte, weiche = [], []
    index_text = (ordner / INDEX_NAME).read_text(encoding="utf-8", errors="replace")

    # Bestand sammeln; archiv/ zählt nicht (§10.6 Punkt 1)
    notizen, archiviert = {}, set()
    for pfad in sorted(ordner.rglob("*.md")):
        rel = pfad.relative_to(ordner).as_posix()
        if rel.split("/", 1)[0] == ARCHIV_NAME:
            archiviert.add(pfad.stem)
            continue
        if rel == INDEX_NAME:
            continue
        notizen[rel] = pfad
    vorhandene = {pfad.stem for pfad in notizen.values()}

    paar_worte = {}
    for rel, pfad in notizen.items():
        text = pfad.read_text(encoding="utf-8", errors="replace")
        felder, probleme, kopf_ende, koerper_ab = lies_kopf(text)
        harte.extend(kopf_befunde(felder, probleme, rel, kopf_ende))
        if felder["name"] and felder["name"] != pfad.stem:
            harte.append(("NAME_UNGLEICH_DATEI",
                          f"{rel}:{kopf_ende} — name „{felder['name']}“ ≠ "
                          f"Dateiname „{pfad.stem}“ (§10.2 Punkt 2)"))
        zeilen = text.splitlines()
        koerper = zeilen[koerper_ab - 1:]  # ohne geschlossenen Kopf: ganze Datei
        harte.extend(verweis_befunde(rel, koerper, koerper_ab, vorhandene, archiviert))
        harte.extend(pfad_befunde(rel, koerper, koerper_ab))
        alterung = alterung_befund(rel, zeilen)
        if alterung:
            weiche.append(alterung)
        worte = set(w for w in WORT_MUSTER.findall(
            f"{felder['name'] or ''} {felder['description'] or ''}".lower())
            if len(w) >= NAEHE_MIN_LAENGE)
        paar_worte[rel] = worte

    # Index: Einträge gegen den Bestand, in beide Richtungen (§10.3 Punkt 1)
    ziele = set()
    for nr, ziel in index_eintraege(index_text):
        if ziel is None:
            harte.append(("INDEX_ZEIGT_INS_LEERE",
                          f"{INDEX_NAME}:{nr} — Eintrag zeigt auf keine Datei "
                          f"(kein Link in der Zeile)"))
            continue
        ziel_posix = Path(ziel).as_posix()
        if ziel_posix in notizen:
            ziele.add(ziel_posix)
        else:
            harte.append(("INDEX_ZEIGT_INS_LEERE",
                          f"{INDEX_NAME}:{nr} — Eintrag „{ziel}“ existiert nicht"))
    for rel in sorted(set(notizen) - ziele):
        harte.append(("VERWAIST", f"{rel} — keine Zeile im Index (§10.3 Punkt 1)"))

    # Pfade im Index zählen mit — ein toter Pfad im Köder führt genauso ins Leere
    harte.extend(pfad_befunde(INDEX_NAME, index_text.splitlines(), 1))

    # Ladegrenze des Index (§10.3 Punkt 2), weiche Warnung ab 85 %
    zeilen_zahl = len(index_text.splitlines())
    groesse = len(index_text.encode("utf-8"))
    ueber_grenze = []
    if zeilen_zahl > ZEILEN_GRENZE:
        ueber_grenze.append(f"{zeilen_zahl} Zeilen (Grenze {ZEILEN_GRENZE})")
    if groesse > GROESSE_GRENZE:
        ueber_grenze.append(f"{groesse} Bytes (Grenze {GROESSE_GRENZE})")
    if ueber_grenze:
        harte.append(("INDEX_ZU_GROSS", f"{INDEX_NAME} — " + " und ".join(ueber_grenze)))
    else:
        nahe_dran = []
        if zeilen_zahl / ZEILEN_GRENZE >= FUELLSTAND_AB:
            nahe_dran.append(f"{zeilen_zahl} von {ZEILEN_GRENZE} Zeilen")
        if groesse / GROESSE_GRENZE >= FUELLSTAND_AB:
            nahe_dran.append(f"{groesse} von {GROESSE_GRENZE} Bytes")
        if nahe_dran:
            weiche.append(("INDEX_FUELLSTAND",
                           f"{INDEX_NAME} — " + " und ".join(nahe_dran) +
                           " — Index wird bald zu groß, jetzt aufräumen (§10.3 Punkt 2)"))

    for wert, a, b in naehe_werte(paar_worte):
        wert_text = f"{wert:.3f}".replace(".", ",")
        weiche.append(("NAEHE",
                       f"{a} ↔ {b} — Wortüberlappung {wert_text} "
                       f"(ohne Urteil, §10.4 Punkt 9)"))

    rang = {k: i for i, k in enumerate(HARTE_ORDNUNG)}
    harte.sort(key=lambda befund: (rang.get(befund[0], 99), befund[1]))
    return harte, weiche, len(notizen)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Merkzettel-Check: hält die Form des Gedächtnisses gesund "
                    "(Vertrag §10). Liest nur, ändert nichts.")
    parser.add_argument("ordner", nargs="?", metavar="ORDNER", default=None,
                        help=f"Merkzettel-Ordner (Vorgabe: {STANDARD_ORDNER})")
    args = parser.parse_args(argv)

    ordner = Path(args.ordner) if args.ordner else STANDARD_ORDNER
    if not ordner.is_dir():
        print(f"merkzettel_check: Ordner existiert nicht: {ordner}", file=sys.stderr)
        return 2
    if not (ordner / INDEX_NAME).is_file():
        print(f"merkzettel_check: kein {INDEX_NAME} in {ordner}", file=sys.stderr)
        return 2

    try:
        harte, weiche, anzahl = pruefe_merkzettel(ordner)
    except OSError as e:
        print(f"merkzettel_check: Merkzettel nicht lesbar: {e}", file=sys.stderr)
        return 2

    print(f"Merkzettel: {ordner}")
    if harte:
        for art, text in harte:
            print(f"  {art}: {text}")
    else:
        print("  OK: keine harten Befunde")
    if weiche:
        print("  --- weiche Befunde — Hinweise, kein Fehler, Exit bleibt 0 ---")
        for art, text in weiche:
            print(f"  {art}: {text}")
    else:
        print("  (keine weichen Befunde)")
    print(f"Summe: {anzahl} Notiz(en), {len(harte)} harte(r) Befund(e), "
          f"{len(weiche)} weiche(r) Befund(e)")
    return 1 if harte else 0


if __name__ == "__main__":
    sys.exit(main())

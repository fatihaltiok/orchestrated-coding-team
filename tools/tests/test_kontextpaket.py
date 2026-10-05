"""Tests für werkzeuge/kontextpaket.py (Vertrag §11, besonders §11.11).

Nur Wegwerf-Projekte unter tmp_path — nie ein echtes Projekt, nie der
Merkzettel unter ~/.claude. Je Befundart aus §11.2–§11.10 mindestens ein
auslösender und ein sauberer Test; dazu die verbindlichen Gates 1, 2 und 5,
Formattoleranz an neutralisierten Formbeispielen aus mehreren Projekten,
Nur-Lesen, Kein-Netz, Determinismus und stabile Kennungen.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "kontextpaket", WERKZEUGE / "kontextpaket.py")
kp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kp)

HARTE_ARTEN = [
    "FORMAT_UNKLAR", "NUMMER_DOPPELT", "DEKLARATION_UNBEKANNTER_SCHLUESSEL",
    "STAND_NICHT_GEFUNDEN", "QUELLE_FEHLT", "KEINE_DEKLARATION",
    "EINHEIT_UEBER_GRENZE", "EINHEIT_FEHLT", "EINHEIT_GEAENDERT",
    "STATUS_GEAENDERT", "STAND_VERALTET", "TEXT_FEHLT", "TEIL_FEHLT",
]
WEICHE_ARTEN = [
    "KEINE_DEKLARATION", "STATUS_UNKLAR", "FELD_FEHLT", "BEDINGUNG_DOPPELT",
    "STAND_OHNE_ENDE", "KEINE_LISTEN",
]

STAND_KOPF = "^## Stand für den Wiedereinstieg"
STAND_ENDE = "^---\\s*$"

PROGRESS_GESUND = """# Projekt Wegwerf

## Stand für den Wiedereinstieg

Der Stand ist kurz und aktuell.
Nur bis zum ersten „Davor:“ gilt.

Davor: alles Frühere ist Historie.

---

## Bedingungen (append-only, nicht zusammenfassen)

Einträge vor dem 18.08. sind Nachbildungen.

- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.
- [2026-09-02 | aktiv] Zweite Bedingung, die über
  mehrere Zeilen geht und wörtlich bleibt.

## Entscheidungen (append-only, nicht zusammenfassen)

### E-001 | 2026-09-03 | gültig
- **Entscheidung:** Das Werkzeug liest nur und schreibt nie.
- **Verworfene Alternative:** Schreibender Zugriff mit Rückfrage.
- **Grund:** Rein lesend ist gefahrlos (Fatih, 2026-09-01).
- **Betroffene Pfade:** werkzeuge/kontextpaket.py, werkzeuge/tests/
- **Prüfbar durch:** pytest werkzeuge/tests -q

### E-002 | 2026-09-04 | revidiert durch E-001
- **Entscheidung:** Drei Teile waren geplant.
- **Verworfene Alternative:** Ein Stück.
- **Grund:** Verworfen, weil ein Stück prüfbarer ist.
- **Betroffene Pfade:** docs/
- **Prüfbar durch:** pytest
"""

DEKL_GESUND = {
    "format": 1,
    "projekt": "testprojekt",
    "protokoll": "PROGRESS.md",
    "stand": {"datei": "PROGRESS.md", "beginn": STAND_KOPF,
              "ende": STAND_ENDE, "historie_ab": "Davor:"},
    "pflicht_zusatz": ["ARBEITSWEISE.md"],
    "zusatz": ["docs/*.md"],
}

ARBEITSWEISE = """# Arbeitsweise Testprojekt

Der Interpreter ist python3. Tests: pytest. Push nur nach Freigabe.
"""

DOCS_HINWEIS = """# Hinweise

## Kandidatentalpe

Der Auftragsbär klettert gerne über Werkzeugfelsen und
sucht dabei nach Kontextbeeren im Quelltextdickicht.

## Etwas ganz anderes

Die Wiese ist grün und der Fluss trägt ruhig sein Wasser.
"""


def baue_projekt(tmp_path, progress=PROGRESS_GESUND, dekl=DEKL_GESUND,
                 arbeitsweise=ARBEITSWEISE, dateien=None):
    """Wegwerf-Projekt anlegen; gibt (ordner, dekl_pfad) zurück."""
    ordner = tmp_path / "projekt"
    ordner.mkdir()
    (ordner / "PROGRESS.md").write_text(progress, encoding="utf-8")
    if arbeitsweise is not None:
        (ordner / "ARBEITSWEISE.md").write_text(arbeitsweise, encoding="utf-8")
    (ordner / "docs").mkdir()
    (ordner / "docs" / "hinweis.md").write_text(DOCS_HINWEIS, encoding="utf-8")
    for rel, text in (dateien or {}).items():
        ziel = ordner / rel
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
    dekl_pfad = None
    if dekl is not None:
        dekl_pfad = ordner / "KONTEXT.json"
        dekl_pfad.write_text(json.dumps(dekl, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return ordner, dekl_pfad


def lauf(capsys, argv):
    """kp.main ausführen -> (code, stdout, stderr)."""
    code = kp.main(argv)
    gefangen = capsys.readouterr()
    return code, gefangen.out, gefangen.err


def baue_paket(capsys, tmp_path, neu=True, **kwargs):
    """Gesundes Paket bauen -> (code, paket_text, stderr, projektordner).

    neu=False: Projekt steht schon (zweiter bau-Lauf im selben Test).
    """
    ordner = tmp_path / "projekt"
    if neu:
        ordner, _dekl = baue_projekt(tmp_path, **kwargs)
    code, out, err = lauf(capsys, ["bau", str(ordner)])
    assert "Traceback" not in err, err
    return code, out, err, ordner


def lade_manifest(paket_text):
    bloecke = kp.MANIFEST_BLOCK_MUSTER.findall(paket_text)
    assert bloecke, "kein Manifest im Paket"
    return json.loads(bloecke[-1])


def snapshot(ordner):
    """Inhalt und Änderungszeit jeder Datei — für den Nur-Lesen-Test."""
    stand = {}
    for pfad in sorted(ordner.rglob("*")):
        if pfad.is_file():
            stand[pfad.relative_to(ordner).as_posix()] = (
                pfad.read_bytes(), pfad.stat().st_mtime_ns)
    return stand


# --- Sauberer Fall: Exit 0, keine Befunde ----------------------------------------

def test_sauber_bau_exit_0_ohne_befunde(tmp_path, capsys):
    code, paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert code == 0
    for art in HARTE_ARTEN + WEICHE_ARTEN:
        assert art not in paket
    man = lade_manifest(paket)
    assert man["format"] == 1
    assert man["projekt"] == "testprojekt"
    assert man["teil"] is None
    ids = [e["id"] for e in man["einheiten"]]
    # keine Einheit darf fehlen: 2 Bedingungen + 2 Entscheidungen + Stand + Pflicht-Zusatz
    assert len([i for i in ids if i.startswith("B-")]) == 2
    assert "E-001" in ids and "E-002" in ids
    assert "STAND" in ids
    assert "PZ-ARBEITSWEISE.md" in ids


def test_sauber_pruefe_exit_0(tmp_path, capsys):
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "OK" in err
    for art in HARTE_ARTEN:
        assert art not in err


def test_paket_bloecke_in_fester_reihenfolge(tmp_path, capsys):
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path)
    positionen = [paket.index(marke) for marke in (
        "# Kontextpaket —", "## Stand", "## Bedingungen (gelten)",
        "## Entscheidungen (gültig) — Übersicht",
        "## Entscheidungen — nicht gültig", "## Pflicht-Zusatzdateien",
        "## Zusatz — Fundstellen", "```kontext-manifest")]
    assert positionen == sorted(positionen)
    assert "## UNKLAR" not in paket  # Block fehlt, wenn leer (§11.5.2)


def test_manifest_felder_vollstaendig(tmp_path, capsys):
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    man = lade_manifest(paket)
    assert man["projektordner"] == str(Path(ordner).resolve())
    assert "T" in man["erzeugt"] and ("+" in man["erzeugt"][10:] or
                                      man["erzeugt"].endswith("Z"))
    assert man["auftrag_sha256"] is None
    quelle = man["quellen"][0]
    assert quelle["datei"] == "PROGRESS.md"
    assert len(quelle["sha256"]) == 64 and quelle["bytes"] > 0
    bedingung = next(e for e in man["einheiten"] if e["art"] == "bedingung")
    assert set(bedingung) == {"id", "art", "status", "datei", "zeile", "sha"}
    assert bedingung["status"] == "gilt" and len(bedingung["sha"]) == 12
    entscheidung = next(e for e in man["einheiten"] if e["id"] == "E-001")
    assert entscheidung["status"] == "gültig" and entscheidung["volltext"] is False


# --- Bedingungen wörtlich und Kennungen (§11.3/§11.5.4) ---------------------------

def test_bedingungen_woertlich_mit_kennung_und_fundstelle(tmp_path, capsys):
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher." in paket
    assert "Committe nichts selbst, frag vorher.\n[B-" in paket
    assert "· Zeile 16]" in paket  # 1-basierte Zeile der Kopfzeile der ersten Bedingung
    mehrzeilig = ("Zweite Bedingung, die über\n  mehrere Zeilen geht und wörtlich "
                  "bleibt.")
    assert mehrzeilig in paket  # Fortsetzung wörtlich, zeichengenau (mehrzeiliges Beispiel)
    einleitung = "Einträge vor dem 18.08. sind Nachbildungen."
    assert einleitung in paket  # §11.3.5: Text vor dem ersten Eintrag kommt mit


def test_aufgehobene_bedingung_nur_stummelzeile(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "- [2026-09-01 | aufgehoben 2026-09-20] Committe nichts selbst, frag vorher.")
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    man = lade_manifest(paket)
    aufgehoben = [e for e in man["einheiten"]
                  if e["art"] == "bedingung" and e["status"] == "aufgehoben"]
    assert len(aufgehoben) == 1
    text_ab_block = paket[paket.index("## Bedingungen"):]
    assert "[aufgehoben] 2026-09-01 · B-" in text_ab_block
    assert "Committe nichts selbst" not in text_ab_block  # kein Text der aufgehobenen


def test_status_aktiv_bis_wiederaufnahme_gilt(tmp_path, capsys):
    # „Alles andere gilt“ — auch „aktiv bis Wiederaufnahme“ (§11.3.4)
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe",
        "- [2026-09-01 | aktiv bis Wiederaufnahme durch Fatih] Committe")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "Committe nichts selbst, frag vorher." in paket


# --- FORMAT_UNKLAR (§11.3.6, §11.13 Punkt 2) + §11.11.5 (nichts fällt still weg) --

def test_format_unklar_ausgeloest_und_woertlich_im_unklar_block(tmp_path, capsys):
    # §11.13 Punkt 2: FORMAT_UNKLAR jetzt nur noch für Zeilen, die mit "- " oder
    # "* " beginnen, aber kein gültiger Eintragskopf sind (verunglückter Eintrag).
    fremdzeile = "- Kein gültiger Eintragskopf, mitten in der Liste verunglückt."
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung",
        f"{fremdzeile}\n- [2026-09-02 | aktiv] Zweite Bedingung")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 1
    assert "FORMAT_UNKLAR" in err
    assert "## UNKLAR — selbst lesen" in paket
    assert f"PROGRESS.md:17: {fremdzeile}" in paket  # wörtlich, mit Datei und Zeile
    # die Bedingung danach bleibt trotzdem im Paket (Verschweigen verboten)
    assert "mehrere Zeilen geht und wörtlich bleibt." in paket


def test_format_unklar_sauber_nicht_vorhanden(tmp_path, capsys):
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err


def test_zeile_in_codeblock_loest_kein_format_unklar_aus(tmp_path, capsys):
    # Prüfstand: eine „# … Bedingungen-Eintrag“-Zeile im Codeblock (§11.3.1)
    codeblock = ("```\n# Beispiel: Bedingungen-Eintrag\n"
                 "- [2020-01-01 | aktiv] keine echte Bedingung\n```\n")
    progress = PROGRESS_GESUND.replace(
        "## Entscheidungen (append-only, nicht zusammenfassen)",
        codeblock + "\n## Entscheidungen (append-only, nicht zusammenfassen)")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    assert "keine echte Bedingung" not in paket  # Codeblock-Inhalt ist keine Einheit


# --- BEDINGUNG_DOPPELT (§11.3.7) ---------------------------------------------------

def test_bedingung_doppelt_weich_beide_bleiben(tmp_path, capsys):
    doppelt = "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher."
    progress = PROGRESS_GESUND + "\n## Bedingungen (append-only, nicht zusammenfassen)\n\n" + doppelt + "\n"
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0  # weich
    assert "BEDINGUNG_DOPPELT" in err
    # seit §11.15 Punkt 8 trägt der Befund im Manifest die Zeile als zeile_text
    # mit — daher nur im Paketkörper zählen (beide bleiben im Paket, §11.3.7)
    koerper = kp.MANIFEST_BLOCK_MUSTER.sub("", paket)
    assert koerper.count(doppelt) == 2


def test_bedingung_doppelt_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "BEDINGUNG_DOPPELT" not in err


# --- NUMMER_DOPPELT (§11.4.5) -------------------------------------------------------

def test_nummer_doppelt_hart_bei_verschiedenem_rumpf(tmp_path, capsys):
    doppelt = """### E-001 | 2026-08-01 | gültig
- **Entscheidung:** Ein ganz anderer Text im zweiten Eintrag.
- **Verworfene Alternative:** nichts
- **Grund:** anders
- **Betroffene Pfade:** x/
- **Prüfbar durch:** nichts
"""
    progress = PROGRESS_GESUND + doppelt
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 1
    assert "NUMMER_DOPPELT" in err


def test_nicht_aufsteigende_nummern_kein_befund(tmp_path, capsys):
    # projekt-f: E-028 steht vor E-024 — kein Befund (§11.4.5)
    weiter = """### E-028 | 2026-09-05 | gültig
- **Entscheidung:** Später entschieden, früher gelistet.
- **Verworfene Alternative:** Um sortieren.
- **Grund:** Historie bleibt stehen.
- **Betroffene Pfade:** docs/
- **Prüfbar durch:** pytest

### E-024 | 2026-09-02 | gültig
- **Entscheidung:** Früher entschieden, später gelistet.
- **Verworfene Alternative:** Um sortieren.
- **Grund:** Historie bleibt stehen.
- **Betroffene Pfade:** docs/
- **Prüfbar durch:** pytest
"""
    progress = PROGRESS_GESUND + weiter
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "NUMMER_DOPPELT" not in err
    idx_028 = paket.index("E-028 (2026-09-05)")
    idx_024 = paket.index("E-024 (2026-09-02)")
    assert idx_028 < idx_024  # Dateireihenfolge bleibt erhalten


def test_nummer_doppelt_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "NUMMER_DOPPELT" not in err


# --- STATUS_UNKLAR / FELD_FEHLT (§11.4.2/§11.4.4) ------------------------------------

def test_status_unklar_wird_wie_gueltig_behandelt(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        "### E-002 | 2026-09-04 | Freigabe besteht; Ausführung für Neustart unterbrochen")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0  # weich
    assert "STATUS_UNKLAR" in err
    man = lade_manifest(paket)
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "unklar"
    assert "E-002 (2026-09-04)" in paket  # in der Übersicht, wie gültig behandelt


def test_status_unklar_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "STATUS_UNKLAR" not in err


def test_feld_fehlt_weich_gemeldet(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "- **Prüfbar durch:** pytest werkzeuge/tests -q\n", "")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0  # weich — alte Einträge sind historisch so (§11.4.4)
    assert "FELD_FEHLT" in err
    man = lade_manifest(paket)
    e1 = next(e for e in man["einheiten"] if e["id"] == "E-001")
    assert e1["status"] == "gültig"  # bleibt trotzdem gültig


def test_feld_fehlt_übersichtszeile_ohne_feld(tmp_path, capsys):
    # Ohne Feld „Entscheidung“: die ersten 300 Zeichen des Rumpfs (§11.5.5)
    progress = PROGRESS_GESUND.replace(
        "- **Entscheidung:** Das Werkzeug liest nur und schreibt nie.\n", "")
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    zeile = [z for z in paket.splitlines() if z.startswith("E-001 (2026-09-03)")]
    assert zeile, "Übersichtszeile für E-001 fehlt"
    assert "[kein Feld ‚Entscheidung‘]" in zeile[0]
    assert "Verworfene Alternative" in zeile[0]  # Rumpfanfang wörtlich


def test_feld_fehlt_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "FELD_FEHLT" not in err


# --- KEINE_DEKLARATION (§11.2.5): bau hart, protokoll weich -----------------------

def test_keine_deklaration_bau_hart_mit_hinweisblock(tmp_path, capsys):
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, dekl=None)
    assert code == 1
    assert "KEINE_DEKLARATION" in err
    assert "STAND NICHT DEKLARIERT — selbst lesen" in paket
    # Das Paket entsteht trotzdem: Bedingungen und Entscheidungen sind drin
    assert "Committe nichts selbst, frag vorher." in paket
    assert "E-001 (2026-09-03)" in paket


def test_keine_deklaration_protokoll_weich(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path, dekl=None)
    code, out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0  # hier nur weich (Altprojekt, §11.2.5)
    assert "KEINE_DEKLARATION" in err
    assert "Bedingungen: 2 gelten" in out


def test_keine_deklaration_sauber_mit_deklaration(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "KEINE_DEKLARATION" not in err


# --- DEKLARATION_UNBEKANNTER_SCHLUESSEL (§11.2.6) -----------------------------------

def test_deklaration_unbekannter_schluessel_hart(tmp_path, capsys):
    dekl = dict(DEKL_GESUND, pflicht_zusaetze=["ARBEITSWEISE.md"])  # Tippfehler
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1
    assert "DEKLARATION_UNBEKANNTER_SCHLUESSEL" in err
    assert "pflicht_zusaetze" in err


def test_deklaration_unbekannter_schluessel_im_stand(tmp_path, capsys):
    dekl = json.loads(json.dumps(DEKL_GESUND))
    dekl["stand"]["anfang"] = "^x"  # unbekannter Schlüssel unter stand
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1
    assert "stand.anfang" in err


def test_deklaration_unbekannter_schluessel_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "DEKLARATION_UNBEKANNTER_SCHLUESSEL" not in err


# --- STAND_NICHT_GEFUNDEN / STAND_OHNE_ENDE / historie_ab (§11.2.1/2) ---------------

def test_stand_nicht_gefunden_hart(tmp_path, capsys):
    dekl = json.loads(json.dumps(DEKL_GESUND))
    dekl["stand"]["beginn"] = "^Diese Überschrift gibt es nicht"
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1
    assert "STAND_NICHT_GEFUNDEN" in err


def test_stand_nicht_gefunden_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "STAND_NICHT_GEFUNDEN" not in err


def test_stand_ohne_ende_hart_bei_bau_weich_bei_protokoll(tmp_path, capsys):
    # §11.12 Punkt 18 (B18): STAND_OHNE_ENDE ist bei bau hart, bei protokoll weich
    dekl = json.loads(json.dumps(DEKL_GESUND))
    dekl["stand"]["ende"] = "^Diese Zeile kommt nie vor$"
    dekl["stand"]["historie_ab"] = None  # sonst schneidet der Schnitt vor dem Dateiende
    code, paket, err, ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1  # hart bei bau
    assert "STAND_OHNE_ENDE" in err
    # Stand reicht bis Dateiende: die letzte Protokollzeile ist im Standblock
    stand_block = paket[paket.index("## Stand"):paket.index("## Bedingungen (gelten)")]
    assert "- **Prüfbar durch:** pytest" in stand_block
    code2, _out, err2 = lauf(capsys, ["protokoll", str(ordner)])
    assert code2 == 0  # weich bei protokoll
    assert "STAND_OHNE_ENDE" in err2


def test_stand_ohne_ende_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "STAND_OHNE_ENDE" not in err


def test_historie_ab_geschnitten_mit_sichtbarem_vermerk(tmp_path, capsys):
    _code, paket, _err, _ordner = baue_paket(capsys, tmp_path)
    assert "Historie ab Zeile 6 weggelassen, laut KONTEXT.json" in paket
    assert "Davor: alles Frühere ist Historie." not in paket
    assert "Der Stand ist kurz und aktuell." in paket  # der Teil vor dem Schnitt bleibt


# --- QUELLE_FEHLT (§11.2.3) ----------------------------------------------------------

def test_quelle_fehlt_pflicht_zusatz_hart(tmp_path, capsys):
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, arbeitsweise=None)
    assert code == 1
    assert "QUELLE_FEHLT" in err
    assert "ARBEITSWEISE.md" in err


def test_quelle_fehlt_protokoll_hart(tmp_path, capsys):
    dekl = json.loads(json.dumps(DEKL_GESUND))
    dekl["protokoll"] = "GEHT-NICHT.md"
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1
    assert "QUELLE_FEHLT" in err


def test_quelle_fehlt_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "QUELLE_FEHLT" not in err


# --- KEINE_LISTEN (§11.10) ------------------------------------------------------------

def test_keine_listen_weich_bei_protokoll(tmp_path, capsys):
    leer = "# Projekt ohne Listen\n\nNur Text, keine Bedingungen, keine Entscheidungen.\n"
    dekl = json.loads(json.dumps(DEKL_GESUND))
    del dekl["stand"]  # kein Stand deklariert, also kein STAND_NICHT_GEFUNDEN
    ordner, _dekl = baue_projekt(tmp_path, progress=leer, dekl=dekl)
    code, out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0
    assert "KEINE_LISTEN" in err
    assert "Bedingungen: 0 gelten" in out
    assert "Entscheidungen: 0 gültig" in out


def test_keine_listen_sauber(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0
    assert "KEINE_LISTEN" not in err


# --- Gate 1: Auslassung wird gemeldet (§11.11.1) ---------------------------------------

def test_gate1_bedingung_aus_koerper_geloescht_text_fehlt(tmp_path, capsys):
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    ganz = "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher."
    assert ganz in paket
    (tmp_path / "paket.md").write_text(paket.replace(ganz, ""), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(tmp_path / "paket.md"),
                                    "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err
    man = lade_manifest(paket)
    kennung = next(e["id"] for e in man["einheiten"]
                   if e["art"] == "bedingung" and e["status"] == "gilt")
    assert kennung in err  # genau diese Kennung


def test_gate1_wort_in_bedingung_geaendert_text_fehlt(tmp_path, capsys):
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    (tmp_path / "paket.md").write_text(
        paket.replace("frag vorher.", "frag nachher."), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(tmp_path / "paket.md"),
                                    "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err


# --- Gate 2: Veraltung wird gemeldet (§11.11.2) -----------------------------------------

def pruefe_nach_aenderung(capsys, tmp_path, aenderung):
    """Paket bauen, Quelle ändern, pruefen -> (code, stderr)."""
    code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    assert code == 0
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    progress = (ordner / "PROGRESS.md").read_text(encoding="utf-8")
    (ordner / "PROGRESS.md").write_text(aenderung(progress), encoding="utf-8")
    return lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])


def test_gate2_neue_bedingung_einheit_fehlt(tmp_path, capsys):
    code, _out, err = pruefe_nach_aenderung(
        capsys, tmp_path,
        lambda p: p.replace("## Entscheidungen",
                            "- [2026-09-06 | aktiv] Neu angehängte dritte Bedingung.\n\n"
                            "## Entscheidungen"))
    assert code == 1
    assert "EINHEIT_FEHLT" in err


def test_gate2_ent_scheidung_revidiert_status_geaendert(tmp_path, capsys):
    code, _out, err = pruefe_nach_aenderung(
        capsys, tmp_path,
        lambda p: p.replace("### E-001 | 2026-09-03 | gültig",
                            "### E-001 | 2026-09-03 | revidiert durch E-009"))
    assert code == 1
    assert "STATUS_GEAENDERT" in err


def test_gate2_stand_geaendert_stand_veraltet(tmp_path, capsys):
    code, _out, err = pruefe_nach_aenderung(
        capsys, tmp_path,
        lambda p: p.replace("Der Stand ist kurz und aktuell.",
                            "Der Stand ist kurz und jetzt anders."))
    assert code == 1
    assert "STAND_VERALTET" in err


def test_gate2_wortlaut_geaendert_einheit_geaendert(tmp_path, capsys):
    code, _out, err = pruefe_nach_aenderung(
        capsys, tmp_path,
        lambda p: p.replace("Das Werkzeug liest nur und schreibt nie.",
                            "Das Werkzeug liest nur und schreibt fast nie."))
    assert code == 1
    assert "EINHEIT_GEAENDERT" in err


def test_gate2_unveraenderte_quelle_exit_0(tmp_path, capsys):
    code, _out, err = pruefe_nach_aenderung(capsys, tmp_path, lambda p: p)
    assert code == 0, err
    assert "OK" in err


# --- Gate 5: Grenze — teilen statt abschneiden (§11.11.3) --------------------------------

GRENZE = 2500
EXTRA_BEDINGUNGEN = 3  # lange Bedingungen, damit der Pflichtteil die Grenze reißt


def progress_mit_langen_bedingungen():
    lange = "".join(
        f"- [2026-09-1{i} | aktiv] Lange Bedingung {i}: "
        + ("wörtlich wiederholter Inhalt. " * 20)
        + f"Ende der Bedingung {i}.\n"
        for i in range(EXTRA_BEDINGUNGEN))
    return PROGRESS_GESUND.replace(
        "## Entscheidungen (append-only, nicht zusammenfassen)",
        lange + "\n## Entscheidungen (append-only, nicht zusammenfassen)")


def gate5_paket(capsys, tmp_path):
    ordner, _dekl = baue_projekt(tmp_path, progress=progress_mit_langen_bedingungen())
    code, out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(GRENZE),
                                   "--ausgabe", str(tmp_path / "teile")])
    assert "Traceback" not in err, err
    return code, ordner, tmp_path / "teile"


def test_gate5_ohne_ausgabe_exit_3_stdout_leer(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path, progress=progress_mit_langen_bedingungen())
    code, out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(GRENZE)])
    assert code == 3
    assert out == ""  # stdout bleibt leer — ein abgeschnittenes Paket sähe vollständig aus
    # §11.12 Punkt 1 (B1): ohne --ausgabe misst die Grenze das GANZE Paket
    assert "Paket" in err
    assert "Zeichen" in err
    assert "--ausgabe" in err  # Vorschlag


def test_gate5_ohne_grenze_unter_der_grenze_exit_0(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, err = lauf(capsys, ["bau", str(ordner),
                                   "--max-zeichen", "100000"])
    assert code == 0
    assert "kontext-manifest" in out  # Paket wird ausgegeben


def test_gate5_mit_ausgabe_teile_jede_unter_grenze_vereinigung_vollstaendig(tmp_path, capsys):
    code, _ordner, teile = gate5_paket(capsys, tmp_path)
    dateien = sorted(teile.glob("paket-*-von-*.md"))
    assert len(dateien) >= 2  # wirklich geteilt
    alle = {}
    for k, datei in enumerate(dateien, 1):
        text = datei.read_text(encoding="utf-8")
        assert len(text) <= GRENZE, f"{datei.name} hat {len(text)} Zeichen"
        assert text.startswith(f"TEIL {k} VON {len(dateien)}")
        assert "ohne die anderen Teile unvollständig" in text
        man = lade_manifest(text)
        assert man["teil"] == {"nr": k, "von": len(dateien)}
        for e in man["einheiten"]:
            assert e["id"] not in alle, f"{e['id']} doppelt über Teile"
            alle[e["id"]] = e
    # die Vereinigung enthält jede Einheit genau einmal: (2 + EXTRA) Bedingungen,
    # E-001, E-002, STAND, Pflicht-Zusatz — und seit §11.15 Punkt 3 die
    # Einleitung vor dem ersten Eintrag
    assert len(alle) == 3 + EXTRA_BEDINGUNGEN + 4
    assert {"E-001", "E-002", "STAND", "PZ-ARBEITSWEISE.md"} <= set(alle)
    assert any(e["art"] == "einleitung" for e in alle.values())


def test_gate5_pruefe_ueber_die_teile_exit_0(tmp_path, capsys):
    _code, ordner, teile = gate5_paket(capsys, tmp_path)
    erster = sorted(teile.glob("paket-*-von-*.md"))[0]
    code, _out, err = lauf(capsys, ["pruefe", str(erster), "--projekt", str(ordner)])
    assert code == 0, err
    assert "OK" in err


def test_gate5_teil_geloescht_teil_fehlt(tmp_path, capsys):
    _code, ordner, teile = gate5_paket(capsys, tmp_path)
    dateien = sorted(teile.glob("paket-*-von-*.md"))
    assert len(dateien) >= 2
    dateien[-1].unlink()
    code, _out, err = lauf(capsys, ["pruefe", str(dateien[0]),
                                    "--projekt", str(ordner)])
    assert code == 1
    assert "TEIL_FEHLT" in err


# --- Formattoleranz an nachgebauten echten Fällen (§11.11.4) --------------------------

def test_formattoleranz_gueltig_ohne_umlaut(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace("| gültig", "| gueltig")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    man = lade_manifest(paket)
    assert next(e for e in man["einheiten"] if e["id"] == "E-001")["status"] == "gültig"


@pytest.mark.parametrize("status", [
    "**revidiert durch E-009**",               # projekt-a: Fettschrift
    "revidiert durch E-033 (2026-08-29 abends)",  # mit Klammerzusatz
])
def test_formattoleranz_revidiert_variants_nicht_gueltig(tmp_path, capsys, status):
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        f"### E-002 | 2026-09-04 | {status}")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    # §11.12 Punkt 15: Block 7 zeigt das Statusfeld — Sterne entfernt (B15),
    # sonst wörtlich
    assert f"E-002 — {status.replace('*', '')} · Zeile" in paket


def test_formattoleranz_titel_nach_dem_status(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "### E-001 | 2026-09-03 | gültig",
        "### E-001 | 2026-09-03 | gültig — Beispiel-Regel")
    code, _paket, err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    man = lade_manifest(_paket)
    assert next(e for e in man["einheiten"] if e["id"] == "E-001")["status"] == "gültig"
    # der Titel bleibt Teil des Wortlauts — im Volltext steht die Kopfzeile wörtlich
    code, out, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0
    assert "gültig — Beispiel-Regel" in out


def test_formattoleranz_entscheidungen_ohne_abschnittsueberschrift(tmp_path, capsys):
    # projekt-e: Einträge stehen ohne eigene Abschnittsüberschrift (§11.4.1)
    progress = PROGRESS_GESUND.replace(
        "## Entscheidungen (append-only, nicht zusammenfassen)\n\n### E-001",
        "## Sonstiges\n\n### E-001")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "E-001 (2026-09-03)" in paket
    assert "E-002" in {e["id"] for e in lade_manifest(paket)["einheiten"]}


def test_formattoleranz_zweiter_abschnitt_weitere_bedingungen(tmp_path, capsys):
    # projekt-g: „### Weitere Bedingungen“ — beide Abschnitte werden gelesen
    weiter = ("### Weitere Bedingungen\n\n"
              "- [2026-08-01 | aktiv] Bedingung aus dem zweiten Abschnitt.\n")
    progress = PROGRESS_GESUND + "\n" + weiter
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "Bedingung aus dem zweiten Abschnitt." in paket
    man = lade_manifest(paket)
    assert len([e for e in man["einheiten"] if e["art"] == "bedingung"]) == 3


def test_formattoleranz_bedingung_status_freigabe_unterbrochen_gilt(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe",
        "- [2026-09-01 | Freigabe besteht; Ausführung für Neustart unterbrochen] Committe")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "Committe nichts selbst, frag vorher." in paket  # gilt — alles andere gilt
    man = lade_manifest(paket)
    gelten = [e for e in man["einheiten"]
              if e["art"] == "bedingung" and e["status"] == "gilt"]
    assert len(gelten) == 2


def test_formattoleranz_mehrzeilige_bedingung_vollstaendig_und_kennung_stabil():
    b1 = kp.Bedingung(1, "2026-09-02", "aktiv",
                      ["- [2026-09-02 | aktiv] Zweite Bedingung, die über",
                       "   mehrere Zeilen geht und wörtlich bleibt."])
    b2 = kp.Bedingung(99, "2026-09-02", "aktiv",
                      ["- [2026-09-02 | aktiv] Zweite Bedingung, die über",
                       "   mehrere Zeilen geht und wörtlich bleibt."])
    assert b1.kennung == b2.kennung  # Kennung aus Text, nicht aus Zeilennummer


# --- Volltext-Auswahl (a) genannt, (b) Pfad, (c) Suchwörter (§11.5.6) ------------------

def test_volltext_ausgabe_ohne_auftrag_nur_block5(tmp_path, capsys):
    # §11.12 Punkt 19 (B19): ohne jeden Bezug entfällt Block 6 mit einer
    # Hinweiszeile (die Überschrift bleibt als Platzhalter stehen)
    _code, paket, _err, _ordner = baue_paket(capsys, tmp_path)
    assert "## Entscheidungen — Volltext, auftragsbezogen" in paket
    assert "kein Auftrag gegeben — Block entfällt" in paket
    assert "Volltextgrund" not in paket
    man = lade_manifest(paket)
    assert next(e for e in man["einheiten"] if e["id"] == "E-001")["volltext"] is False


def test_volltext_a_genannte_ent_scheidung_im_volltext(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0
    assert "## Entscheidungen — Volltext, auftragsbezogen" in out
    assert "Volltextgrund: genannt" in out
    man = lade_manifest(out)
    assert next(e for e in man["einheiten"] if e["id"] == "E-001")["volltext"] is True


def test_volltext_a_genannte_nummer_ohne_nullen(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "1"])
    assert code == 0
    assert "Volltextgrund: genannt" in out


def test_volltext_b_pfad_treffer(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, _err = lauf(capsys, ["bau", str(ordner), "--pfad", "kontextpaket.py"])
    assert code == 0
    assert "Volltextgrund: pfad" in out


def test_volltext_c_zwei_suchwoerter_genuegen_eines_nicht(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    # nur ein Suchwort trifft -> keine Vorauswahl
    code, out, _err = lauf(capsys, ["bau", str(ordner),
                                    "--auftrag", "kontextpaket"])
    assert code == 0
    assert "Volltextgrund: suchwörter" not in out
    # zwei verschiedene Suchwörter -> grobe Vorauswahl greift
    # („werkzeug“ ist Stoppwort nach §11.12 Punkt 8, deshalb andere Wörter)
    code, out, _err = lauf(capsys, ["bau", str(ordner),
                                    "--auftrag", "liest kontextpaket"])
    assert code == 0
    assert "grobe Vorauswahl" in out
    assert "Volltextgrund: suchwörter" in out
    # … und sie entfernt nichts aus der Übersicht
    assert "E-001 (2026-09-03)" in out


# --- Nur-Lesen (§11.11.6) ----------------------------------------------------------------

def test_nur_lesen_bau_pruefe_protokoll_kandidaten_aendern_nichts(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    vorher = snapshot(ordner)
    code, paket, _err, _o = baue_paket(capsys, tmp_path, neu=False)
    assert code == 0
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    assert lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])[0] == 0
    assert lauf(capsys, ["protokoll", str(ordner)])[0] == 0
    assert lauf(capsys, ["kandidaten", str(ordner), "--auftrag", "Werkzeug Test"])[0] == 0
    assert snapshot(ordner) == vorher  # Inhalt und Änderungszeiten unverändert


def test_ausgabe_innerhalb_des_projekts_exit_2(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, _out, err = lauf(capsys, ["bau", str(ordner),
                                    "--ausgabe", str(ordner / "teil")])
    assert code == 2
    assert "innerhalb" in err


def test_ausgabe_vorhanden_und_nicht_leer_exit_2(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    aus = tmp_path / "voll"
    aus.mkdir()
    (aus / "x.txt").write_text("belegt", encoding="utf-8")
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--ausgabe", str(aus)])
    assert code == 2
    assert "nicht leer" in err


# --- Kein Netz (§11.11.7) ------------------------------------------------------------------

def test_1112_b21_kein_netz_frischer_prozess_alte_quelltextsuche_entfallen():
    """§11.13 Auslegung 11: nur der frische Unterprozess zählt (B21), die
    Quelltextsuche ist entfallen. urllib.parse bleibt ausdrücklich erlaubt."""
    modul_pfad = str(WERKZEUGE / "kontextpaket.py")
    frisch = subprocess.run(
        [sys.executable, "-c",
         "import sys, importlib.util; "
         f"spec = importlib.util.spec_from_file_location('kp', r'{modul_pfad}'); "
         "vor = set(sys.modules); "
         "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
         "neu = {k.split('.')[0] for k in set(sys.modules) - vor}; "
         "verboten = {'socket', 'http', 'https', 'ftplib', 'ssl', 'telnetlib', "
         "'smtplib', 'xmlrpc', 'request'}; "
         "assert not (verboten & neu), verboten & neu"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert frisch.returncode == 0, frisch.stderr


# --- Determinismus (§11.11.8) ----------------------------------------------------------------

def test_determinismus_zweimal_bau_gleicher_koerper_bis_auftime(tmp_path, capsys):
    import re
    _c1, p1, _e1, _o = baue_paket(capsys, tmp_path)
    _c2, p2, _e2, _o = baue_paket(capsys, tmp_path, neu=False)
    muster = re.compile(r'(- Erzeugt: .*|"erzeugt": "[^"]*")')
    assert muster.sub("", p1) == muster.sub("", p2)


# --- Kennung stabil (§11.11.9) ----------------------------------------------------------------

def test_kennung_stabil_zeile_eingefuegt_kennungen_gleich_pruefe_schweigt(tmp_path, capsys):
    _code, p1, _err, ordner = baue_paket(capsys, tmp_path)
    m1 = lade_manifest(p1)
    alt = {e["id"]: e["zeile"] for e in m1["einheiten"] if e["id"].startswith(("B-", "E-"))}
    progress = (ordner / "PROGRESS.md").read_text(encoding="utf-8")
    progress = progress.replace("## Bedingungen",
                                "Hier kommt eine neue Notizzeile dazu.\n\n## Bedingungen")
    (ordner / "PROGRESS.md").write_text(progress, encoding="utf-8")
    code, p2, err, _o = baue_paket(capsys, tmp_path, neu=False)
    assert code == 0
    m2 = lade_manifest(p2)
    neu = {e["id"]: e["zeile"] for e in m2["einheiten"] if e["id"].startswith(("B-", "E-"))}
    assert set(neu) == set(alt)          # alle Kennungen gleich
    assert set(neu.values()) != set(alt.values())  # aber die Zeilen verschoben sich
    # pruefe meldet dafür nichts
    pfad = tmp_path / "paket1.md"
    pfad.write_text(p1, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "OK" in err

# === TEIL4-TESTS ===


import hashlib

LANGER_ZUSATZ_KOPF = "## Pflichtanleitung\n\n"
LANGER_ZUSATZ_SATZ = ("Pflichtzeile mit Inhalt, damit die Datei über die "
                      "Grenze wächst. ")


# --- EINHEIT_UEBER_GRENZE (§11.8.3) ---------------------------------------------------

def test_einheit_ueber_grenze_hart_eigener_teil_ungekuerzt(tmp_path, capsys):
    """Einheit über der Grenze: harter Befund, eigener Teil, nicht gekürzt (§11.8.3)."""
    ordner, _dekl = baue_projekt(tmp_path)
    lang = LANGER_ZUSATZ_KOPF + LANGER_ZUSATZ_SATZ * 100
    (ordner / "ARBEITSWEISE.md").write_text(lang, encoding="utf-8")
    grenze = 900
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(grenze),
                                    "--ausgabe", str(tmp_path / "teile")])
    assert code == 1
    assert "EINHEIT_UEBER_GRENZE" in err
    assert "ARBEITSWEISE.md" in err
    teile = sorted((tmp_path / "teile").glob("paket*.md"))
    assert len(teile) >= 2
    # Die überlange Einheit steht wörtlich, ungekürzt — in genau einem Teil.
    treffer = [t for t in teile if "Pflichtzeile mit Inhalt" in t.read_text(encoding="utf-8")]
    assert len(treffer) == 1
    assert treffer[0].read_text(encoding="utf-8").count("Pflichtzeile mit Inhalt") == 100
    assert len(treffer[0].read_text(encoding="utf-8")) > grenze
    # Das Manifest des Teils listet die überlange Pflicht-Einheit.
    man = lade_manifest(treffer[0].read_text(encoding="utf-8"))
    assert "PZ-ARBEITSWEISE.md" in [e.get("id") for e in man.get("einheiten", [])]


def test_einheit_ueber_grenze_sauber_mit_grosser_grenze(tmp_path, capsys):
    """Reicht die Grenze, gibt es keinen EINHEIT_UEBER_GRENZE-Befund (§11.8.3)."""
    ordner, _dekl = baue_projekt(tmp_path)
    (ordner / "ARBEITSWEISE.md").write_text(
        LANGER_ZUSATZ_KOPF + LANGER_ZUSATZ_SATZ * 100, encoding="utf-8")
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", "40000"])
    assert code == 0
    assert "EINHEIT_UEBER_GRENZE" not in err


# --- kandidaten (§11.7) ---------------------------------------------------------------

def test_kandidaten_json_form_punkte_sortierung_gegenprobe(tmp_path, capsys):
    """JSON-Form, Punktzahl = verschiedene Suchwörter, Sortierung; Fremdabschnitt fehlt."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, err = lauf(capsys, [
        "kandidaten", str(ordner), "--auftrag",
        "Kandidatentalpe Auftragsbär Werkzeugfelsen Kontextbeeren"])
    assert code == 0
    assert "Traceback" not in err
    daten = json.loads(out)
    assert daten["format"] == 1
    assert daten["auftrag"].startswith("Kandidatentalpe")
    kand = daten["kandidaten"]
    assert kand, "Abschnitt mit Suchwörtern muss Kandidat sein"
    erster = next(k for k in kand if k["ueberschrift"] == "Kandidatentalpe")
    assert erster["punkte"] == 4  # Überschrift + drei Worttreffer im Rumpf
    assert erster["id"].startswith("Z-") and len(erster["id"]) == 14
    assert erster["datei"] == str((ordner / "docs" / "hinweis.md").resolve())
    assert erster["ueberschrift"] == "Kandidatentalpe"
    assert erster["punkte"] >= 3
    assert erster["zeile"] >= 1 and erster["bis"] >= erster["zeile"]
    assert erster["text"].startswith("## Kandidatentalpe")   # wörtlich mit Überschrift
    # Kennung = Z- + SHA-256(12) über Abspfad + Zeilenumbruch + Text (§11.7.3)
    frisch = hashlib.sha256((erster["datei"] + "\n" + erster["text"])
                            .encode("utf-8")).hexdigest()[:12]
    assert erster["sha"] == frisch
    # Gegenprobe: Der Abschnitt ohne Suchwörter ist kein eigener Kandidat
    # (er taucht nur innerhalb des H1-Abschnitts auf, der die ganze Datei umfasst).
    assert all(k["ueberschrift"] != "Etwas ganz anderes" for k in kand)
    # Sortierung: Punkte absteigend, dann Datei, dann Zeile (§11.7.2)
    schluessel = [(-k["punkte"], k["datei"], k["zeile"]) for k in kand]
    assert schluessel == sorted(schluessel)


def test_kandidaten_merkzettel_ohne_archiv_und_memory(tmp_path, capsys):
    """--merkzettel liest Notizen, aber nie MEMORY.md und nie archiv/ (§11.7.1)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = tmp_path / "merkzettel"
    (merk / "archiv").mkdir(parents=True)
    (merk / "archiv" / "alt.md").write_text(
        "## Alt\n\nAuftragsbär im Archiv", encoding="utf-8")
    (merk / "MEMORY.md").write_text(
        "- [Index](x.md) — Auftragsbär-Werkzeugfelsen", encoding="utf-8")
    (merk / "notizen").mkdir()
    (merk / "notizen" / "heute.md").write_text(
        "## Heute\n\nDer Auftragsbär sieht den Werkzeugfelsen.", encoding="utf-8")
    code, out, err = lauf(capsys, [
        "kandidaten", str(ordner), "--auftrag", "Auftragsbär Werkzeugfelsen",
        "--merkzettel", str(merk)])
    assert code == 0
    daten = json.loads(out)
    dateien = [k["datei"] for k in daten["kandidaten"]]
    assert all("archiv/" not in d for d in dateien), dateien
    assert all(not d.endswith("MEMORY.md") for d in dateien), dateien
    assert str((merk / "notizen" / "heute.md").resolve()) in dateien
    treffer = [k for k in daten["kandidaten"]
               if k["datei"] == str((merk / "notizen" / "heute.md").resolve())]
    assert treffer[0]["text"].startswith("## Heute")


def test_kandidaten_stueckelung_ueber_4000_kein_verlust(tmp_path, capsys):
    """Abschnitt über 4000 Zeichen: Stücke an Zeilengrenzen, lückenlos (§11.7.1)."""
    ordner, _dekl = baue_projekt(tmp_path)
    zeilen = [f"Zeile {i}: Werkzeugfelsen im Pfad, damit der Abschnitt über die "
              f"Stückgrenze wächst." for i in range(1, 121)]
    (ordner / "docs" / "tal.md").write_text(
        "## Talpauftrag\n\n" + "\n".join(zeilen) + "\n", encoding="utf-8")
    code, out, err = lauf(capsys, ["kandidaten", str(ordner),
                                   "--auftrag", "Werkzeugfelsen"])
    assert code == 0
    daten = json.loads(out)
    tal = [k for k in daten["kandidaten"] if k["ueberschrift"] == "Talpauftrag"]
    assert len(tal) >= 2
    assert all(k["text"].splitlines()[0] == "## Talpauftrag" for k in tal)
    koerper = [z for k in tal for z in k["text"].splitlines()[1:]]
    assert koerper == [""] + zeilen          # nichts fehlt, nichts doppelt
    for a, b in zip(tal, tal[1:]):
        assert a["bis"] + 1 == b["zeile"]    # aufeinanderfolgende Stücke
    assert all(len(k["text"]) <= 4100 for k in tal)
    assert all(k["punkte"] >= 1 for k in tal)


def test_kandidaten_ohne_suchwoerter_leer_mit_hinweis(tmp_path, capsys):
    """Auftrag ohne verwertbare Suchwörter: keine Kandidaten, Hinweis, Exit 0."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, err = lauf(capsys, ["kandidaten", str(ordner),
                                   "--auftrag", "ein zwei der und"])
    assert code == 0
    assert json.loads(out)["kandidaten"] == []
    assert "keine Suchwörter" in err


# --- protokoll --alle (§11.10) --------------------------------------------------------

def test_protokoll_alle_mit_repos_überspringt_ohne_protokoll(tmp_path, capsys):
    """--alle über --repos: fehlender Ordner und fehlendes Protokoll = Hinweis (§11.10)."""
    gesund, _dekl = baue_projekt(tmp_path)
    leer = tmp_path / "leer"
    leer.mkdir()
    repos_pfad = tmp_path / "repos.json"
    repos_pfad.write_text(json.dumps({"repos": [
        {"pfad": str(gesund)}, {"pfad": str(tmp_path / "fehlt")},
        {"pfad": str(leer)}]}), encoding="utf-8")
    code, out, err = lauf(capsys, ["protokoll", "--alle", "--repos", str(repos_pfad)])
    assert code == 0
    assert "Ordner existiert nicht" in out
    assert "keine Protokolldatei PROGRESS.md" in out
    assert "Bedingungen:" in out and "Entscheidungen:" in out
    assert "QUELLE_FEHLT" not in err          # überspringen, kein harter Befund


def test_protokoll_alle_ohne_repos_datei_exit_2(tmp_path, capsys):
    """--alle ohne lesbare repos.json ist ein Aufruffehler (Exit 2)."""
    code, _out, err = lauf(capsys, ["protokoll", "--alle",
                                    "--repos", str(tmp_path / "fehlt.json")])
    assert code == 2
    assert "repos.json nicht lesbar" in err


# --- Aufruffehler gesammelt (Exit 2) --------------------------------------------------

def test_exit_2_bei_verletzten_aufrufvertraegen(tmp_path, capsys):
    """Fehlende Ordner/Dateien und kaputte Manifeste sind Aufruffehler (Exit 2)."""
    # 1. Projektordner fehlt (bau)
    code, _out, err = lauf(capsys, ["bau", str(tmp_path / "niemand")])
    assert code == 2 and "existiert nicht" in err
    # 2. --auftrag-datei fehlt (bau auf gesundem Projekt)
    ordner, _dekl = baue_projekt(tmp_path)
    code, _out, err = lauf(capsys, ["bau", str(ordner),
                                    "--auftrag-datei", str(tmp_path / "x.md")])
    assert code == 2 and "Auftrag-Datei existiert nicht" in err
    # 3. KONTEXT.json ist kein gültiges JSON
    (ordner / "KONTEXT.json").write_text("{ kaputt", encoding="utf-8")
    code, _out, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 2 and "kein gültiges JSON" in err
    # 4. pruefe auf Paketdatei ohne Manifest
    blind = tmp_path / "blind.md"
    blind.write_text("# Paket\n\nkein Manifest hier\n", encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(blind)])
    assert code == 2 and "ohne kontext-manifest-Block" in err
    # 5. kandidaten mit fehlendem Merkzettel-Ordner (eigener Ordner, ohne KONTEXT.json)
    frei = tmp_path / "frei"
    frei.mkdir()
    code, _out, err = lauf(capsys, ["kandidaten", str(frei), "--auftrag", "Auftragsbär",
                                    "--merkzettel", str(tmp_path / "keinmerk")])
    assert code == 2 and "existiert nicht" in err
    # 6. protokoll weder Ordner noch --alle
    code, _out, err = lauf(capsys, ["protokoll"])
    assert code == 2

# === U1b-NACHBESSERUNG: §11.12 (Vertragsprüfung V1) ==============================


# --- B1 (§11.12 Punkt 1): Grenze gilt für die ganze Datei --------------------------

def test_1112_b1_grenze_misst_ganzes_paket_ohne_ausgabe(tmp_path, capsys):
    """Ohne --ausgabe misst --max-zeichen Blöcke 1–10 inkl. Manifest (B1)."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0
    ganze = len(paket)
    # Grenze knapp unter der Paketgröße: Pflichtteil allein wäre darunter,
    # aber B1 misst das ganze Paket → Exit 3
    code, out, err = lauf(capsys, ["bau", str(ordner),
                                   "--max-zeichen", str(ganze - 50)])
    assert code == 3
    assert out == ""
    # über der Grenze steht die Paketgröße, nicht der kleinere Pflichtteil
    assert str(ganze - 50) in err
    code, out, _err = lauf(capsys, ["bau", str(ordner),
                                    "--max-zeichen", str(ganze + 50)])
    assert code == 0 and "kontext-manifest" in out


def test_1112_b1_mit_ausgabe_jede_datei_komplett_unter_grenze(tmp_path, capsys):
    """Mit --ausgabe zählt jede Teildatei inkl. Kopf, Block 9 und Manifest (B1)."""
    ordner, _dekl = baue_projekt(tmp_path, progress=progress_mit_langen_bedingungen())
    grenze = 2600
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(grenze),
                                    "--ausgabe", str(tmp_path / "teile")])
    assert code in (0, 1)
    teile = sorted((tmp_path / "teile").glob("paket*.md"))
    assert len(teile) >= 2
    for datei in teile:
        text = datei.read_text(encoding="utf-8")
        assert len(text) <= grenze, f"{datei.name} hat {len(text)} Zeichen"
        # Kopf (Block 1) und Manifest sind in jeder Teildatei
        assert "# Kontextpaket —" in text
        assert "```kontext-manifest" in text
    # Block 9 steht im letzten Teil
    assert "## Zusatz — Fundstellen" in teile[-1].read_text(encoding="utf-8")


# --- B2 (§11.12 Punkt 2): Volltext wird geprüft --------------------------------------

def test_1112_b2_volltext_geloescht_text_fehlt(tmp_path, capsys):
    """Für volltext-Einheiten muss Kopf+Rumpf in Block 6 stehen (B2).
    §11.15 Punkt 12 (R1-11): der Löschtest isoliert den Volltext — nur Block 6
    wird verändert, Block 5 (Übersicht) bleibt unangetastet."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0
    ziel = "Das Werkzeug liest nur und schreibt nie."
    start6 = paket.index("## Entscheidungen — Volltext, auftragsbezogen")
    grenzen = [paket.find(k, start6) for k in
               ("## Entscheidungen — nicht gültig", "## Pflicht-Zusatzdateien",
                "## Zusatz — Fundstellen", "```kontext-manifest")]
    ende6 = min(g for g in grenzen if g != -1)
    block6 = paket[start6:ende6]
    assert ziel in block6
    (tmp_path / "paket.md").write_text(
        paket[:start6] + block6.replace(ziel, "[gelöscht]", 1) + paket[ende6:],
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(tmp_path / "paket.md"),
                                    "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err
    assert "Volltext" in err
    assert "Übersichtszeile steht nicht" not in err   # Block 5 blieb ganz


def test_1115_12_nur_block5_veraendert_uebersicht_befund(tmp_path, capsys):
    """§11.15 Punkt 12 (R1-11), umgekehrter Fall: nur die Übersichtszeile in
    Block 5 verändern → Übersicht-Befund, kein Volltext-Befund."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0
    ziel = "Das Werkzeug liest nur und schreibt nie."
    start5 = paket.index("## Entscheidungen (gültig) — Übersicht")
    ende5 = paket.index("## Entscheidungen — Volltext, auftragsbezogen", start5)
    block5 = paket[start5:ende5]
    assert ziel in block5
    (tmp_path / "paket.md").write_text(
        paket[:start5] + block5.replace(ziel, "[gelöscht]", 1) + paket[ende5:],
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(tmp_path / "paket.md"),
                                    "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err
    assert "Übersichtszeile steht nicht" in err
    assert "Volltext (Kopf+Rumpf) steht nicht" not in err   # Block 6 blieb ganz


def test_1112_b2_volltext_da_sauber(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    (tmp_path / "paket.md").write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(tmp_path / "paket.md"),
                                    "--projekt", str(ordner)])
    assert code == 0, err


# --- B3 (§11.12 Punkt 3): pruefe liest wie bau — QUELLE_FEHLT hart --------------------

def test_1112_b3_pruefe_quelle_fehlt_hart(tmp_path, capsys):
    """Fehlt eine Datei aus quellen, meldet pruefe QUELLE_FEHLT hart (B3)."""
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    (ordner / "ARBEITSWEISE.md").unlink()
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "QUELLE_FEHLT" in err
    assert "ARBEITSWEISE.md" in err


# --- B4 (§11.12 Punkt 4): Teile erkennen — Dateiname egal ------------------------------

def test_1112_b4_teile_erkennen_dateiname_egal(tmp_path, capsys):
    """Zusammengehörige Teile über Manifest-Dreisatz, nicht über Dateinamen (B4)."""
    ordner, _dekl = baue_projekt(tmp_path, progress=progress_mit_langen_bedingungen())
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(GRENZE),
                                    "--ausgabe", str(tmp_path / "teile")])
    assert "Traceback" not in err
    teile = sorted((tmp_path / "teile").glob("paket-*-von-*.md"))
    assert len(teile) >= 2
    # Umbenennen darf die Erkennung nicht brechen
    teile[0].rename(teile[0].parent / "ganz-anderer-name.md")
    erster = teile[0].parent / "ganz-anderer-name.md"
    code, _out, err = lauf(capsys, ["pruefe", str(erster), "--projekt", str(ordner)])
    assert code == 0, err
    # Fehlt eine Nummer → TEIL_FEHLT
    teile[-1].unlink()
    code, _out, err = lauf(capsys, ["pruefe", str(erster), "--projekt", str(ordner)])
    assert code == 1
    assert "TEIL_FEHLT" in err


def test_1112_b4_jeder_teil_beginnt_mit_kopf_und_vermerk(tmp_path, capsys):
    """Jeder Teil beginnt mit Teilvermerk und Kopf; UNKLAR in Teil 1, Block 9 im letzten."""
    fremdzeile = "- Verunglückte Zeile hier."
    progress = progress_mit_langen_bedingungen().replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung",
        f"{fremdzeile}\n- [2026-09-02 | aktiv] Zweite Bedingung")
    ordner, _dekl = baue_projekt(tmp_path, progress=progress)
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(GRENZE),
                                    "--ausgabe", str(tmp_path / "teile")])
    teile = sorted((tmp_path / "teile").glob("paket-*-von-*.md"))
    von = len(teile)
    for k, datei in enumerate(teile, 1):
        text = datei.read_text(encoding="utf-8")
        assert text.startswith(f"TEIL {k} VON {von}")
        assert "# Kontextpaket —" in text  # Block-1-Kopf in jedem Teil
    assert "## UNKLAR" in teile[0].read_text(encoding="utf-8")
    if von > 1:
        assert "## UNKLAR" not in teile[1].read_text(encoding="utf-8")
    assert "## Zusatz — Fundstellen" in teile[-1].read_text(encoding="utf-8")


# --- B5 (§11.12 Punkt 5): offener Codeblock ist hart -----------------------------------

def test_1112_b5_offener_zaun_hart_eintraege_dahinter_kommen(tmp_path, capsys):
    """Dateiende mit offenem Zaun: hart, und ab dem Zaun wird ohne ihn gelesen (B5)."""
    progress = PROGRESS_GESUND.replace(
        "## Entscheidungen (append-only, nicht zusammenfassen)",
        "```\nEin Codeblock, der nie geschlossen wird.\n\n"
        "- [2026-09-07 | aktiv] Erste echte Zeile hinter dem offenen Zaun.\n\n"
        "## Entscheidungen (append-only, nicht zusammenfassen)")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 1
    assert "CODEBLOCK_NIE_GESCHLOSSEN" in err
    # ab dem offenen Zaun wird so gelesen, als gäbe es ihn nicht: die Bedingung
    # hinter dem Zaun landet trotzdem im Paket
    man = lade_manifest(paket)
    assert len([e for e in man["einheiten"] if e["art"] == "bedingung"]) == 3
    assert "Erste echte Zeile hinter dem offenen Zaun." in paket


def test_1112_b5_offener_zaun_sauber_geschlossener_block_still(tmp_path, capsys):
    codeblock = "```\nnur ein Beispiel\n```\n"
    progress = PROGRESS_GESUND.replace(
        "## Entscheidungen (append-only, nicht zusammenfassen)",
        codeblock + "\n## Entscheidungen (append-only, nicht zusammenfassen)")
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "CODEBLOCK_NIE_GESCHLOSSEN" not in err


def test_1112_b5_offener_zaun_bei_protokoll(tmp_path, capsys):
    progress = PROGRESS_GESUND + "\n```\noffen\n"
    ordner, _dekl = baue_projekt(tmp_path, progress=progress)
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 1
    assert "CODEBLOCK_NIE_GESCHLOSSEN" in err


# --- §11.14 (Entscheidungen) / §11.12 Punkt 6 (nur noch Bedingungen): Status ------------

def test_1114_gueltig_mit_revidiert_bleibt_gueltig_weich(tmp_path, capsys):
    """„gültig — revidiert durch E-009“ ist gültig mit weichem Hinweis (§11.14).

    Ersetzt den früheren test_1112_b6_storno_zuerst_vor_gueltig: §11.14 kehrt
    die Reihenfolge von §11.12 Punkt 6 für Entscheidungen um — „gültig —
    revidiert …“ heißt, diese Entscheidung hebt eine andere auf. Das Statusfeld
    (B15) dieser Schreibweise bleibt wörtlich „gültig“ (Cut an der ersten Folge
    „ — “); der Wortlaut selbst bleibt über den Kopf-Hash sichtbar
    (EINHEIT_GEAENDERT).
    """
    progress = PROGRESS_GESUND.replace(
        "### E-001 | 2026-09-03 | gültig",
        "### E-001 | 2026-09-03 | gültig — revidiert durch E-009")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0                       # nur weiche Befunde
    assert "STATUS_UNKLAR" not in err
    assert err.count("TEILWEISE_REVIDIERT") == 1
    assert ("Status „gültig — revidiert durch E-009“ nennt revidiert" in err
            )  # ganzer Wortlaut ab dem Status (§13.7 Punkt 1)
    man = lade_manifest(paket)
    e1 = next(e for e in man["einheiten"] if e["id"] == "E-001")
    assert e1["status"] == "gültig"
    # gültig: bleibt in Block 5 (§11.14) …
    uebersicht = paket[paket.index("## Entscheidungen (gültig) — Übersicht"):
                       paket.index("## Pflicht-Zusatzdateien")]
    assert "E-001 (2026-09-03)" in uebersicht
    # … und steht nicht im „nicht gültig“-Block (dort steht nur E-002;
    # „revidiert durch E-001“ in dessen Zeile ist nur der Verweis)
    block7 = paket[paket.index("## Entscheidungen — nicht gültig"):
                   paket.index("## Pflicht-Zusatzdateien")]
    assert not re.search(r"^E-001 — ", block7, re.M)


def test_137_teilweise_revidiert_meldung_zitiert_ganzen_wortlaut(tmp_path, capsys):
    """Meldung zitiert den ganzen Wortlaut ab dem Status (§13.7 Punkt 1).

    „### E-002 | … | gültig — Text (revidiert E-001)“: Die Meldung muss
    „(revidiert E-001)“ enthalten — der alte Cut am Statusfeld („gültig“)
    verschluckte genau diesen Verweis. Einstufung (weich, gültig) und
    Zählung (genau 1×) bleiben unverändert.
    """
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        "### E-002 | 2026-09-04 | gültig — Text (revidiert E-001)")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0                       # nur weiche Befunde
    assert "STATUS_UNKLAR" not in err
    assert err.count("TEILWEISE_REVIDIERT") == 1
    assert "(revidiert E-001)" in err
    assert ("Status „gültig — Text (revidiert E-001)“ nennt revidiert" in err
            )  # ganzer Wortlaut, nicht nur das Statusfeld
    man = lade_manifest(paket)
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "gültig"        # Einstufung unverändert (§11.14)


def test_1112_b6_wortgrenzen_ueberholt_in_anderem_wort_sticht_nicht(tmp_path, capsys):
    # „ueberholt“ als Teil eines längeren Worts ist kein Storno; ein echtes
    # Stornowort als Ganzes auch über Fettschrift/Großschreibung hinweg
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        "### E-002 | 2026-09-04 | **Überholt durch E-001**")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    man = lade_manifest(paket)
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "Überholt durch E-001"


def test_1112_b6_bedingung_aufgehoben_mit_wieder_gilt_mit_unklar(tmp_path, capsys):
    """„aufgehoben“ zusammen mit „wieder“/„nicht“: gilt + weich STATUS_UNKLAR (B6)."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe",
        "- [2026-09-01 | nicht aufgehoben] Committe")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0  # gilt weiter, nur weicher Befund
    assert "STATUS_UNKLAR" in err
    assert "Committe nichts selbst, frag vorher." in paket
    man = lade_manifest(paket)
    gelten = [e for e in man["einheiten"]
              if e["art"] == "bedingung" and e["status"] == "gilt"]
    assert len(gelten) == 2


def test_1112_b6_bedingung_wiederaufnahme_ist_nicht_wieder(tmp_path, capsys):
    # Wortgrenzen: „Wiederaufnahme“ enthält „wieder“ nicht als ganzes Wort —
    # also ist die Bedingung aufgehoben, ohne weichen STATUS_UNKLAR-Befund
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe",
        "- [2026-09-01 | aufgehoben bis Wiederaufnahme] Committe")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    man = lade_manifest(paket)
    aufgehoben = [e for e in man["einheiten"]
                  if e["art"] == "bedingung" and e["status"] == "aufgehoben"]
    assert len(aufgehoben) == 1


# --- §11.14 Pflichttest: neutralisierte Kopfzeilen (gültig + TEILWEISE_REVIDIERT) -------

# Nachgebaut nach echten Kopfzeilen aus mehreren Projekten (Wortlaut neutralisiert,
# Form erhalten). Projekt-a bis Projekt-d decken verschiedene Statusvarianten ab;
# alle ergeben gültig mit weichem TEILWEISE_REVIDIERT (§11.14).

PROJEKT_A_KOPFZEILEN = [
    '### E-012 | 2026-09-08 | gueltig — **revidiert E-005**',
    '### E-013 | 2026-09-09 | gueltig — **aber die Fassung ist ueberholt: '
    'E-016 hat die Reihenfolge der Beispiele angepasst**',
    '### E-024 | 2026-09-09 | gueltig — **NACHTRAG 14.09.: revidiert durch '
    'E-046** — die Regel gilt weiter fuer die bestehenden Eintraege, das '
    'Dokument darf aber neue Abschnitte bekommen',
    '### E-030 | 2026-09-11 | gueltig — **revidiert E-021 teilweise**',
    '### E-034 | 2026-09-11 | gueltig — **revidiert E-031**',
    '### E-046 | 2026-09-14 | gueltig — **revidiert E-021 und E-024 fuer '
    'Abschnitt 1; ersetzt fuer Abschnitt 1 die Sortierregel aus E-016**',
    '### E-048 | 2026-09-16 | gueltig — **wendet die Regel aus E-046 auf '
    'Abschnitt 2 an; revidiert damit die Sortierung aus E-044** (dort „etwa '
    'drei Muster / vier bis sechs Beispiele“). Der uebrige Inhalt von E-044 '
    'bleibt unberuehrt. Das alte Gate `tests/test_beispiel02.py` prueft seit '
    'dem 16.09. nur noch die Untergrenze, damit nicht zwei Gates einander '
    'widersprechen.',
    '### E-068 | 2026-09-23 | gueltig, Auswahl fuer kuenftige Vorlagen revidiert '
    'durch E-069 — **ergaenzt E-035 („Fehlendes ergaenzt die Redaktion im '
    'selben Format nach") um einen Weg, wenn die Redaktion nicht erreichbar '
    'ist; hebt E-035 NICHT auf**',
    '### E-069 | 2026-09-23 | gueltig, „Mustervorlage" revidiert durch E-071 — '
    '**revidiert in E-068 nur die Dateivorlage fuer kuenftige Beispiele**',
    '### E-071 | 2026-09-23 | gueltig — **revidiert in E-069 nur „mit einem '
    'neutralen Muster als Vorlage"; Auswahl bleibt bei Format B aus der '
    'Beispielsammlung**',
]

PROJEKT_B_KOPFZEILEN = [
    '### E-007 | 2026-09-05 | gültig, Halbsatz „Reihenfolge folgt dem Muster" '
    'revidiert durch E-016 (hatte im anderen Protokoll keine E-Nummer, stand '
    'dort nur als Bedingung 10:2x)',
    '### E-016 | 2026-09-07 | gültig (revidiert den Halbsatz „Reihenfolge folgt '
    'dem Muster" aus E-007)',
    '### E-021 | 2026-09-08 | gueltig (revidiert E-020 Schritt 3 — E-020 gilt '
    'nur noch fuer Eintraege unter vier Zeilen)',
    '### E-031 | 2026-09-09 | gueltig — **Teilsatz „Vorlage bleibt '
    'muster:alt“ revidiert durch E-033 (Format B ist Standard, Format A '
    'Rueckfall); der Struktur-Baustein selbst gilt unveraendert**',
]

PROJEKT_C_KOPFZEILEN = [
    '### E-024 | 2026-09-14 | gültig — Die Liste nutzt wieder Einzüge (revidiert E-020)',
    '### E-045 | 2026-09-19 | gültig, Schreibweise der Zahlen revidiert durch '
    'E-046 — Beispielliste: vier Einträge, Texte wörtlich übernommen',
]

PROJEKT_D_KOPFZEILEN = [
    '### E-083 | 2026-08-31 | gültig (revidiert E-082)',
    '### E-124 | 2026-09-10 | gültig für PRÜFUNGEN; der Hinweis-Teil ist '
    '**revidiert durch E-125** (Hinweise kommen doch aus Datei B statt Datei A, '
    'weil Datei A die Spalte nicht führt und die Beispieldaten in B vollständig '
    'sind). Der in „Prüfbar durch" genannte Test „kein format-b-Adapter im Code" '
    'gilt damit NICHT und wurde bewusst nicht gebaut — an seine Stelle trat der '
    'Test aus E-126 (kein `format-b:`-Eintrag mit `kind: alpha`/`beta`)',
    '### E-128 | 2026-09-13 | gültig, Teil „`modus: "test"` fest" revidiert '
    'durch E-129',
]


def _progress_mit_kopfzeilen(kopfzeilen):
    """Gesundes Protokoll + neutralisierte Kopfzeilen mit Mindest-Rumpf."""
    eintraege = []
    for z in kopfzeilen:
        eintraege += [z,
                      "- **Entscheidung:** Einstellung für den Beispielfall.",
                      "- **Verworfene Alternative:** Keine.",
                      "- **Grund:** Form der Kopfzeile nachgebaut; Wortlaut neutralisiert.",
                      "- **Betroffene Pfade:** docs/",
                      "- **Prüfbar durch:** pytest werkzeuge/tests -q", ""]
    return PROGRESS_GESUND + "\n## Entscheidungen — neutralisierte Fälle (§11.14)\n\n" \
        + "\n".join(eintraege)


def _pruefe_echte_kopfzeilen(capsys, tmp_path, kopfzeilen):
    code, paket, err, _ordner = baue_paket(
        capsys, tmp_path, progress=_progress_mit_kopfzeilen(kopfzeilen))
    assert code == 0                       # TEILWEISE_REVIDIERT ist weich
    assert "STATUS_UNKLAR" not in err
    assert err.count("TEILWEISE_REVIDIERT") == len(kopfzeilen)
    nummern = {f"E-{int(re.match(r'^#{2,4}\s+E-(\d{3,})', z).group(1)):03d}"
               for z in kopfzeilen}
    man = lade_manifest(paket)
    for e in man["einheiten"]:
        if e["id"] in nummern:
            assert e["status"] == "gültig", e
    # alle in Block 5 (Übersicht), keiner im „nicht gültig“-Block (§11.14)
    uebersicht = paket[paket.index("## Entscheidungen (gültig) — Übersicht"):
                       paket.index("## Pflicht-Zusatzdateien")]
    for n in nummern:
        assert re.search(rf"^{n} \(\d{{4}}-\d{{2}}-\d{{2}}\)", uebersicht, re.M)
    # Block 7 existiert (E-002 des gesunden Protokolls) — aber keine der
    # echten Kopfzeilen gehört hinein
    block7 = paket[paket.index("## Entscheidungen — nicht gültig"):
                   paket.index("## Pflicht-Zusatzdateien")]
    for n in nummern:
        assert not re.search(rf"^{n} — ", block7, re.M)
    assert "## UNKLAR" not in paket
    return paket, err


def test_1114_echte_kopfzeilen_projekt_a(tmp_path, capsys):
    _pruefe_echte_kopfzeilen(capsys, tmp_path, PROJEKT_A_KOPFZEILEN)


def test_1114_echte_kopfzeilen_projekt_b(tmp_path, capsys):
    _pruefe_echte_kopfzeilen(capsys, tmp_path, PROJEKT_B_KOPFZEILEN)


def test_1114_echte_kopfzeilen_projekt_c(tmp_path, capsys):
    _pruefe_echte_kopfzeilen(capsys, tmp_path, PROJEKT_C_KOPFZEILEN)


def test_1114_echte_kopfzeilen_projekt_d(tmp_path, capsys):
    _paket, err = _pruefe_echte_kopfzeilen(capsys, tmp_path, PROJEKT_D_KOPFZEILEN)
    # E-083: Statusfeld ohne „ — “ → im Hinweis vollständig wörtlich
    assert "Status „gültig (revidiert E-082)“ nennt revidiert" in err


def test_1114_gegenfall_revidiert_durch_e006(tmp_path, capsys):
    """Wörtlich aus §11.14: „### E-001 | 2026-09-12 | revidiert durch E-006“."""
    code, paket, err, _ordner = baue_paket(
        capsys, tmp_path,
        progress=PROGRESS_GESUND.replace(
            "### E-001 | 2026-09-03 | gültig",
            "### E-001 | 2026-09-12 | revidiert durch E-006"))
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    assert "TEILWEISE_REVIDIERT" not in err
    assert "E-001 — revidiert durch E-006 · Zeile" in paket
    # E-001 und E-002 sind beide nicht gültig → Block 5 (Übersicht) entfällt
    assert "## Entscheidungen (gültig) — Übersicht" not in paket


def test_1114_gegenfall_revidiert_fettschrift(tmp_path, capsys):
    """Wörtlich aus §11.14: Statusfragment „**revidiert durch E-009**“."""
    code, paket, err, _ordner = baue_paket(
        capsys, tmp_path,
        progress=PROGRESS_GESUND.replace(
            "### E-002 | 2026-09-04 | revidiert durch E-001",
            "### E-002 | 2026-09-04 | **revidiert durch E-009**"))
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    assert "TEILWEISE_REVIDIERT" not in err
    # B15: Block 7 zeigt das Statusfeld — Sterne entfernt, sonst wörtlich
    assert "E-002 — revidiert durch E-009 · Zeile" in paket


# --- B7 (§11.12 Punkt 7): kein Wegweiser, STAND_NICHT_DEKLARIERT -----------------------

def test_1112_b7_stand_schluessel_fehlt_hart_bei_bau(tmp_path, capsys):
    dekl = json.loads(json.dumps(DEKL_GESUND))
    del dekl["stand"]
    code, paket, err, ordner = baue_paket(capsys, tmp_path, dekl=dekl)
    assert code == 1
    assert "STAND_NICHT_DEKLARIERT" in err
    # der Block heißt genau so — ohne Wegweiser auf andere Dateien
    assert "STAND NICHT DEKLARIERT — selbst lesen: PROGRESS.md" in paket


def test_1112_b7_stand_schluessel_fehlt_weich_bei_protokoll(tmp_path, capsys):
    dekl = json.loads(json.dumps(DEKL_GESUND))
    del dekl["stand"]
    ordner, _d = baue_projekt(tmp_path, dekl=dekl)
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0
    assert "STAND_NICHT_DEKLARIERT" in err


def test_1112_b7_blockname_ohne_wegweiser_bei_keiner_deklaration(tmp_path, capsys):
    code, paket, _err, _ordner = baue_paket(capsys, tmp_path, dekl=None)
    assert "STAND NICHT DEKLARIERT — selbst lesen: PROGRESS.md" in paket
    assert "ARBEITSWEISE.md\n" not in paket.split("STAND NICHT DEKLARIERT")[1][:80]


# --- B12 (§11.12 Punkt 8): Stoppwortliste ist Vertrag ------------------------------------

VERTRAGS_STOPWOERTER = [
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


def test_1112_b12_stoppwoerter_sha_im_manifest():
    """Die Liste im Code ist wörtlich die Vertragsliste; der SHA landet im Manifest."""
    assert sorted(kp.STOPWOERTER) == sorted(VERTRAGS_STOPWOERTER)
    erwartet = hashlib.sha256("\n".join(VERTRAGS_STOPWOERTER)
                              .encode("utf-8")).hexdigest()[:12]
    assert kp.STOPPOERTER_SHA == erwartet


def test_1112_b12_stoppwoerter_sha_stellt_sich_ins_manifest(tmp_path, capsys):
    _code, paket, _err, _ordner = baue_paket(capsys, tmp_path)
    man = lade_manifest(paket)
    assert man["stoppwoerter_sha"] == kp.STOPPOERTER_SHA


def test_1112_b12_werkzeug_ist_stoppwort(tmp_path, capsys):
    """„Werkzeug“ zählt nicht als Suchwort (B12)."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, _err = lauf(capsys, ["bau", str(ordner),
                                    "--auftrag", "Werkzeug"])
    assert code == 0
    assert "Volltextgrund" not in out  # keine Suchwörter → keine Vorauswahl


# --- B13 (§11.12 Punkt 9): eingerückt = Leerzeichen oder Tab ---------------------------

def test_1112_b13_tab_eingerueckt_ist_fortsetzung(tmp_path, capsys):
    """Eingerückt heißt: beginnt mit Leerzeichen oder Tab (B13) — hier Tab."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung, die über\n"
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "- [2026-09-02 | aktiv] Zweite Bedingung, die über\n"
        "\tmehrere Zeilen (mit Tab eingerückt) geht und wörtlich bleibt.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    assert "mehrere Zeilen (mit Tab eingerückt) geht und wörtlich bleibt." in paket


def test_1112_b13_un_eingerueckt_beginnt_nicht_mit_zeichen_zaehlt_mit(tmp_path, capsys):
    # Eine Fortsetzungszeile, die mit „- “ beginnt, ist kein Fortsetzen mehr (B13)
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n- Keine Fortsetzung mehr.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 1
    assert "FORMAT_UNKLAR" in err  # beginnt mit „- “, kein Eintragskopf (§11.13.2)


# --- B14 (§11.12 Punkt 10): Rumpfende vor jedem Entscheidungs-Kopf ---------------------

def test_1112_b14_rumpf_endet_vor_kopf_jeder_ebene(tmp_path, capsys):
    """Ein ####-Kopf innerhalb eines ###-Eintrags beendet den Rumpf (B14)."""
    weiter = """### E-010 | 2026-09-05 | gültig
- **Entscheidung:** Äußere Entscheidung mit tiefem Nachfolger.
- **Verworfene Alternative:** Ebenen mischen.
- **Grund:** Rumpf muss vor jedem Kopf enden.
- **Betroffene Pfade:** docs/
- **Prüfbar durch:** pytest

#### E-011 | 2026-09-06 | gültig
- **Entscheidung:** Innere Entscheidung auf tieferer Ebene.
- **Verworfene Alternative:** Nicht anlegen.
- **Grund:** Tiefe Ebenen kommen vor.
- **Betroffene Pfade:** docs/
- **Prüfbar durch:** pytest
"""
    progress = PROGRESS_GESUND + weiter
    code, paket, err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0, err
    assert "NUMMER_DOPPELT" not in err
    man = lade_manifest(paket)
    e10 = next(e for e in man["einheiten"] if e["id"] == "E-010")
    e11 = next(e for e in man["einheiten"] if e["id"] == "E-011")
    # E-011 ist eine eigene Einheit mit eigener Zeile, nicht Rumpf von E-010
    assert e11["zeile"] == e10["zeile"] + 7
    voll = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-010"])[1]
    # E-011s eigene Rumpffelder gehören nicht zu E-010 (Rumpf endet vorher);
    # die Übersichtszeile von E-011 darf trotzdem da sein.
    assert "Nicht anlegen." not in voll
    assert "Äußere Entscheidung mit tiefem Nachfolger." in voll
    assert "Ebenen mischen." in voll


# --- B15/B16 (§11.12 Punkte 11/12): Statusfeld und Manifest -----------------------------

def test_1112_b15_statusfeld_endet_an_der_ersten_gedankenstrich_folge(tmp_path, capsys):
    """Statusfeld = Rest bis vor die erste Folge „ — “; Block 7 zeigt es (B15)."""
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        "### E-002 | 2026-09-04 | revidiert durch E-001 — ausführliche Begründung hier")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" not in err
    assert "E-002 — revidiert durch E-001 · Zeile" in paket
    assert "ausführliche Begründung hier · Zeile" not in paket  # abgeschnitten
    man = lade_manifest(paket)
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "revidiert durch E-001"  # B16: Statusfeld im Manifest


def test_1112_b16_manifest_enthält_aufgehoben_und_nicht_gueltige_mit_statusfeld(
        tmp_path, capsys):
    """Manifest-Einheiten tragen aufgehobene Bedingungen und Statusfelder (B16)."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "- [2026-09-01 | aufgehoben 2026-09-20] Committe nichts selbst, frag vorher.")
    code, paket, _err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    man = lade_manifest(paket)
    aufgehoben = [e for e in man["einheiten"]
                  if e["art"] == "bedingung" and e["status"] == "aufgehoben"]
    assert len(aufgehoben) == 1
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "revidiert durch E-001"  # Statusfeld, nicht pauschal
    # quellen enthält jetzt auch KONTEXT.json (B16)
    dateien = [q["datei"] for q in man["quellen"]]
    assert dateien == ["PROGRESS.md", "ARBEITSWEISE.md", "KONTEXT.json"]


def test_1112_b16_status_aenderung_in_beide_richtungen(tmp_path, capsys):
    """Status-Glitch in beide Richtungen: revidiert-E-Text geändert → gemeldet (B16)."""
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        "### E-002 | 2026-09-04 | revidiert durch E-099")
    code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    (ordner / "PROGRESS.md").write_text(progress.replace(
        "revidiert durch E-099", "revidiert durch E-777"), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "STATUS_GEAENDERT" in err  # Statusfeld-Wechsel greift


# --- B17 (§11.12 Punkt 13): gleiche Nummer, gleicher Rumpf ------------------------------

def test_1112_b17_entscheidung_doppelt_weich_nur_erster_im_paket(tmp_path, capsys):
    """Gleiche Nummer + gleicher Rumpf: weich ENTSCHEIDUNG_DOPPELT, erster bleibt (B17)."""
    doppelt = """### E-001 | 2026-09-03 | gültig
- **Entscheidung:** Das Werkzeug liest nur und schreibt nie.
- **Verworfene Alternative:** Schreibender Zugriff mit Rückfrage.
- **Grund:** Rein lesend ist gefahrlos (Fatih, 2026-09-01).
- **Betroffene Pfade:** werkzeuge/kontextpaket.py, werkzeuge/tests/
- **Prüfbar durch:** pytest werkzeuge/tests -q
"""
    # Leere Zeilen davor und danach — sonst unterscheidet sich der
    # normalisierte Rumpf (leere Grenzzeilen) und es wird hart NUMMER_DOPPELT.
    progress = PROGRESS_GESUND + "\n" + doppelt + "\n"
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0, err
    assert "ENTSCHEIDUNG_DOPPELT" in err
    assert "NUMMER_DOPPELT" not in err
    assert paket.count("E-001 (2026-09-03)") == 1  # nur der erste in der Übersicht
    man = lade_manifest(paket)
    assert [e["id"] for e in man["einheiten"]].count("E-001") == 1


def test_1112_b17_entscheidung_doppelt_sauber(tmp_path, capsys):
    _code, _paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "ENTSCHEIDUNG_DOPPELT" not in err


# --- B18 (§11.12 Punkt 14): STAND_OHNE_ENDE härte ---------------------------------------
# (bei bau hart, bei protokoll weich — geprüft in
# test_stand_ohne_ende_hart_bei_bau_weich_bei_protokoll oben, angepasst)


# --- B19 (§11.12 Punkt 15): AUFTRAGSBEZUG_LEER und Hinweiszeilen -------------------------

def test_1112_b19_genannte_nummer_ohne_treffer_weich(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-999"])
    assert code == 0  # weich
    assert "AUFTRAGSBEZUG_LEER" in err
    assert "E-999" in err
    # Der unbekannte Eintrag erscheint nirgends als Volltext
    assert "### E-999" not in out


def test_1112_b19_pfad_ohne_treffer_weich_sauber(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--pfad", "gibt/es/nicht.py"])
    assert code == 0
    assert "AUFTRAGSBEZUG_LEER" in err
    # Sauberfall: echter Pfad-Treffer meldet nichts
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--pfad", "kontextpaket.py"])
    assert code == 0
    assert "AUFTRAGSBEZUG_LEER" not in err


def test_1112_b19_ohne_jeden_bezug_hinweiszeilen_in_bloecken_6_und_9(tmp_path, capsys):
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "kein Auftrag gegeben — Block entfällt" in paket
    assert paket.count("kein Auftrag gegeben — Block entfällt") == 2  # Block 6 UND 9


def test_1112_b19_merkzettel_fehlender_ordner_exit_2(tmp_path, capsys):
    """§16.1 Punkt 2c ersetzt B19: --merkzettel gibt es jetzt auch bei bau —
    ein fehlender Ordner ist Aufruffehler (Exit 2) wie bei kandidaten."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, _out, err = lauf(capsys, ["bau", str(ordner),
                                    "--merkzettel", str(tmp_path / "merk")])
    assert code == 2 and "existiert nicht" in err
    code, _out, _err = lauf(capsys, ["kandidaten", str(ordner),
                                     "--auftrag", "Auftragsbär",
                                     "--merkzettel", str(tmp_path / "merk")])
    assert code == 2  # Merkzettel-Ordner fehlt
    # --ohne-merkzettel und --merkzettel schließen sich aus (argparse-Gruppe)
    (tmp_path / "merk").mkdir()
    with pytest.raises(SystemExit) as excinfo:
        kp.main(["bau", str(ordner), "--ohne-merkzettel",
                 "--merkzettel", str(tmp_path / "merk")])
    assert excinfo.value.code == 2


# --- B20 (§11.12 Punkt 16): Determinismus maskiert beide Zeitstempel ----------------------

def test_1112_b20_determinismus_maskiert_kopf_und_manifest_zeit(tmp_path, capsys):
    """Beide Zeitstempel (Kopfzeile und Manifest-erzeugt) werden maskiert (B20)."""
    _c1, p1, _e1, _o = baue_paket(capsys, tmp_path)
    _c2, p2, _e2, _o = baue_paket(capsys, tmp_path, neu=False)
    kopf_muster = re.compile(r"- Erzeugt: .*")
    manifest_muster = re.compile(r'"erzeugt": "[^"]*"')
    # beide Muster greifen wirklich — sonst maskiert der Test nichts
    assert kopf_muster.search(p1) and manifest_muster.search(p1)
    assert kopf_muster.sub("", p1) == kopf_muster.sub("", p2)
    assert manifest_muster.sub("", p1) == manifest_muster.sub("", p2)


# --- B27 (§11.12 Punkt 18): kaputtes Manifest → Exit 2 ------------------------------------

def test_1112_b27_manifest_ungueltig_json_exit_2(tmp_path, capsys):
    _code, paket, _err, _ordner = baue_paket(capsys, tmp_path)
    kaputt = paket.replace('{\n  "format": 1,', '{\n  "format": 1, "kaputt": ,', 1)
    assert kaputt != paket
    pfad = tmp_path / "kaputt.md"
    pfad.write_text(kaputt, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad)])
    assert code == 2
    assert "kein gültiges JSON" in err


def test_1112_b27_manifest_unbekanntes_format_exit_2(tmp_path, capsys):
    _code, paket, _err, _ordner = baue_paket(capsys, tmp_path)
    anderes = paket.replace('"format": 1', '"format": 99', 1)
    assert anderes != paket
    pfad = tmp_path / "fremd.md"
    pfad.write_text(anderes, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad)])
    assert code == 2
    assert "unbekanntes format" in err


# --- B11 (§11.12 Punkt 19): Kandidaten tragen quelle relativ -------------------------------

def test_1112_b11_kandidaten_quelle_relativ(tmp_path, capsys):
    """Jeder Kandidat trägt „quelle“ relativ zum Projekt (B11)."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, out, _err = lauf(capsys, [
        "kandidaten", str(ordner), "--auftrag", "Kandidatentalpe Auftragsbär"])
    assert code == 0
    daten = json.loads(out)
    erster = next(k for k in daten["kandidaten"]
                  if k["ueberschrift"] == "Kandidatentalpe")
    assert erster["quelle"] == "docs/hinweis.md"  # relativ, keine Maschinenpfade


def test_1112_b11_kandidaten_quelle_merkzettel_praefix(tmp_path, capsys):
    ordner, _dekl = baue_projekt(tmp_path)
    merk = tmp_path / "merk"
    merk.mkdir()
    (merk / "heute.md").write_text("## Heute\n\nDer Auftragsbär sieht den "
                                   "Werkzeugfelsen.", encoding="utf-8")
    code, out, _err = lauf(capsys, ["kandidaten", str(ordner),
                                    "--auftrag", "Auftragsbär Werkzeugfelsen",
                                    "--merkzettel", str(merk)])
    daten = json.loads(out)
    treffer = next(k for k in daten["kandidaten"]
                   if k["ueberschrift"] == "Heute")
    assert treffer["quelle"] == "merkzettel/heute.md"


# --- §11.13 Punkt 1: Datumsfeld mit Zusatz -------------------------------------------------

def test_1113_punkt1_datum_mit_uhrzeit_wird_erkannt(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "- [2026-09-01 18:26 | aktiv] Committe nichts selbst, frag vorher.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    assert "[2026-09-01 18:26 | aktiv] Committe nichts selbst, frag vorher." in paket


def test_1113_punkt1_datum_mit_woertlichem_zusatz(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "- [2026-09-01 abends | aktiv] Committe nichts selbst, frag vorher.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    assert "abends" in paket  # Zusatz wird mitgeführt, nicht ausgewertet


def test_1113_punkt1_storno_zusatz_bleibt_erkannt(tmp_path, capsys):
    """Auch mit Zusatz im Datumsfeld greift die Storno-Erkennung (B6)."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "- [2026-09-01 19:55 | aufgehoben 2026-09-20] Committe nichts selbst, "
        "frag vorher.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    man = lade_manifest(paket)
    aufgehoben = [e for e in man["einheiten"]
                  if e["art"] == "bedingung" and e["status"] == "aufgehoben"]
    assert len(aufgehoben) == 1  # Zusatz im Datumsfeld stört nicht (§11.13.1)


# --- §11.13 Punkt 2: Anmerkungen ------------------------------------------------------------

ANMERKUNG_SNIPPET = """
> Regie-Notiz: Bedingungen nie zusammenfassen — sie sind append-only.
> Die zweite Zeile derselben Notiz.

---

"""


def test_1113_punkt2_anmerkung_woertlich_im_bedingungsblock(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + ANMERKUNG_SNIPPET)
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0, err
    assert "FORMAT_UNKLAR" not in err
    assert "> Regie-Notiz: Bedingungen nie zusammenfassen — sie sind append-only." \
        in paket
    assert "> Die zweite Zeile derselben Notiz." in paket
    assert "\n---\n" in paket  # Trennlinie wörtlich


def test_1113_punkt2_anmerkung_eine_manifest_einheit(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + ANMERKUNG_SNIPPET)
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    man = lade_manifest(paket)
    anm = [e for e in man["einheiten"] if e["art"] == "anmerkung"]
    assert len(anm) == 1  # beide Zeilen = EINE Einheit
    assert anm[0]["id"].startswith("A-")


def test_1113_punkt2_anmerkung_pruefe_geloescht_einheit_fehlt(tmp_path, capsys):
    """pruefe behandelt Anmerkungen wie Bedingungen: gelöscht → hart (§11.13.2)."""
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + ANMERKUNG_SNIPPET)
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    (ordner / "PROGRESS.md").write_text(
        progress.replace(ANMERKUNG_SNIPPET, "\n"), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "EINHEIT_FEHLT" in err


def test_1113_punkt2_anmerkung_pruefe_wortlaut_geaendert(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + ANMERKUNG_SNIPPET)
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    (ordner / "PROGRESS.md").write_text(
        progress.replace("append-only.", "append-only!"), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    # Anmerkungen tragen ihre Inhalts-SHA in der ID: geänderter Wortlaut = alte
    # Einheit verschwunden, neue unbekannt — beide Richtungen fallen hart auf.
    assert "A-2b4c5d6dfdac (anmerkung) steht nicht im Manifest" in err
    assert "steht im Manifest, ist in der Quelle aber verschwunden" in err


def test_1113_punkt2_anmerkung_pruefe_sauber(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + ANMERKUNG_SNIPPET)
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "TEXT_FEHLT" not in err


def test_1113_punkt2_fliesstext_nach_erstem_eintrag_ist_anmerkung(tmp_path, capsys):
    """Fließtext nach dem ersten Eintrag: wörtlich, keine FORMAT_UNKLAR (§11.13.2)."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung, die über\n"
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "- [2026-09-02 | aktiv] Zweite Bedingung, die über\n"
        "  mehrere Zeilen geht und wörtlich bleibt.\n"
        "\nDie zweite Bedingung stammt aus der Nachbesprechung vom Montag.")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "FORMAT_UNKLAR" not in err
    assert "Die zweite Bedingung stammt aus der Nachbesprechung vom Montag." in paket
    man = lade_manifest(paket)
    assert any(e["art"] == "anmerkung" for e in man["einheiten"])


def test_1113_punkt2_stern_zeile_ohne_kopf_bleibt_format_unklar(tmp_path, capsys):
    """Nur `- `/`*`-Zeilen ohne gültigen Kopf lösen FORMAT_UNKLAR (§11.13.2)."""
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n"
        "* Kein gültiger Eintragskopf.\n")
    code, _paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 1
    assert "FORMAT_UNKLAR" in err


def test_1113_punkt2_fliesstext_vor_erstem_eintrag_unberuehrt(tmp_path, capsys):
    """Einleitung vor dem ersten Eintrag bleibt Einleitung, keine Anmerkung."""
    _code, paket, err, _ordner = baue_paket(capsys, tmp_path)
    assert "Einträge vor dem 18.08. sind Nachbildungen." in paket
    man = lade_manifest(paket)
    assert not any(e["art"] == "anmerkung" for e in man["einheiten"])


# --- §11.13 Auslegungen (1)–(10) -------------------------------------------------------------

def test_1113_auslegung1_zaunzeilen_zaehlen_zum_codeblock(tmp_path, capsys):
    """(1) Zeilen zwischen geschlossenen Zäunen sind Code — dort wird nichts geparst."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "```\n- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.\n```")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert "Committe nichts selbst" not in paket  # im Codeblock: nicht geparst
    assert "CODEBLOCK_NIE_GESCHLOSSEN" not in err  # Zaun ist sauber geschlossen


def test_1113_auslegung2_teil_manifeste_schlank_mit_pflichtschluesseln(tmp_path, capsys):
    """(2) Jedes Teil-Manifest: projekt, erzeugt, teil, stoppwoerter_sha; quellen nur Teil 1."""
    aus = tmp_path / "aus"
    # Das Manifest enthält jetzt zusätzlich den Jev-Status (§14 Punkt 7).
    code, _out, err = lauf(capsys, ["bau", str(baue_projekt(tmp_path)[0]),
                                    "--max-zeichen", "2000", "--ausgabe", str(aus)])
    assert code == 0, err
    teile = sorted(aus.glob("paket-*.md"))
    assert len(teile) >= 2
    manifests = []
    for i, t in enumerate(teile, 1):
        man = lade_manifest(t.read_text(encoding="utf-8"))
        assert ("jev" in man) is (i == 1)
        for schluessel in ("projekt", "erzeugt", "teil", "stoppwoerter_sha"):
            assert schluessel in man, (i, schluessel)
        assert man["teil"] == {"nr": i, "von": len(teile)}
        manifests.append(man)
    assert len(manifests[0]["quellen"]) == 3  # vollständig in Teil 1
    assert all(m["quellen"] == [] for m in manifests[1:])


def test_1113_auslegung3_ueberlange_einheit_ganz_in_einem_teil(tmp_path, capsys):
    """(3) Eine Einheit größer als die Grenze: EINHEIT_UEBER_GRENZE, nie abgeschnitten."""
    lang = "- [2026-09-01 | aktiv] " + "Wort " * 300 + "\n"
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.", lang.rstrip("\n"))
    ordner = baue_projekt(tmp_path, progress=progress)[0]
    aus = tmp_path / "aus"
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", "1200",
                                    "--ausgabe", str(aus)])
    assert code == 1  # EINHEIT_UEBER_GRENZE ist hart (§11.8) …
    teile = sorted(aus.glob("paket-*.md"))
    assert teile
    alles = "".join(t.read_text(encoding="utf-8") for t in teile)
    assert "EINHEIT_UEBER_GRENZE" in err     # … gemeldet (stderr/gesamtbefund),
    assert "Wort Wort" in alles              #    aber nie abgeschnitten
    assert alles.count("Wort") >= 300        # die lange Einheit ist komplett da


def test_1113_auslegung4_mittelweg_bekannter_befund_weich_neuer_hart(tmp_path, capsys):
    """(4) pruefe: bekannte Parser-Befunde weich („schon beim Bau bekannt“), neue hart."""
    verunglueckt = "* Verunglückter Eintrag ohne Kopf."
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n" + verunglueckt)
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert "FORMAT_UNKLAR" in _err  # beim Bau bekannt
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    # noch einmal dasselbe Projekt, unverändert → bekannt, weich, Exit 0
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "schon beim Bau bekannt" in err
    # eine ZWEITE, neue Verunglückung → hart, Exit 1
    (ordner / "PROGRESS.md").write_text(
        progress.replace("  mehrere Zeilen geht und wörtlich bleibt.",
                         "  mehrere Zeilen geht und wörtlich bleibt.\n"
                         + verunglueckt + "\n* Zweite, neue Verunglückung."),
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "schon beim Bau bekannt" in err   # die erste (Zeile 19) bleibt weich …
    assert "FORMAT_UNKLAR: PROGRESS.md:21" in err  # … die neue (21) ist hart


def test_1113_auslegung4_codeblock_nie_geschlossen_beim_pruefe_immer_hart(tmp_path, capsys):
    progress = PROGRESS_GESUND.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.",
        "  mehrere Zeilen geht und wörtlich bleibt.\n```")
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path, progress=progress)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1  # immer hart, auch wenn beim Bau bekannt
    assert "CODEBLOCK_NIE_GESCHLOSSEN" in err


def test_1113_auslegung5_paket_md_name_ohne_teilung(tmp_path, capsys):
    """(5) Ohne Teilung heißt die Datei paket.md."""
    aus = tmp_path / "aus"
    code, _out, err = lauf(capsys, ["bau", str(baue_projekt(tmp_path)[0]),
                                    "--max-zeichen", "99999", "--ausgabe", str(aus)])
    assert code == 0, err
    assert [p.name for p in aus.iterdir()] == ["paket.md"]


def test_1113_auslegung6_status_unklar_wie_gueltig(tmp_path, capsys):
    """(6) STATUS_UNKLAR bleibt wörtlich im Geltungs-Block, weich, Exit 0."""
    progress = PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung, die über",
        "- [2026-09-02 | aufgehoben 2026-09-10, wieder aufgenommen 2026-09-12] "
        "Zweite Bedingung, die über")
    code, paket, err, _ordner = baue_paket(capsys, tmp_path, progress=progress)
    assert code == 0
    assert "STATUS_UNKLAR" in err
    geltend = paket.split("## Bedingungen (gelten)")[1].split("\n## ")[0]
    assert "Zweite Bedingung" in geltend  # wörtlich da, nicht nur Stummel


def test_1113_auslegung7_zeilennummer_stoert_textvergleich_nicht(tmp_path, capsys):
    """(7) Die Fundstellen-Zeilennummer wird beim Textvergleich entfernt — Verschieben ok."""
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    # Eine Zeile VOR die Überschrift der Entscheidungen schieben → alle Nummern wandern.
    # (Seit §11.15 Punkt 3 ist die Einleitung eine Einheit — die Zwischenzeile darf
    # nicht mehr in sie hinein, daher hinter den Dokumenttitel, wo keine Einheit liegt.)
    (ordner / "PROGRESS.md").write_text(
        PROGRESS_GESUND.replace("# Projekt Wegwerf\n",
                                "# Projekt Wegwerf\n\n"
                                "Zwischenzeile, die alle Nummern verschiebt.\n"),
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "EINHEIT_GEAENDERT" not in err
    assert "TEXT_FEHLT" not in err


def test_1113_auslegung8_keine_listen_nur_bei_protokoll(tmp_path, capsys):
    """(8) KEINE_LISTEN gibt es nur beim Unterbefehl protokoll, nicht bei bau."""
    ordner = baue_projekt(tmp_path)[0]
    (ordner / "PROGRESS.md").write_text(
        "# Projekt ohne Listen\n\n## Stand für den Wiedereinstieg\n\n"
        "Kurz und aktuell.\n\n---\n\n"
        "## Bedingungen (append-only, nicht zusammenfassen)\n\n"
        "Nur Text, keine Einträge.\n", encoding="utf-8")
    code, _out, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0
    assert "KEINE_LISTEN" not in err
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0  # weich
    assert "KEINE_LISTEN" in err


def test_1113_auslegung9_historie_marke_fehlt_weich(tmp_path, capsys):
    """(9) historie_ab, das im Stand nicht vorkommt → weich HISTORIE_MARKE_FEHLT."""
    ordner = baue_projekt(tmp_path)[0]
    (ordner / "KONTEXT.json").write_text(json.dumps({
        "format": 1, "projekt": "testprojekt", "protokoll": "PROGRESS.md",
        "stand": {"datei": "PROGRESS.md", "beginn": STAND_KOPF,
                  "ende": STAND_ENDE, "historie_ab": "Marke, die es nie gab"},
        "pflicht_zusatz": ["ARBEITSWEISE.md"]}), encoding="utf-8")
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 0  # weich
    assert "HISTORIE_MARKE_FEHLT" in err
    # Der Stand selbst läuft bis zum Ende weiter (im bau-Paket sichtbar)
    code2, paket, _e2 = lauf(capsys, ["bau", str(ordner)])
    assert code2 == 0
    assert "Der Stand ist kurz und aktuell." in paket
    assert "Nur bis zum ersten „Davor:“ gilt." in paket


def test_1113_auslegung10_alle_ueberspringt_ausdruecklich_ist_hart(tmp_path, capsys):
    """(10) protokoll --alle überspringt Fehlende; ausdrückliche Nennung → QUELLE_FEHLT."""
    ordner = baue_projekt(tmp_path)[0]
    (ordner / "PROGRESS.md").unlink()
    repos = tmp_path / "repos.json"
    repos.write_text(json.dumps({"repos": [{"name": "testprojekt",
                                            "pfad": str(ordner)}]}), encoding="utf-8")
    code, out, err = lauf(capsys, ["protokoll", "--alle", "--repos", str(repos)])
    assert code == 0
    assert "übersprungen" in out
    code, _out, err = lauf(capsys, ["protokoll", str(ordner)])
    assert code == 1
    assert "QUELLE_FEHLT" in err


# --- §11.12 Punkt 14 (B18) benannt; Auslegungen (11)–(13) haben ihre Deckung oben ---

def test_1112_b18_stand_ohne_ende_hart_bei_bau_weich_bei_protokoll(tmp_path, capsys):
    """Deckt denselben Fall wie test_stand_ohne_ende_hart_bei_bau_weich_bei_protokoll
    unter benanntem Namen (B18): bei bau hart (Exit 1), bei protokoll weich (Exit 0)."""
    test_stand_ohne_ende_hart_bei_bau_weich_bei_protokoll(tmp_path, capsys)


def test_1113_auslegung11_netzpruefung_nur_frischer_unterprozess():
    """(11) → §11.12 Punkt 17 (B21), oben umgesetzt (Quelltextsuche entfallen)."""
    test_1112_b21_kein_netz_frischer_prozess_alte_quelltextsuche_entfallen()


def test_1113_auslegung12_gleiche_nummer_gleicher_rumpf_weich(tmp_path, capsys):
    """(12) → §11.12 Punkt 13 (B17), oben umgesetzt."""
    test_1112_b17_entscheidung_doppelt_weich_nur_erster_im_paket(tmp_path, capsys)


def test_1113_auslegung13_trennlinie_ist_anmerkung(tmp_path, capsys):
    """(13) Die `---`-Zeile nach Einträgen ist durch Punkt 2 erledigt (Anmerkung)."""
    test_1113_punkt2_anmerkung_woertlich_im_bedingungsblock(tmp_path, capsys)


# === U1d: §11.15 Nachtrag nach der Gegenprüfung R1 (2026-09-26) ====================

# --- Punkt 4 (R1-03): Pfadgrenze — nichts außerhalb des Projektordners lesen -------

def test_1115_4_pflicht_zusatz_punkt_punkt_gesperrt(tmp_path, capsys):
    """pflicht_zusatz mit „..“ bricht aus dem Projekt aus → QUELLE_AUSSERHALB,
    die Datei wird nicht gelesen, ihr Inhalt erscheint nirgends (§11.15 Punkt 4)."""
    koeder = tmp_path / "koeder-aussen.txt"
    koeder.write_text("GEHEIMKOEDER_R1_4a\n", encoding="utf-8")
    dekl = dict(DEKL_GESUND, pflicht_zusatz=["../koeder-aussen.txt"], zusatz=[])
    ordner, _p = baue_projekt(tmp_path, dekl=dekl, arbeitsweise=None)
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    assert "QUELLE_AUSSERHALB" in err
    assert "koeder-aussen.txt" in err
    # Köderwort weder in stdout noch in einer Ausgabedatei
    assert "GEHEIMKOEDER_R1_4a" not in paket


def test_1115_4_symlink_nach_außen_gesperrt(tmp_path, capsys):
    """Symlink im Projekt auf eine Datei außerhalb → QUELLE_AUSSERHALB (resolve),
    Inhalt bleibt nicht sichtbar (§11.15 Punkt 4)."""
    koeder = tmp_path / "koeder-aussen.txt"
    koeder.write_text("GEHEIMKOEDER_R1_4b\n", encoding="utf-8")
    dekl = dict(DEKL_GESUND, pflicht_zusatz=["bruecke.md"], zusatz=[])
    ordner, _p = baue_projekt(tmp_path, dekl=dekl, arbeitsweise=None)
    (ordner / "bruecke.md").symlink_to(koeder)
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    assert "QUELLE_AUSSERHALB" in err
    assert "GEHEIMKOEDER_R1_4b" not in paket


def test_1115_4_protokoll_ausserhalb_gesperrt(tmp_path, capsys):
    """Auch das deklarierte Protokoll wird auf den Projektordner begrenzt (§11.15 Punkt 4)."""
    koeder = tmp_path / "koeder-aussen.md"
    koeder.write_text("# GEHEIMKOEDER_R1_4c\n", encoding="utf-8")
    dekl = dict(DEKL_GESUND, protokoll="../koeder-aussen.md", pflicht_zusatz=[],
                zusatz=[])
    ordner, _p = baue_projekt(tmp_path, dekl=dekl, arbeitsweise=None)
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    assert "QUELLE_AUSSERHALB" in err
    assert "GEHEIMKOEDER_R1_4c" not in paket


# --- Punkt 5 (R1-12): stand.datei wird gelesen ---------------------------------------

def test_1115_5_stand_datei_wird_gelesen(tmp_path, capsys):
    """Stand kommt aus der deklarierten Datei (hier STATUS.md), nicht aus dem
    Protokoll (§11.15 Punkt 5): bau grün, Standtext im Paket, Einheit und
    quellen tragen die Datei."""
    dekl = dict(DEKL_GESUND)
    dekl["stand"] = {"datei": "STATUS.md", "beginn": "^## Stand$",
                     "ende": "^---\\s*$", "historie_ab": None}
    ordner, _p = baue_projekt(
        tmp_path, dekl=dekl,
        dateien={"STATUS.md": "## Stand\n\nDies ist der separate aktuelle Stand.\n\n---\n"})
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0, err
    assert "Dies ist der separate aktuelle Stand." in paket
    assert "Quelle: STATUS.md" in paket
    man = lade_manifest(paket)
    stand_einheiten = [u for u in man["einheiten"] if u["id"] == "STAND"]
    assert stand_einheiten and stand_einheiten[0]["datei"] == "STATUS.md"
    assert any(q["datei"] == "STATUS.md" for q in man["quellen"])
    # Protokoll-Quelle unverändert daneben
    assert any(q["datei"] == "PROGRESS.md" for q in man["quellen"])


def test_1115_5_stand_datei_ausserhalb_gesperrt(tmp_path, capsys):
    """stand.datei außerhalb des Projektordners → QUELLE_AUSSERHALB (§11.15 Punkte 4/5)."""
    (tmp_path / "status-draussen.md").write_text(
        "## Stand\n\nGEHEIMKOEDER_R1_5b\n\n---\n", encoding="utf-8")
    dekl = dict(DEKL_GESUND)
    dekl["stand"] = {"datei": "../status-draussen.md", "beginn": "^## Stand$",
                     "ende": "^---\\s*$", "historie_ab": None}
    ordner, _p = baue_projekt(tmp_path, dekl=dekl)
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    assert "QUELLE_AUSSERHALB" in err
    assert "GEHEIMKOEDER_R1_5b" not in paket


# --- Punkt 6 (R1-10): Quellen-Drift ---------------------------------------------------

def test_1115_6_deklaration_geaendert_ist_hart(tmp_path, capsys):
    """KONTEXT.json nach dem Bau geändert → DEKLARATION_GEAENDERT, hart (§11.15
    Punkt 6): die Deklaration bestimmt, was Pflicht ist."""
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    dekl = json.loads((ordner / "KONTEXT.json").read_text(encoding="utf-8"))
    dekl["zusatz"] = ["docs/*.md", "NEU.md"]
    (ordner / "KONTEXT.json").write_text(
        json.dumps(dekl, ensure_ascii=False, indent=2), encoding="utf-8")
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "DEKLARATION_GEAENDERT" in err
    assert "KONTEXT.json" in err


def test_1115_6_pflichtquelle_geaendert_nur_weich(tmp_path, capsys):
    """Andere Quelle abweichend, ohne dass eine Einheit sich ändert → weicher
    Hinweis QUELLE_GEAENDERT, Exit 0 (§11.15 Punkt 6): die Einheitenprüfung
    entscheidet, ob das Paket veraltet ist."""
    dekl = dict(DEKL_GESUND)
    dekl["stand"] = {"datei": "STATUS.md", "beginn": "^## Stand$",
                     "ende": "^---\\s*$", "historie_ab": None}
    ordner, _p = baue_projekt(
        tmp_path, dekl=dekl,
        dateien={"STATUS.md": "## Stand\n\nDer Stand gilt weiter.\n\n---\n"
                              "Hier steht Historie, die nicht zum Stand gehört.\n"})
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0, err
    # Änderung HINTER dem Stand (nach ---): Stand- und Einheiten-Hash bleiben gleich,
    # nur der Quellen-Hash von STATUS.md weicht ab.
    (ordner / "STATUS.md").write_text(
        "## Stand\n\nDer Stand gilt weiter.\n\n---\n"
        "Hier steht jetzt ganz andere Historie.\n", encoding="utf-8")
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "QUELLE_GEAENDERT" in err
    assert "STATUS.md" in err
    assert "DEKLARATION_GEAENDERT" not in err


# --- Punkt 7 (R1-04): bekannte Befunde bei Teilung aus Teil 1 -------------------------

def _projekt_mit_fremdzeile():
    return PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung",
        "- FALSCHE Zeile, weder Eintrag noch Fortsetzung.\n"
        "- [2026-09-02 | aktiv] Zweite Bedingung")


def test_1115_7_geteilt_bekannte_befunde_aus_teil_1(tmp_path, capsys):
    """Geteiltes, unverändertes Paket: pruefe liest die bekannten Befunde aus
    Teil 1 — auch wenn Teil 2 geprüft wird (§11.15 Punkt 7). Vorher: hart, weil
    Teil-Manifeste die Befunde nicht trugen."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_fremdzeile())
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", str(GRENZE),
                                    "--ausgabe", str(tmp_path / "teile")])
    assert code == 1 and "FORMAT_UNKLAR" in err
    teile = sorted((tmp_path / "teile").glob("paket-*-von-*.md"))
    assert len(teile) >= 2
    # Teil 1 trägt die vollständige Befundliste
    man1 = lade_manifest(teile[0].read_text(encoding="utf-8"))
    assert any(b["art"] == "FORMAT_UNKLAR" for b in man1["befunde"])
    # Nichts an der Quelle geändert — auch Teil 2 muss durchgehen (weich, Exit 0)
    code, _out, err = lauf(capsys, ["pruefe", str(teile[-1]), "--projekt", str(ordner)])
    assert code == 0, err
    assert "schon beim Bau bekannt" in err
    assert "DEKLARATION_GEAENDERT" not in err


# --- Punkt 8 (R1-05): Wiedererkennen nach Wortlaut (zeile_text) -----------------------

def _projekt_mit_ersetzbarer_fremdzeile():
    return PROGRESS_GESUND.replace(
        "- [2026-09-02 | aktiv] Zweite Bedingung",
        "- ALT falscher Eintrag.\n- [2026-09-02 | aktiv] Zweite Bedingung")


def test_1115_8_andere_zeile_gleiche_stelle_ist_hart(tmp_path, capsys):
    """Ersetzt jemand die fehlerhafte Zeile durch einen anderen Inhalt an derselben
    Stelle, gilt sie nicht mehr als „schon beim Bau bekannt“ — hart (§11.15
    Punkt 8, Art + Datei + Wortlaut statt Zeilennummer)."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_ersetzbarer_fremdzeile())
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    quelle = ordner / "PROGRESS.md"
    quelle.write_text(quelle.read_text(encoding="utf-8").replace(
        "- ALT falscher Eintrag.", "- NEU anderer falscher Eintrag."),
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1, err
    bekannt = [z for z in err.splitlines() if "FORMAT_UNKLAR" in z]
    assert bekannt and "schon beim Bau bekannt" not in bekannt[0]


def test_1115_8_bekannte_zeile_nur_verschoben_bleibt_weich(tmp_path, capsys):
    """Bekannte fehlerhafte Zeile nur verschoben → weicher Hinweis „schon beim
    Bau bekannt“, Exit 0 (§11.15 Punkt 8): die Zeilennummer ist nur Fundstelle."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_ersetzbarer_fremdzeile())
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    quelle = ordner / "PROGRESS.md"
    text = quelle.read_text(encoding="utf-8")
    # Die Fremdzeile hinter die zweite Bedingung verschieben (gleicher Wortlaut)
    text = text.replace("- ALT falscher Eintrag.\n", "")
    text = text.replace(
        "  mehrere Zeilen geht und wörtlich bleibt.\n",
        "  mehrere Zeilen geht und wörtlich bleibt.\n- ALT falscher Eintrag.\n")
    quelle.write_text(text, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    bekannt = [z for z in err.splitlines() if "FORMAT_UNKLAR" in z]
    assert bekannt and "schon beim Bau bekannt" in bekannt[0]


# --- Punkt 9 (R1-07): UNKLARE Entscheidungen bekommen den Volltext --------------------

def test_1115_9_unklare_entscheidung_bekommt_volltext(tmp_path, capsys):
    """Per --entscheidung genannte Entscheidung mit unklarem Status (§11.15
    Punkt 9) kommt in Block 6 wie eine gültige; der Hinweis STATUS_UNKLAR bleibt."""
    progress = PROGRESS_GESUND.replace(
        "### E-001 | 2026-09-03 | gültig", "### E-001 | 2026-09-03 | offen")
    ordner, _dekl = baue_projekt(tmp_path, progress=progress)
    code, paket, err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0, err
    assert "STATUS_UNKLAR" in err                     # Hinweis bleibt
    assert "## Entscheidungen — Volltext, auftragsbezogen" in paket
    assert "### E-001 (PROGRESS.md, Zeile" in paket   # Volltext ist da
    man = lade_manifest(paket)
    e001 = [u for u in man["einheiten"] if u["id"] == "E-001"]
    assert e001 and e001[0].get("volltext") is True


# --- Punkt 10 (R1-08): jede Einheit genau einmal — E-NNN/volltext ---------------------

def test_1115_10_geteilte_entscheidung_eindeutige_kennungen(tmp_path, capsys):
    """Übersicht und Volltext derselben Entscheidung sind getrennte Einheiten
    (E-NNN und E-NNN/volltext); über alle Teile kommt jede Kennung genau
    einmal vor (§11.15 Punkt 10)."""
    ordner, _p = baue_projekt(tmp_path, progress=progress_mit_langen_bedingungen())
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001",
                                    "--max-zeichen", str(GRENZE),
                                    "--ausgabe", str(tmp_path / "teile")])
    assert "Traceback" not in err
    teile = sorted((tmp_path / "teile").glob("paket-*-von-*.md"))
    assert len(teile) >= 2
    ids = []
    for datei in teile:
        m = lade_manifest(datei.read_text(encoding="utf-8"))
        ids.extend(u["id"] for u in m["einheiten"])
    assert len(ids) == len(set(ids)), f"doppelte Kennungen: {sorted(ids)}"
    assert "E-001" in ids and "E-001/volltext" in ids
    code, _out, err = lauf(capsys, ["pruefe", str(teile[0]), "--projekt", str(ordner)])
    assert code == 0, err
    assert "EINHEIT_DOPPELT" not in err


def test_1115_10_volltext_einheit_fehlt_einheit_fehlt(tmp_path, capsys):
    """Nennt volltext_auswahl eine Entscheidung, deren Volltext-Einheit fehlt →
    EINHEIT_FEHLT (§11.15 Punkt 10)."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--entscheidung", "E-001"])
    assert code == 0
    man = lade_manifest(paket)
    assert man.get("volltext_auswahl") == ["E-001"]
    man["einheiten"] = [u for u in man["einheiten"] if u["art"] != "volltext"]
    neuer_block = ("```kontext-manifest\n"
                   + json.dumps(man, ensure_ascii=False, indent=2) + "\n```")
    manipuliert = re.sub(r"```kontext-manifest\n.*?\n```", lambda _m: neuer_block,
                         paket, flags=re.S)
    pfad = tmp_path / "paket.md"
    pfad.write_text(manipuliert, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "EINHEIT_FEHLT" in err
    assert "E-001/volltext" in err


def test_1115_10_doppelte_kennung_im_manifest_ist_hart(tmp_path, capsys):
    """Eine Kennung, die zweimal im Manifest steht → harter Befund EINHEIT_DOPPELT
    (§11.15 Punkt 10) — bisher wurde sie still verschluckt."""
    ordner, _dekl = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0
    man = lade_manifest(paket)
    doppelt = [u for u in man["einheiten"] if u["id"] == "E-001"][0]
    man["einheiten"].append(dict(doppelt))
    neuer_block = ("```kontext-manifest\n"
                   + json.dumps(man, ensure_ascii=False, indent=2) + "\n```")
    manipuliert = re.sub(r"```kontext-manifest\n.*?\n```", lambda _m: neuer_block,
                         paket, flags=re.S)
    pfad = tmp_path / "paket.md"
    pfad.write_text(manipuliert, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "EINHEIT_DOPPELT" in err
    assert "E-001" in err


# --- Punkt 2 (R1-02): UNKLAR-Zeilen sind Einheiten ------------------------------------

def _paket_ohne_unklar_block(paket):
    start = paket.index("## UNKLAR")
    ende = paket.index("## Stand", start)
    return paket[:start] + paket[ende:]


def test_1115_2_unklar_zeile_ist_einheit_und_geschuetzt(tmp_path, capsys):
    """FORMAT_UNKLAR-Zeilen sind Manifest-Einheiten (U-…, art „unklar“); wird der
    UNKLAR-Block samt Überschrift aus dem Paket entfernt → BLOCK_FEHLT
    (§11.15 Punkt 1; die Einheitlichkeit der U-Einheit ist oben belegt)."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_fremdzeile())
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1 and "FORMAT_UNKLAR" in err
    man = lade_manifest(paket)
    u_einheiten = [u for u in man["einheiten"] if u["art"] == "unklar"]
    assert u_einheiten, "UNKLAR-Zeile fehlt im Manifest"
    assert u_einheiten[0]["id"].startswith("U-")
    assert u_einheiten[0]["datei"] == "PROGRESS.md"
    manipuliert = _paket_ohne_unklar_block(paket)
    pfad = tmp_path / "paket.md"
    pfad.write_text(manipuliert, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    # Der Test entfernt den ganzen Block inklusive Überschrift — seit §11.15
    # Punkt 1 meldet das BLOCK_FEHLT (hart) statt einzelner TEXT_FEHLT.
    assert "BLOCK_FEHLT" in err


def test_1115_2_unklar_zeile_aus_quelle_verschollen_kein_fehler(tmp_path, capsys):
    """Verschwindet die unklare Zeile aus der Quelle (z. B. repariert), ist das
    kein Fehler — kein EINHEIT_FEHLT für die U-Einheit (§11.15 Punkt 2)."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_fremdzeile())
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    quelle = ordner / "PROGRESS.md"
    quelle.write_text(quelle.read_text(encoding="utf-8").replace(
        "- FALSCHE Zeile, weder Eintrag noch Fortsetzung.\n", ""), encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 0, err
    assert "EINHEIT_FEHLT" not in err
    assert "TEXT_FEHLT" not in err


# --- Punkt 3 (R1-06): Einleitungen sind Einheiten --------------------------------------

def _projekt_mit_einleitung():
    return PROGRESS_GESUND.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "Wichtiger Vorspann nur hier.\n"
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.")


def test_1115_3_einleitung_ist_einheit_und_geschuetzt(tmp_path, capsys):
    """Einleitungstext vor dem ersten Eintrag ist eine Manifest-Einheit (I-…,
    art „einleitung“) und wird wie eine Anmerkung geprüft (§11.15 Punkt 3)."""
    ordner, _p = baue_projekt(tmp_path, progress=_projekt_mit_einleitung())
    code, paket, err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0, err
    man = lade_manifest(paket)
    i_einheiten = [u for u in man["einheiten"] if u["art"] == "einleitung"]
    assert i_einheiten, "Einleitung fehlt im Manifest"
    assert i_einheiten[0]["id"].startswith("I-")
    # Einleitungstext aus dem Paket entfernen → TEXT_FEHLT
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket.replace("Wichtiger Vorspann nur hier.\n", ""),
                    encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err
    assert "I-" in err


def test_1115_3_neue_einleitung_in_quelle_einheit_fehlt(tmp_path, capsys):
    """Kommt in der Quelle eine Einleitung hinzu, fehlt ihre Einheit im Manifest →
    EINHEIT_FEHLT (geprüft wie eine Anmerkung, §11.15 Punkt 3)."""
    ordner, _p = baue_projekt(tmp_path)
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket, encoding="utf-8")
    quelle = ordner / "PROGRESS.md"
    quelle.write_text(quelle.read_text(encoding="utf-8").replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "Nachträglich eingefügte Einleitung.\n"
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher."),
        encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "EINHEIT_FEHLT" in err
    assert "I-" in err


def test_1115_1_koeder_in_anderem_block_zaehlt_nicht(tmp_path, capsys):
    """§11.15 Punkt 1 (R1-01): TEXT_FEHLT blockbezogen — eine Kopie des Pflichttexts
    im Stand-Block (Block 3) rettet die fehlende Bedingung in Block 4 nicht mehr."""
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    zeile = "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher."
    start = paket.index("## Bedingungen (gelten)")
    ende = paket.index("## Entscheidungen (gültig)", start)
    block4 = paket[start:ende]
    assert zeile in block4
    ohne = paket[:start] + block4.replace(zeile + "\n", "", 1) + paket[ende:]
    # Köder: derselbe Text wörtlich im Stand-Block des Pakets
    koeder = ohne.replace("Nur bis zum ersten „Davor:“ gilt.",
                          "Nur bis zum ersten „Davor:“ gilt.\n" + zeile)
    pfad = tmp_path / "paket.md"
    pfad.write_text(koeder, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err
    assert "(Bedingung)" in err


def test_1115_1_block_kopf_fehlt_block_fehlt(tmp_path, capsys):
    """§11.15 Punkt 1 (R1-01): fehlt die Blocküberschrift, obwohl der Block Einheiten
    im Manifest hat → harter Befund BLOCK_FEHLT."""
    _code, paket, _err, ordner = baue_paket(capsys, tmp_path)
    assert "## Bedingungen (gelten)\n" in paket
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket.replace("## Bedingungen (gelten)\n", "", 1),
                    encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "BLOCK_FEHLT" in err


def test_1115_1_doppelter_text_zweimal_benoetigt(tmp_path, capsys):
    """§11.15 Punkt 1 (R1-01): tragen zwei Einheiten eines Blocks denselben Text,
    muss er dort zweimal vorkommen — eine Stelle reicht nicht."""
    ordner = baue_projekt(tmp_path)[0]
    (ordner / "PROGRESS.md").write_text(
        "# Projekt Doppelt\n\n## Stand für den Wiedereinstieg\n\nAktuell.\n\n---\n\n"
        "## Bedingungen (append-only, nicht zusammenfassen)\n\n"
        "- [2026-09-01 | aktiv] Gleiche Pflichtbedingung ZWEIMAL.\n"
        "## Weitere Bedingungen\n\n"
        "- [2026-09-01 | aktiv] Gleiche Pflichtbedingung ZWEIMAL.\n"
        "## Entscheidungen (append-only, nicht zusammenfassen)\n\n"
        "### E-001 | 2026-09-01 | gültig\n"
        "- **Entscheidung:** Eine.\n- **Verworfene Alternative:** Keine.\n"
        "- **Grund:** X.\n- **Betroffene Pfade:** x\n- **Prüfbar durch:** ja\n",
        encoding="utf-8")
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 0
    start = paket.index("## Bedingungen (gelten)")
    ende = paket.index("## Entscheidungen (gültig)", start)
    text = "- [2026-09-01 | aktiv] Gleiche Pflichtbedingung ZWEIMAL."
    assert paket[start:ende].count(text) == 2
    pfad = tmp_path / "paket.md"
    pfad.write_text(paket[:start] + paket[start:ende].replace(text + "\n", "", 1)
                    + paket[ende:], encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(pfad), "--projekt", str(ordner)])
    assert code == 1
    assert "TEXT_FEHLT" in err


def test_1115_1_geteilte_teile_tragen_block_koepfe(tmp_path, capsys):
    """§11.15 Punkt 1: bei Teilung trägt jeder Teil die Überschrift jedes Blocks,
    von dem er Einheiten enthält (wörtlich oder mit dem Zusatz „(Fortsetzung)“)."""
    _code, _ordner, ausg = gate5_paket(capsys, tmp_path)
    block_von_art = {"unklar": 2, "stand": 3, "bedingung": 4, "anmerkung": 4,
                     "einleitung": 4, "entscheidung": 5, "volltext": 6,
                     "pflicht_zusatz": 8}
    kopf_von_block = {2: "## UNKLAR — selbst lesen", 3: "## Stand",
                      4: "## Bedingungen (gelten)",
                      5: "## Entscheidungen (gültig) — Übersicht",
                      6: "## Entscheidungen — Volltext, auftragsbezogen",
                      8: "## Pflicht-Zusatzdateien"}
    geprueft = 0
    for f in sorted(ausg.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        man = lade_manifest(text)
        koerper = kp.MANIFEST_BLOCK_MUSTER.sub("", text)
        bloecke_im_teil = set()
        for u in man["einheiten"]:
            nr = block_von_art.get(u["art"])
            if nr is None:
                continue
            if u["art"] == "entscheidung" and u.get("status") not in ("gültig", None):
                continue
            bloecke_im_teil.add(nr)
        for nr in bloecke_im_teil:
            kopf = kopf_von_block[nr]
            assert kopf in koerper or kopf + " (Fortsetzung)" in koerper, \
                f"{f.name}: Block-{nr}-Kopf fehlt, obwohl Einheiten daraus im Manifest stehen"
            geprueft += 1
    assert geprueft >= 3  # die langen Bedingungen verteilen Block 4 auf mehrere Teile


def test_1115_11_kandidaten_meldet_deklarationsbefunde(tmp_path, capsys):
    """§11.15 Punkt 11 (R1-09): kandidaten meldet Deklarationsbefunde auf stderr
    wie bau; bei hartem Befund endet es mit Exit 1 — die JSON-Ausgabe kommt
    trotzdem (§11.15 Punkt 11)."""
    ordner = baue_projekt(tmp_path)[0]
    dekl = json.loads((ordner / "KONTEXT.json").read_text(encoding="utf-8"))
    dekl["unbekannter_schluessel"] = True
    (ordner / "KONTEXT.json").write_text(json.dumps(dekl, ensure_ascii=False),
                                         encoding="utf-8")
    code, out, err = lauf(capsys, ["kandidaten", str(ordner),
                                   "--auftrag", "Pflichtbedingung"])
    assert "DEKLARATION_UNBEKANNTER_SCHLUESSEL" in err
    assert "kontextpaket kandidaten" in err
    ausgabe = json.loads(out)   # Ausgabe trotzdem
    assert ausgabe["format"] == 1
    assert code == 1


def test_1115_10_geteilt_doppelte_bedingung_kein_doppelt(tmp_path, capsys):
    """Frisch gebaute geteilte Pakete listen jede Kennung nur im ersten Teil,
    der sie trägt — BEDINGUNG_DOPPELT (weich) darf bei Teilung nicht zusätzlich
    EINHEIT_DOPPELT (hart) erzeugen (§11.15 Punkt 10)."""
    ordner = baue_projekt(tmp_path)[0]
    lang = ("- [2026-09-01 | aktiv] Lange Bedingung: "
            + "wörtlich wiederholter Inhalt. " * 20 + "Ende.\n")
    (ordner / "PROGRESS.md").write_text(
        "# Projekt DoppeltGeteilt\n\n## Stand für den Wiedereinstieg\n\nAktuell.\n\n---\n\n"
        "## Bedingungen (append-only, nicht zusammenfassen)\n\n"
        + lang +
        "## Weitere Bedingungen\n\n" + lang +
        "## Entscheidungen (append-only, nicht zusammenfassen)\n\n"
        "### E-001 | 2026-09-01 | gültig\n"
        "- **Entscheidung:** Eine.\n- **Verworfene Alternative:** Keine.\n"
        "- **Grund:** X.\n- **Betroffene Pfade:** x\n- **Prüfbar durch:** ja\n",
        encoding="utf-8")
    ausg = tmp_path / "teile"
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--max-zeichen", "1200",
                                    "--ausgabe", str(ausg)])
    assert "Traceback" not in err, err
    dateien = sorted(ausg.glob("*.md"))
    assert len(dateien) >= 2, "Paket sollte geteilt sein"
    alle = [u["id"] for f in dateien
            for u in lade_manifest(f.read_text(encoding="utf-8"))["einheiten"]]
    assert len(alle) == len(set(alle)), f"doppelte Kennungen über Teile: {alle}"
    code, _out, err = lauf(capsys, ["pruefe", str(dateien[0]),
                                    "--projekt", str(ordner)])
    assert "EINHEIT_DOPPELT" not in err, err


# --- §14: Jev-Reihenfolge im Block 9 ---------------------------------------------

def _jev_projekt(tmp_path, anzahl=2, jev=True, besonderer_text=None,
                 einzeltexte=None, ueberschriften=None):
    dekl = {**DEKL_GESUND, "zusatz": ["docs/extra.md"]}
    if jev is not None:
        dekl["jev"] = jev
    ordner, _ = baue_projekt(tmp_path, dekl=dekl)
    texte = []
    for i in range(anzahl):
        if einzeltexte is not None:
            body = einzeltexte[i]
        elif besonderer_text is not None:
            body = besonderer_text
        else:
            body = f"marker-{i:03d}"
        kopf = ueberschriften[i] if ueberschriften else f"Kandidat {i:03d}"
        texte += [f"## {kopf}", "", f"Jev Regeln Zusatz {body}.", ""]
    (ordner / "docs" / "extra.md").write_text("\n".join(texte), encoding="utf-8")
    return ordner


def _jev_antwort(noul):
    return 200, {}, json.dumps({
        "model": "jev-1.13.0",
        "answers": {"wichtig": {"type": "noul", "noul": noul}},
        "usage": {"input_tokens": 17, "output_tokens": 2},
    }).encode("utf-8")


def _jev_fake_transport(n=2, kontrolle=0.1):
    aufrufe = []

    def transport(_url, _kopf, rumpf, _timeout):
        daten = json.loads(rumpf.decode("utf-8"))
        state = daten["state"]["kandidat"]
        aufrufe.append(state)
        if state.get("quelle") == "kontrolle":
            return _jev_antwort(kontrolle)
        marker = re.search(r"marker-(\d+)", state["text"])
        index = int(marker.group(1)) if marker else 0
        return _jev_antwort((index + 1) / (n + 1))

    transport.aufrufe = aufrufe
    return transport


def _jev_bau(capsys, ordner, *extra):
    return lauf(capsys, ["bau", str(ordner), "--auftrag", "Jev Regeln Zusatz",
                         *extra])


def test_14_erfolg_jev_sortiert_aber_exit_bleibt_unveraendert(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path, anzahl=3)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(3)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, err = _jev_bau(capsys, ordner)
    assert code == 0
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    assert block.index("Kandidat 002") < block.index("Kandidat 001") < block.index("Kandidat 000")
    assert "Reihenfolge: Jev (jev-1.13.0, 3 von 3 bewertet, Kontrolle bestanden)" in block
    man = lade_manifest(paket)
    assert man["jev"]["reihenfolge"] == "jev"
    assert man["jev"]["status"] == "bewertet"
    assert "noul" not in json.dumps(man["jev"])
    ohne_code, _ohne_paket, _ohne_err = _jev_bau(capsys, ordner, "--ohne-jev")
    assert ohne_code == code
    assert len(transport.aufrufe) == 4
    assert "kontextpaket bau: Jev — Reihenfolge jev" in err


def test_14_kein_schluessel_nutzt_lokal_ohne_transport(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path, jev=None)  # fehlender Schlüssel bedeutet „an“
    transport = _jev_fake_transport()
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    assert code == 0 and not transport.aufrufe
    assert "Reihenfolge: lokal (Jev: kein_schluessel)" in paket
    man = lade_manifest(paket)
    assert man["jev"]["reihenfolge"] == "lokal"
    assert "JEV_NICHT_BEWERTET" in json.dumps(man["befunde"])


@pytest.mark.parametrize("aus", [False, True], ids=["deklaration", "aufruf"])
def test_14_jev_abschaltbar(tmp_path, capsys, monkeypatch, aus):
    ordner = _jev_projekt(tmp_path, jev=aus)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport()
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    args = ("--ohne-jev",) if aus else ()
    code, paket, _err = _jev_bau(capsys, ordner, *args)
    assert code == 0 and not transport.aufrufe
    grund = "--ohne-jev" if aus else "KONTEXT.json"
    assert f"Reihenfolge: lokal (Jev aus: {grund})" in paket
    assert "JEV_AUS" in json.dumps(lade_manifest(paket)["befunde"])


def test_14_unzuverlaessige_kontrolle_faellt_lokal_zurueck(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path, anzahl=3)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    monkeypatch.setattr(kp, "JEV_TRANSPORT", _jev_fake_transport(3, kontrolle=0.9))
    code, paket, _err = _jev_bau(capsys, ordner)
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    assert code == 0
    assert "Reihenfolge: lokal (Jev: kontrolle)" in block
    assert block.index("Kandidat 000") < block.index("Kandidat 001") < block.index("Kandidat 002")
    assert "JEV_UNZUVERLAESSIG" in json.dumps(lade_manifest(paket)["befunde"])


def test_14_http_500_faellt_lokal_zurueck(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path, anzahl=1)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    jev_modul = kp._lade_jev()
    monkeypatch.setattr(jev_modul.time, "sleep", lambda *_: None)
    aufrufe = []

    def serverfehler(*_args):
        aufrufe.append(1)
        return 500, {}, b"ohne geheimnis"

    monkeypatch.setattr(kp, "JEV_TRANSPORT", serverfehler)
    code, paket, _err = _jev_bau(capsys, ordner)
    assert code == 0 and len(aufrufe) == 6
    assert "Reihenfolge: lokal (Jev: http_500)" in paket
    assert "JEV_NICHT_BEWERTET" in json.dumps(lade_manifest(paket)["befunde"])


def test_14_11_schluesselmuster_haelt_nur_treffer_zurueck(tmp_path, capsys,
                                                        monkeypatch):
    """§14.11 Punkt 2: nur der Kandidat mit Schlüsselmuster bleibt ungesendet;
    Jev ordnet die übrigen, der Zurückgehaltene steht im Block 9 dahinter."""
    geheim = "sk-" + "abcdefghijklmnopqrstuv"
    einzeltexte = [f"marker-{i:03d}" for i in range(5)]
    einzeltexte[2] = geheim
    ordner = _jev_projekt(tmp_path, anzahl=5, einzeltexte=einzeltexte)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(5)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, err = _jev_bau(capsys, ordner)
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    man = lade_manifest(paket)
    manifest = json.dumps(man)
    gesendet = [a for a in transport.aufrufe if a.get("quelle") != "kontrolle"]
    assert code == 0
    assert len(gesendet) == 4 and len(transport.aufrufe) == 5
    assert geheim not in json.dumps(transport.aufrufe, ensure_ascii=False)
    assert geheim not in paket + err + manifest
    assert "JEV_SCHLUESSELMUSTER" in manifest
    assert man["jev"]["reihenfolge"] == "jev"
    assert man["jev"]["zurueckgehalten"] == 1
    assert "4 von 4 bewertet" in block
    assert "1 zurückgehalten (Schlüsselmuster)" in block
    for i in (0, 1, 3, 4):
        assert block.index(f"Kandidat {i:03d}") < block.index("Kandidat 002")


def test_14_11_schluesselmuster_im_auftrag_blockiert_ganz(tmp_path, capsys,
                                                        monkeypatch):
    """§14.11 Punkt 2: Treffer im Auftrag schaltet Jev weiterhin ganz ab."""
    ordner = _jev_projekt(tmp_path, anzahl=2)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Jev Regeln Zusatz sk-" + "abcdefghijklmnopqrstuv"])
    manifest = json.dumps(lade_manifest(paket))
    assert code == 0 and not transport.aufrufe
    assert "Reihenfolge: lokal (Jev: schluesselmuster)" in paket
    assert "JEV_SCHLUESSELMUSTER" in manifest


def test_14_11_schluesselmuster_alle_zurueckgehalten_lokal(tmp_path, capsys,
                                                         monkeypatch):
    """§14.11 Punkt 2: sind alle Kandidaten betroffen, läuft nichts über den
    Transport und die Reihenfolge bleibt lokal — ohne Geheimnisleck."""
    geheim = "sk-" + "abcdefghijklmnopqrstuv"
    ordner = _jev_projekt(tmp_path, anzahl=2, besonderer_text=geheim)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, err = _jev_bau(capsys, ordner)
    man = lade_manifest(paket)
    manifest = json.dumps(man)
    assert code == 0 and not transport.aufrufe
    assert "Reihenfolge: lokal (Jev: schluesselmuster)" in paket
    assert geheim not in paket + err + manifest
    assert "JEV_SCHLUESSELMUSTER" in manifest
    assert man["jev"]["zurueckgehalten"] == 2


def test_14_kostenobergrenze_blockiert_transport(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path, anzahl=1)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(1)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner, "--jev-obergrenze-usd", "0.0000001")
    assert code == 0 and not transport.aufrufe
    assert "Reihenfolge: lokal (Jev: kostenobergrenze)" in paket
    assert "JEV_ZU_TEUER" in json.dumps(lade_manifest(paket)["befunde"])


def test_14_ohne_auftrag_keine_jev_zeile(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport()
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    assert code == 0 and not transport.aufrufe
    assert "Reihenfolge:" not in block


def test_14_ohne_kandidaten_keine_jev_zeile(tmp_path, capsys, monkeypatch):
    ordner = _jev_projekt(tmp_path)
    (ordner / "docs" / "extra.md").write_text(
        "## Unpassender Abschnitt\n\nNichts zum Thema.\n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport()
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag", "Jev Regeln Zusatz"])
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    assert code == 0 and not transport.aufrufe
    assert "(keine Kandidaten" in block
    assert "Reihenfolge:" not in block


def test_14_jev_typfehler_hart(tmp_path, capsys):
    ordner = _jev_projekt(tmp_path, jev="ja")
    code, _paket, err = _jev_bau(capsys, ordner)
    assert code == 1 and "jev ist kein boolescher Wert" in err


def test_14_import_kontextpaket_laedt_kein_urllib_request():
    code = (
        "import importlib.util, sys; "
        f"p={str(WERKZEUGE / 'kontextpaket.py')!r}; "
        "s=importlib.util.spec_from_file_location('kontextpaket', p); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "assert 'urllib.request' not in sys.modules, sorted(sys.modules); "
        "print('OK')"
    )
    lauf = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=30)
    assert lauf.returncode == 0, lauf.stderr
    assert "OK" in lauf.stdout


def test_14_keine_kandidaten_entfernt_und_pruefe_bleibt_gruen(tmp_path, capsys,
                                                               monkeypatch):
    ordner = _jev_projekt(tmp_path, anzahl=40)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    jev_modul = kp._lade_jev()
    original = jev_modul.ordne_kandidaten
    beobachtet = {}

    def ordne(auftrag, kandidaten, **kwargs):
        beobachtet["eingang"] = [k["id"] for k in kandidaten]
        ergebnis = original(auftrag, kandidaten, **kwargs)
        beobachtet["ausgang"] = [e.id for e in ergebnis.eintraege]
        return ergebnis

    monkeypatch.setattr(jev_modul, "ordne_kandidaten", ordne)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", _jev_fake_transport(40))
    code, paket, _err = _jev_bau(capsys, ordner)
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split("```kontext-manifest", 1)[0]
    links = [line for line in block.splitlines() if line.startswith("docs/extra.md:")]
    assert code == 0
    assert lade_manifest(paket)["jev"]["status"] == "teilweise"
    assert set(beobachtet["eingang"]) == set(beobachtet["ausgang"])
    assert len(beobachtet["ausgang"]) == 40
    assert len(links) == 15 and len(set(links)) == 15
    datei = tmp_path / "jev-paket.md"
    datei.write_text(paket, encoding="utf-8")
    pruef_code, _out, pruef_err = lauf(capsys, ["pruefe", str(datei), "--projekt", str(ordner)])
    assert pruef_code == 0, pruef_err


# --- §14.12: Nachbesserungen nach der Gegenprüfung R5 ------------------------------

def test_14_12_ordnung_unvollstaendig_faellt_lokal(tmp_path, capsys, monkeypatch):
    """§14.12 (6a, B5): der Vollständigkeitswächter hat einen Test — liefert
    ordne_kandidaten eine Ordnung ohne einen Kandidaten, gilt lokale
    Reihenfolge, grund=ordnung_unvollstaendig, und Block 9 zeigt alle."""
    ordner = _jev_projekt(tmp_path, anzahl=3)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    jev_modul = kp._lade_jev()
    original = jev_modul.ordne_kandidaten

    def defekte_ordnung(auftrag, kandidaten, **kwargs):
        ordnung = original(auftrag, kandidaten, **kwargs)
        return jev_modul.Ordnung(
            eintraege=ordnung.eintraege[1:],  # erster Kandidat fehlt
            status_gesamt=ordnung.status_gesamt, modell=ordnung.modell,
            eingabe_token=ordnung.eingabe_token, kontrolle=ordnung.kontrolle)

    monkeypatch.setattr(jev_modul, "ordne_kandidaten", defekte_ordnung)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", _jev_fake_transport(3))
    code, paket, _err = _jev_bau(capsys, ordner)
    block = paket.split("## Zusatz — Fundstellen", 1)[1].split(
        "```kontext-manifest", 1)[0]
    man = lade_manifest(paket)
    assert code == 0
    assert man["jev"]["reihenfolge"] == "lokal"
    assert man["jev"]["grund"] == "ordnung_unvollstaendig"
    assert "Reihenfolge: lokal (Jev: ordnung_unvollstaendig)" in block
    for i in range(3):
        assert f"Kandidat {i:03d}" in block
    assert "JEV_NICHT_BEWERTET" in json.dumps(man["befunde"])


def test_14_12_schluesselmuster_in_ueberschrift_zurueckgehalten(
        tmp_path, capsys, monkeypatch):
    """§14.12 (6b, B6): Muster nur in der Überschrift → Kandidat
    zurückgehalten und nicht gesendet; der Befund nennt :ueberschrift.
    (Abschnitt.text schließt die Überschrift ein — §14.12.5: die Fundstelle
    bleibt wörtlich im Paket, die Suche schützt nur den Transport.)"""
    geheim = "sk-" + "ueberschriftgeheim123456"
    ordner = _jev_projekt(
        tmp_path, anzahl=2,
        ueberschriften=[f"Kandidat mit {geheim} im Titel", "Kandidat 001"])
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    man = lade_manifest(paket)
    befunde_text = json.dumps(man["befunde"], ensure_ascii=False)
    gesendet = [a for a in transport.aufrufe if a.get("quelle") != "kontrolle"]
    assert code == 0
    assert man["jev"]["zurueckgehalten"] == 1
    assert "JEV_SCHLUESSELMUSTER" in befunde_text
    assert ":ueberschrift" in befunde_text
    assert len(gesendet) == 1
    assert geheim not in json.dumps(transport.aufrufe, ensure_ascii=False)
    assert geheim in paket  # Überschrift steht weiter in Block 9 (§14.12.5)


def test_14_12_schluesselmuster_im_dateinamen_zurueckgehalten(
        tmp_path, capsys, monkeypatch):
    """§14.12 (6c, B6): Muster nur im Dateinamen → das Feld quelle trifft,
    Kandidat zurückgehalten und nicht gesendet; Befund nennt :quelle."""
    geheim = "sk-" + "dateinamegeheim1234567"
    dekl = {**DEKL_GESUND, "jev": True}
    ordner, _ = baue_projekt(tmp_path, dekl=dekl, dateien={
        "docs/extra.md": "## Kandidat 000\n\nJev Regeln Zusatz marker-000.\n",
        f"docs/notiz-{geheim}.md":
            "## Harmlose Notiz\n\nJev Regeln Zusatz marker-001.\n",
    })
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    man = lade_manifest(paket)
    befunde_text = json.dumps(man["befunde"], ensure_ascii=False)
    gesendet = [a for a in transport.aufrufe if a.get("quelle") != "kontrolle"]
    assert code == 0
    assert man["jev"]["reihenfolge"] == "jev"
    assert man["jev"]["zurueckgehalten"] == 1
    assert "JEV_SCHLUESSELMUSTER" in befunde_text
    assert ":quelle" in befunde_text
    assert len(gesendet) == 1
    assert geheim not in json.dumps(transport.aufrufe, ensure_ascii=False)
    assert geheim in paket  # Dateiname bleibt als Anzeige im Paket (§14.12.5)


def test_14_12_jev_geteiltes_paket_feld_nur_in_teil_1(tmp_path, capsys,
                                                    monkeypatch):
    """§14.12 (6d, B8): geteiltes Paket — das Manifest-Feld jev steht nur in
    Teil 1, Block 9 mit Reihenfolge-Zeile im letzten Teil, pruefe Exit 0."""
    ordner = _jev_projekt(tmp_path, anzahl=2)
    prog = (ordner / "PROGRESS.md").read_text(encoding="utf-8")
    prog = prog.replace(
        "- [2026-09-01 | aktiv] Committe nichts selbst, frag vorher.",
        "\n".join(f"- [2026-09-{i:02d} | aktiv] Lange Bedingung Nummer {i}: "
                  + "wörtlich wiederholter Inhalt. " * 30
                  for i in range(1, 28)))
    (ordner / "PROGRESS.md").write_text(prog, encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    ausg = tmp_path / "teile"
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                    "Jev Regeln Zusatz",
                                    "--max-zeichen", "6000",
                                    "--ausgabe", str(ausg)])
    assert code == 0, err
    teile = sorted(ausg.glob("*.md"))
    assert len(teile) >= 2, "Paket sollte geteilt sein"
    teile_manis = [(x, lade_manifest(x.read_text(encoding="utf-8")))
                   for x in teile]
    assert all(("jev" in m) == (m["teil"]["nr"] == 1)
               for _x, m in teile_manis)
    teile_manis.sort(key=lambda xm: xm[1]["teil"]["nr"])
    assert teile_manis[0][1]["jev"]["reihenfolge"] == "jev"
    letzter = teile_manis[-1][0].read_text(encoding="utf-8")
    assert "## Zusatz — Fundstellen" in letzter
    assert "Reihenfolge: Jev" in letzter
    pc, _o, pe = lauf(capsys, ["pruefe", str(teile_manis[0][0]),
                               "--projekt", str(ordner)])
    assert pc == 0, pe


def test_14_12_obergrenze_erst_nach_zurueckhalten_eingehalten(
        tmp_path, capsys, monkeypatch):
    """§14.12 (6e, B8/§14.11.3): die Kostenschätzung läuft über die
    gefilterte Liste — eine Grenze, die mit dem zurückgehaltenen Kandidaten
    überschritten würde, wird ohne ihn eingehalten und Jev läuft."""
    geheim = "sk-" + "obergrenzegeheim1234567"
    einzeltexte = [f"marker-{i:03d}" for i in range(5)]
    einzeltexte[2] = f"marker-002 {geheim} " + "x" * 3000
    ordner = _jev_projekt(tmp_path, anzahl=5, einzeltexte=einzeltexte)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(5)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)

    # Grenze zwischen der Schätzung mit und ohne den getroffenen Kandidaten.
    jev_modul = kp._lade_jev()
    dekl = kp.lade_deklaration(ordner, [])
    kand = kp.kandidaten_liste(kp.zusatz_abschnitte(ordner, dekl),
                               kp.suchwoerter("Jev Regeln Zusatz"))
    kand_jsons = [kp.kandidat_json(a, p) for a, p in kand]
    mit_allem = jev_modul.schaetze_kosten(
        "Jev Regeln Zusatz", kand_jsons)["usd_geschaetzt"]
    gesendet_jsons = [k for k in kand_jsons if geheim not in k["text"]]
    ohne_treffer = jev_modul.schaetze_kosten(
        "Jev Regeln Zusatz", gesendet_jsons)["usd_geschaetzt"]
    grenze = (mit_allem + ohne_treffer) / 2
    assert ohne_treffer < grenze < mit_allem

    code, paket, _err = _jev_bau(capsys, ordner,
                                 "--jev-obergrenze-usd", str(grenze))
    man = lade_manifest(paket)
    gesendet = [a for a in transport.aufrufe if a.get("quelle") != "kontrolle"]
    assert code == 0
    assert man["jev"]["reihenfolge"] == "jev"
    assert man["jev"]["zurueckgehalten"] == 1
    assert len(gesendet) == 4
    assert geheim not in json.dumps(transport.aufrufe, ensure_ascii=False)
    assert "Reihenfolge: Jev" in paket


def test_14_12_doppelte_id_eigener_grund(tmp_path, capsys, monkeypatch):
    """§14.12 (6g, B3): doppelte Kandidaten-ID → Grund doppelte_id statt
    transport_fehler; lokaler Rückfall, 0 Transportaufrufe."""
    ordner = _jev_projekt(tmp_path, anzahl=3, besonderer_text="marker-000",
                          ueberschriften=["Kandidat A"] * 3)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(3)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    man = lade_manifest(paket)
    assert code == 0 and not transport.aufrufe
    assert man["jev"]["reihenfolge"] == "lokal"
    assert man["jev"]["grund"] == "doppelte_id"
    assert "Reihenfolge: lokal (Jev: doppelte_id)" in paket
    assert "JEV_NICHT_BEWERTET" in json.dumps(man["befunde"])


# --- §15.2: Kleinteile nach der Gegenprüfung R4 -------------------------------

@pytest.mark.parametrize("aus", [False, True], ids=["deklaration", "aufruf"])
def test_15_2_stderr_jev_aus_nennt_grund(tmp_path, capsys, monkeypatch, aus):
    """§15.2 Punkt 2: bei JEV_AUS lautet die stderr-Zeile
    „kontextpaket bau: Jev aus (<grund>) — Reihenfolge lokal, 0 $“ —
    Paketzeile, Manifest und Exit bleiben unverändert."""
    ordner = _jev_projekt(tmp_path, jev=aus)
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport()
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    args = ("--ohne-jev",) if aus else ()
    code, paket, err = _jev_bau(capsys, ordner, *args)
    grund = "--ohne-jev" if aus else "KONTEXT.json"
    assert code == 0 and not transport.aufrufe
    assert (f"kontextpaket bau: Jev aus ({grund}) — Reihenfolge lokal, 0 $"
            in err)
    assert "Jev — Reihenfolge" not in err
    # Paketzeile und Manifest unverändert
    assert f"Reihenfolge: lokal (Jev aus: {grund})" in paket
    man = lade_manifest(paket)
    assert man["jev"]["aktiv"] is False
    assert man["jev"]["reihenfolge"] == "lokal"
    assert man["jev"]["grund"] is None
    assert "JEV_AUS" in json.dumps(man["befunde"])


def test_15_2_stderr_uebrige_faelle_unveraendert(tmp_path, capsys, monkeypatch):
    """§15.2 Punkt 2: die anderen Fälle der stderr-Zeile bleiben wie gehabt —
    aktiv ohne Schlüssel zeigt das alte Format mit Grund in Klammern."""
    ordner = _jev_projekt(tmp_path, jev=None)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", _jev_fake_transport())
    code, _paket, err = _jev_bau(capsys, ordner)
    assert code == 0
    assert ("kontextpaket bau: Jev — Reihenfolge lokal (kein_schluessel), "
            "0.00000000 $") in err


def _teilweise_paket(capsys, tmp_path, wortlaut):
    """bau-Lauf mit E-002-Kopf „| <wortlaut>“ (löst TEILWEISE_REVIDIERT aus)."""
    progress = PROGRESS_GESUND.replace(
        "### E-002 | 2026-09-04 | revidiert durch E-001",
        f"### E-002 | 2026-09-04 | {wortlaut}")
    return baue_paket(capsys, tmp_path, progress=progress)


def _teilweise_meldung(err):
    zeilen = [z for z in err.splitlines() if "TEILWEISE_REVIDIERT" in z]
    assert len(zeilen) == 1, err
    return zeilen[0]


def test_15_2_teilweise_revidiert_maskiert_steuerzeichen(tmp_path, capsys):
    """§15.2 Punkt 4 (R4-2): Steuerzeichen (Kategorie Cc) stehen in der
    stderr-Meldung sichtbar als \\xNN (kleine Hex), nicht roh — Manifest-
    Befundtext und Einstufung bleiben unverändert."""
    wortlaut = "gültig — Text\x1b[31m rot\x00 (revidiert E-001)"
    code, paket, err, _ordner = _teilweise_paket(capsys, tmp_path, wortlaut)
    zeile = _teilweise_meldung(err)
    assert code == 0
    assert "Text\\x1b[31m rot\\x00 (revidiert E-001)" in zeile
    assert "\x1b" not in zeile and "\x00" not in zeile
    man = lade_manifest(paket)
    eintrag = next(b for b in man["befunde"]
                   if b["art"] == "TEILWEISE_REVIDIERT")
    assert wortlaut in eintrag["text"]          # Manifest trägt den rohen Wortlaut
    assert "\\x1b" not in eintrag["text"]
    e2 = next(e for e in man["einheiten"] if e["id"] == "E-002")
    assert e2["status"] == "gültig"             # Einstufung unverändert


def test_15_2_teilweise_revidiert_kuerzt_langes_zitat(tmp_path, capsys):
    """§15.2 Punkt 3 (R4-1): ein Status-Wortlaut über 200 Zeichen wird in der
    stderr-Meldung auf 200 Zeichen gekürzt und endet mit „…“; der Manifest-
    Befundtext bleibt vollständig und pruefe bleibt Exit 0."""
    wortlaut = "gültig — " + "x" * 5000 + " (revidiert E-001)"
    code, paket, err, ordner = _teilweise_paket(capsys, tmp_path, wortlaut)
    zeile = _teilweise_meldung(err)
    zitat = re.search(r"Status „(.*)“ nennt", zeile).group(1)
    assert code == 0
    assert len(zitat) == 200 and zitat.endswith("…")
    assert len(zeile) < 400
    eintrag = next(b for b in lade_manifest(paket)["befunde"]
                   if b["art"] == "TEILWEISE_REVIDIERT")
    assert wortlaut in eintrag["text"]          # Manifest unverändert
    assert "…" not in eintrag["text"]
    paketdatei = tmp_path / "paket.md"
    paketdatei.write_text(paket, encoding="utf-8")
    pc, _o, pe = lauf(capsys, ["pruefe", str(paketdatei),
                               "--projekt", str(ordner)])
    assert pc == 0, pe


def test_15_2_teilweise_revidiert_erst_maskieren_dann_kuerzen(tmp_path, capsys):
    """§15.2 Punkt 4: die Reihenfolge ist maskieren → kürzen — ein Wortlaut
    unter 200 Zeichen, dessen maskierte Form länger ist, wird gekürzt."""
    wortlaut = "gültig — " + "x" * 140 + "\x00" * 30 + " (revidiert E-001)"
    assert len(wortlaut) <= 200                 # roh kurz …
    code, _paket, err, _ordner = _teilweise_paket(capsys, tmp_path, wortlaut)
    zitat = re.search(r"Status „(.*)“ nennt",
                      _teilweise_meldung(err)).group(1)
    assert code == 0
    assert len(zitat) == 200 and zitat.endswith("…")   # … maskiert lang
    assert "\\x00" in zitat
    assert "\x00" not in zitat


# === U7-1: §16 Merkzettel als Standard-Quelle in bau ==========================

def _merk(tmp_path, notizen, name="merkzettel-echt"):
    """Wegwerf-Merkzettel anlegen; notizen: {rel_pfad: text}."""
    merk = tmp_path / name
    for rel, text in notizen.items():
        ziel = merk / rel
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
    return merk


def _block9(paket):
    return paket.split("## Zusatz — Fundstellen", 1)[1].split(
        "```kontext-manifest", 1)[0]


def test_16_bau_notiz_fundstelle_absoluter_pfad(tmp_path, capsys, monkeypatch):
    """(a) Notiz mit Suchwort erscheint in Block 9 mit absolutem Pfad (§16.1.3)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"notizen/heute.md":
                            "## Heute\n\nDer Auftragsbär sieht den "
                            "Werkzeugfelsen.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär Werkzeugfelsen"])
    assert code == 0
    abs_pfad = str((merk / "notizen" / "heute.md").resolve())
    assert f"{abs_pfad}:1 — Heute" in _block9(paket)
    # Projektabschnitte bleiben relativ (§16.1.3)
    assert "docs/hinweis.md:" in _block9(paket)
    man = lade_manifest(paket)
    assert man["merkzettel"] == str(merk.resolve())
    assert man["merkzettel_grund"] == "umgebung"


def test_16_bau_merkzettel_ohne_archiv_und_memory(tmp_path, capsys, monkeypatch):
    """(b) MEMORY.md und archiv/ kommen nie ins Paket (§16.1.1)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {
        "MEMORY.md": "# Index\n\nAuftragsbär Gedächtnisindex\n",
        "archiv/alt.md": "## Altnotiz\n\nAuftragsbär aus dem Archiv\n",
        "heute.md": "## Heute\n\nDer Auftragsbär notiert.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär"])
    assert code == 0
    assert lade_manifest(paket)["merkzettel"] == str(merk.resolve())
    assert "Heute" in _block9(paket)          # die normale Notiz ist da
    assert "Gedächtnisindex" not in paket
    assert "Altnotiz" not in paket
    assert "aus dem Archiv" not in paket


def test_16_bau_ohne_merkzettel_schalter(tmp_path, capsys, monkeypatch):
    """(c) --ohne-merkzettel schlägt die Umgebung — keine Notiz, Grund stimmt."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär", "--ohne-merkzettel"])
    assert code == 0
    man = lade_manifest(paket)
    assert man["merkzettel"] is None
    assert man["merkzettel_grund"] == "--ohne-merkzettel"
    assert "Heute" not in _block9(paket)


def test_16_bau_dekl_merkzettel_false(tmp_path, capsys, monkeypatch):
    """(d) \"merkzettel\": false schlägt Umgebung und --merkzettel (§16.1.2b)."""
    dekl = {**DEKL_GESUND, "merkzettel": False}
    ordner, _dekl_pfad = baue_projekt(tmp_path, dekl=dekl)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär", "--merkzettel", str(merk)])
    assert code == 0
    man = lade_manifest(paket)
    assert man["merkzettel"] is None
    assert man["merkzettel_grund"] == "KONTEXT.json"
    assert "Heute" not in _block9(paket)


def test_16_bau_merkzettel_fehlender_ordner_exit_2(tmp_path, capsys, monkeypatch):
    """(e) --merkzettel auf fehlenden Ordner → Exit 2 (§16.1.2c)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, _out, err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                    "Auftragsbär",
                                    "--merkzettel", str(tmp_path / "gibts-nicht")])
    assert code == 2 and "existiert nicht" in err


def test_16_bau_umgebungsordner_fehlt_hinweis(tmp_path, capsys, monkeypatch):
    """(f) Umgebungsordner fehlt → Exit 0, Hinweis auf stderr, Grund „fehlt“."""
    ordner, _dekl = baue_projekt(tmp_path)
    fehlend = tmp_path / "kein-merk"
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(fehlend))
    code, paket, err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                     "Auftragsbär"])
    assert code == 0
    assert (f"kontextpaket bau: Hinweis — Merkzettel-Ordner {fehlend} fehlt, "
            "ohne Merkzettel gebaut") in err
    man = lade_manifest(paket)
    assert man["merkzettel"] is None
    assert man["merkzettel_grund"] == "fehlt"


def test_16_bau_vorgabe_ordner_ueber_home(tmp_path, capsys, monkeypatch):
    """Zusatz zu (d)/(e): ohne Umgebungsvariable greift die Vorgabe unter
    Path.home() (§16.1.2e)."""
    ordner, _dekl = baue_projekt(tmp_path)
    heimat = tmp_path / "heimat"
    merk = _merk(heimat / ".config" / "orchestrated-team",
                 {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"},
                 name="memory")
    monkeypatch.delenv("KONTEXTPAKET_MERKZETTEL", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: heimat))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär"])
    assert code == 0
    man = lade_manifest(paket)
    assert man["merkzettel"] == str(merk.resolve())
    assert man["merkzettel_grund"] == "vorgabe"
    assert "Heute" in _block9(paket)


def test_16_bau_notiz_aendern_macht_paket_nicht_veraltet(tmp_path, capsys,
                                                       monkeypatch):
    """(g) Merkzettel-Dateien stehen nicht in der Quellenliste — eine nach dem
    Bau geänderte Notiz macht das Paket nicht ungültig (§16.1.6)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär"])
    assert code == 0
    man = lade_manifest(paket)
    assert man["merkzettel"] == str(merk.resolve())   # Merkzettel war benutzt
    assert "Heute" in _block9(paket)
    quellen = {q["datei"] for q in man["quellen"]}
    assert all("merkzettel" not in q and "heute.md" not in q for q in quellen)
    (merk / "heute.md").write_text("## Heute\n\nGanz andere Notiz.\n",
                                   encoding="utf-8")
    paketdatei = tmp_path / "paket.md"
    paketdatei.write_text(paket, encoding="utf-8")
    code, _out, err = lauf(capsys, ["pruefe", str(paketdatei)])
    assert code == 0, err


def test_16_bau_jev_eingang_merkzettel_quelle(tmp_path, capsys, monkeypatch):
    """(h) Der Jev-Eingang enthält die Notiz mit quelle „merkzettel/<rel>“
    und ohne „datei“ — kein Maschinenpfad verlässt den Rechner (§16.1.3)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md":
                            "## Heute\n\nJev Regeln Zusatz marker-900.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(3)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, _paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                       "Jev Regeln Zusatz"])
    assert code == 0
    assert transport.aufrufe
    quellen = [a.get("quelle") for a in transport.aufrufe]
    assert "merkzettel/heute.md" in quellen
    assert all("datei" not in a for a in transport.aufrufe)
    assert str(merk.resolve()) not in json.dumps(transport.aufrufe,
                                               ensure_ascii=False)


def test_16_bau_merkzettel_schluesselmuster_zurueckgehalten(tmp_path, capsys,
                                                          monkeypatch):
    """(i) Notiz mit sk-…-Muster wird zurückgehalten wie ein Projektabschnitt —
    nicht gesendet, steht hinter den gesendeten in Block 9 (§14.11, §16.1.9i)."""
    geheim = "sk-" + "merkzettelgeheim123456"
    ordner = _jev_projekt(tmp_path, anzahl=2)
    merk = _merk(tmp_path, {"geheim.md":
                            "## Geheime Notiz\n\nJev Regeln Zusatz "
                            f"{geheim}\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    assert code == 0
    gesendet = [a for a in transport.aufrufe if a.get("quelle") != "kontrolle"]
    assert len(gesendet) == 2                       # nur die Projekt-Kandidaten
    assert geheim not in json.dumps(transport.aufrufe, ensure_ascii=False)
    man = lade_manifest(paket)
    assert man["jev"]["zurueckgehalten"] == 1
    assert "JEV_SCHLUESSELMUSTER" in json.dumps(man["befunde"],
                                              ensure_ascii=False)
    block = _block9(paket)
    abs_pfad = str((merk / "geheim.md").resolve())
    assert f"{abs_pfad}:" in block                 # wörtlich im Paket, absolut
    assert block.index("Kandidat 001") < block.index(abs_pfad)


def test_16_bau_dekl_merkzettel_kein_bool_befund(tmp_path, capsys):
    """(j) \"merkzettel\": \"ja\" → harter Befund, es gilt true (§16.1.2b)."""
    dekl = {**DEKL_GESUND, "merkzettel": "ja"}
    ordner, _dekl_pfad = baue_projekt(tmp_path, dekl=dekl)
    code, paket, _err = lauf(capsys, ["bau", str(ordner)])
    assert code == 1                                # harter Befund
    man = lade_manifest(paket)
    befunde_text = json.dumps(man["befunde"], ensure_ascii=False)
    assert "merkzettel ist kein boolescher Wert" in befunde_text
    # … und es gilt true: Umgebungsordner fehlt (conftest) → Grund „fehlt“
    assert man["merkzettel_grund"] == "fehlt"


def test_16_bau_merkzettel_nur_lesend(tmp_path, capsys, monkeypatch):
    """(k) bau liest den Merkzettel nur — Bytes und mtime unverändert (§16.1.8)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    notiz = merk / "heute.md"
    vorher = (notiz.read_bytes(), notiz.stat().st_mtime_ns)
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär"])
    assert code == 0
    assert lade_manifest(paket)["merkzettel"] == str(merk.resolve())
    assert (notiz.read_bytes(), notiz.stat().st_mtime_ns) == vorher


# === U7-1b: §16.2 Verweise im Merkzettel absichern ==========================

def test_16_2_toter_symlink_laesst_bau_nicht_scheitern(tmp_path, capsys,
                                                      monkeypatch):
    """(a) Toter *.md-Link im Merkzettel: bau Exit 0, der Link fehlt, die echte
    Notiz daneben erscheint — still übergangen (war B1, §16.2.1/§16.2.4a)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md":
                            "## Heute\n\nDer Auftragsbär notiert.\n"})
    (merk / "tot-link.md").symlink_to(tmp_path / "gibt-es-nicht" / "n.md")
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                     "Auftragsbär"])
    assert code == 0, err
    block = _block9(paket)
    assert "tot-link" not in block           # die tote Notiz fehlt …
    assert "tot-link" not in err             # … und wird nicht gemeldet
    assert "Heute" in block                  # echte Notiz daneben erscheint
    # §16.2.3: dieselbe Schranke gilt für kandidaten --merkzettel
    kcode, kout, _kerr = lauf(capsys, ["kandidaten", str(ordner), "--auftrag",
                                       "Auftragsbär", "--merkzettel",
                                       str(merk)])
    assert kcode == 0
    assert "tot-link" not in kout and "merkzettel/heute.md" in kout


def test_16_2_symlink_nach_aussen_gesperrt(tmp_path, capsys, monkeypatch):
    """(b) *.md-Link auf ein Ziel außerhalb des Merkzettels: der Inhalt steht
    weder in Block 9 noch im Jev-Eingang (war B2, §16.2.1/§16.2.4b)."""
    ordner = _jev_projekt(tmp_path, anzahl=1)
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "fremd.md").write_text(
        "## Fremd\n\nJev Regeln Zusatz fremdmarker-4242.\n", encoding="utf-8")
    merk = tmp_path / "merkzettel-echt"
    merk.mkdir()
    (merk / "bruecke.md").symlink_to(aussen / "fremd.md")
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    monkeypatch.setenv("TYPESAFE_API_KEY", "falscher-test-schluessel")
    transport = _jev_fake_transport(2)
    monkeypatch.setattr(kp, "JEV_TRANSPORT", transport)
    code, paket, _err = _jev_bau(capsys, ordner)
    assert code == 0
    assert "fremdmarker-4242" not in paket
    assert "bruecke" not in _block9(paket)
    assert "fremdmarker-4242" not in json.dumps(transport.aufrufe,
                                              ensure_ascii=False)
    assert all(a.get("quelle") != "merkzettel/bruecke.md"
               for a in transport.aufrufe)
    # §16.2.3: auch kandidaten --merkzettel liest das Außen-Ziel nicht
    kcode, kout, _kerr = lauf(capsys, ["kandidaten", str(ordner), "--auftrag",
                                       "Jev Regeln Zusatz", "--merkzettel",
                                       str(merk)])
    assert kcode == 0
    assert "fremdmarker-4242" not in kout and "bruecke" not in kout


def test_16_2_symlink_innerhalb_bleibt_erlaubt(tmp_path, capsys, monkeypatch):
    """(c) Link innerhalb des Ordners auf eine Notiz im selben Ordner bleibt
    erlaubt; Fundstelle und quelle nennen den Linknamen (§16.2.2)."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"echt.md": "## Echt\n\nDer Auftragsbär notiert.\n"})
    (merk / "alias.md").symlink_to(merk / "echt.md")
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL", str(merk))
    code, paket, _err = lauf(capsys, ["bau", str(ordner), "--auftrag",
                                      "Auftragsbär"])
    assert code == 0
    merk_echt = merk.resolve()
    block = _block9(paket)
    assert f"{merk_echt / 'alias.md'}:1 — Echt" in block   # Linkname, nicht Ziel
    assert f"{merk_echt / 'echt.md'}:1 — Echt" in block


@pytest.mark.skipif(not hasattr(os, "mkfifo"),
                    reason="os.mkfifo existiert nur auf POSIX")
def test_16_2_fifo_blockiert_bau_nicht(tmp_path):
    """(d) Eine FIFO namens *.md ist keine echte Datei — bau blockiert nicht
    (§16.2.4d). Unterprozess mit Zeitobrense: ohne den is_file-Schutz würde
    lies_datei die FIFO öffnen und ewig auf einen Schreiber warten."""
    ordner, _dekl = baue_projekt(tmp_path)
    merk = _merk(tmp_path, {"heute.md": "## Heute\n\nAuftragsbär-Notiz.\n"})
    os.mkfifo(merk / "roehre.md")
    kp_pfad = str(WERKZEUGE / "kontextpaket.py")
    laufwerk = subprocess.run(
        [sys.executable, kp_pfad, "bau", str(ordner), "--auftrag",
         "Auftragsbär", "--merkzettel", str(merk), "--ohne-jev"],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert laufwerk.returncode == 0, laufwerk.stderr
    assert "Heute" in laufwerk.stdout
    assert "roehre" not in laufwerk.stdout

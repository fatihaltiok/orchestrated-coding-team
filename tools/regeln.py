"""regeln: Eine Regelquelle für Claude und Codex (Vertrag §13).

    python3 tools/regeln.py erzeuge [--ziel claude|codex] [--ausgabe ORDNER]
        [--quelle ORDNER]
    python3 tools/regeln.py pruefe [--ziel claude|codex] [--quelle ORDNER]
    python3 tools/regeln.py installiere --ziel claude|codex [--quelle ORDNER] --ja
    python3 tools/regeln.py rueckgabe --ziel claude|codex [--quelle ORDNER] --ja

Leitsatz (§13): Die globalen Regeln haben eine Quelle im Repo
(regeln/CLAUDE.md); ~/.claude/CLAUDE.md und ~/.codex/AGENTS.md sind
Erzeugnisse. Kein Unterbefehl schreibt dorthin ohne den ausdrücklichen
Befehl installiere/rueckgabe mit --ja; die erste Installation und jede
inhaltliche Regeländerung braucht zusätzlich Fatihs OK. Beim Umzug geht
keine Regel verloren — Gate 2 belegt es mechanisch: Rückwärtsanwendung
der Ersetzungstabelle auf das Codex-Erzeugnis ergibt bytegenau die Quelle.

Ausgabe/Encoding (§13.6): UTF-8, Bytes 1:1 — kein Zeilenende-Umbau, kein
Strip, abschließender Zeilenumbruch wie in der Quelle. Ersetzungen sind
wörtlich (kein Regex); `anzahl` gilt gegen die Quelle, und `neu` darf in
der Quelle 0-mal vorkommen, sonst Exit 2 (Gate 2 wäre nicht eindeutig).

Heimat (§13.6): REGELN_HOME ersetzt ~ vollständig — Ziele
$REGELN_HOME/.claude/CLAUDE.md, $REGELN_HOME/.codex/AGENTS.md, Sicherungen
$REGELN_HOME/.local/state/regeln/sicherung/. Ohne REGELN_HOME gilt
Path.home(). Die Quelle liegt standardmäßig unter
~/.config/orchestrated-team/regeln, überschreibbar durch REGELN_QUELLE
oder --quelle (Tests benutzen Kopien unter tmp_path).

Exit 0 = ok · 1 = Drift (nur pruefe; fehlende installierte Datei zählt als
Drift) · 2 = Aufruffehler (falsche Ersetzungstabelle, fehlendes --ja,
Symlink-Ziel, verbotenes --ausgabe …). Nur Standardbibliothek, kein Netz.

Nachtrag §13.8 (Gegenprüfung R3, 27.09.2026): --ausgabe schreibt nur
symlinkfrei und atomar (R3-1); installiere/rueckgabe verweigern JEDE
Symlink-Komponente ab der Heimat, auch im Sicherungspfad (R3-2);
Ersetzungen sind paarweise wechselwirkungsfrei, `alt` nicht leer, vor
jedem replace gilt `ergebnis.count(alt) == anzahl` (R3-3); die
Drift-Diagnose vergleicht Zeilen mit Ende und nennt sonst die
Byte-Position (R3-4); Sicherungsnamen tragen UTC-Zeit mit Z-Suffix und
sind chronologisch sortiert, auch Altbestände mit Offset (R3-5);
Eingabe-/E/A-Fehler enden mit Exit 2 statt Traceback (R3-6); REGELN_HOME
leer ist ein Aufruffehler, nie ein stiller Rückfall aufs echte ~ (R3-7.6);
rueckgabe sichert die installierte Datei, bevor sie zurückschreibt (O-1);
das --ausgabe-Verbot gilt immer auch für das echte ~ (O-2).
"""

import argparse
import difflib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_AUFRUFFEHLER = 2

# Gate 3 (§13.6): U3-0 hat gemessen, dass Codex eine globale AGENTS.md bis
# mindestens 66 561 Bytes vollständig liest; das Gate bewacht 64 000 Bytes
# (gemessener Wert minus 2 KB Puffer). Wird es überschritten, ist vor dem
# Anheben neu zu messen — die Grenze ist ein Messwert, keine Herstellerangabe.
CODEX_GRENZE_BYTES = 64000

DIFF_MAX_ZEILEN = 40          # §13.2: diff -u gekürzt auf 40 Zeilen

STANDARD_QUELLE = Path(os.environ.get("REGELN_QUELLE", "~/.config/orchestrated-team/regeln")).expanduser()   # §13.6
SICHERUNG_RELATIV = Path(".local") / "state" / "regeln" / "sicherung"

# ziel -> (Ordner unter der Heimat, Dateiname) (§13.6)
ZIELE = {"claude": (".claude", "CLAUDE.md"), "codex": (".codex", "AGENTS.md")}


class Aufruffehler(Exception):
    """Aufruffehler (Exit 2), z. B. falsche Ersetzungstabelle oder fehlendes --ja."""


def heimat():
    """Heimatordner: REGELN_HOME ersetzt ~ vollständig (§13.6), sonst Path.home().
    Setzt, aber LEER — Exit 2, nie still auf das echte ~ zurückfallen (§13.8)."""
    roh = os.environ.get("REGELN_HOME")
    if roh is None:
        return Path.home()
    if roh == "":
        raise Aufruffehler(
            "REGELN_HOME ist gesetzt, aber leer — nicht auswertbar (§13.8). "
            "Setze es auf einen Ordner oder lösche die Variable; es fällt nie "
            "still auf das echte ~ zurück.")
    return Path(roh)


def ziel_pfad_fuer(ziel):
    ordner_name, datei_name = ZIELE[ziel]
    return heimat() / ordner_name / datei_name


def quelle_ordner(args):
    quelle = Path(args.quelle) if getattr(args, "quelle", None) else STANDARD_QUELLE
    if not quelle.is_dir():
        raise Aufruffehler(f"Quellordner existiert nicht: {quelle} (§13.1)")
    return quelle


def lies_bytes(pfad):
    try:
        return pfad.read_bytes()
    except OSError as e:
        raise Aufruffehler(f"Datei nicht lesbar: {pfad} ({e})")


# --- Erzeugnis bauen (§13.1, §13.6) --------------------------------------------------

def lade_tabelle(quelle):
    """ersetzungen-codex.json laden und formen -> [(alt_bytes, neu_bytes, anzahl)]."""
    pfad = quelle / "ersetzungen-codex.json"
    if not pfad.is_file():
        raise Aufruffehler(f"Ersetzungstabelle fehlt: {pfad} (§13.1)")
    try:
        roh = json.loads(lies_bytes(pfad).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Aufruffehler(f"{pfad} kein gültiges JSON: {e}")
    if not isinstance(roh, list):
        raise Aufruffehler(f"{pfad} ist keine Liste von Ersetzungen (§13.1)")
    tabelle = []
    for i, eintrag in enumerate(roh, 1):
        if not isinstance(eintrag, dict) or not {"alt", "neu", "anzahl"} <= set(eintrag):
            raise Aufruffehler(
                f"{pfad} Eintrag {i}: erwartet alt/neu/anzahl, bekommt "
                f"{sorted(eintrag) if isinstance(eintrag, dict) else type(eintrag).__name__} (§13.1)")
        anzahl = eintrag["anzahl"]
        if not isinstance(anzahl, int) or isinstance(anzahl, bool) or anzahl < 0:
            raise Aufruffehler(
                f"{pfad} Eintrag {i}: anzahl muss eine nichtnegative Zahl sein (§13.1)")
        alt, neu = eintrag["alt"], eintrag["neu"]
        if not isinstance(alt, str) or not isinstance(neu, str):
            raise Aufruffehler(
                f"{pfad} Eintrag {i}: alt und neu müssen Zeichenketten sein, "
                f"bekommt {type(alt).__name__}/{type(neu).__name__} (§13.8)")
        if alt == "":
            raise Aufruffehler(
                f"{pfad} Eintrag {i}: alt darf nicht leer sein — ein leerer "
                f"Suchtext trifft alles und macht die Ersetzung nicht eindeutig (§13.8)")
        tabelle.append((alt.encode("utf-8"), neu.encode("utf-8"), anzahl))
    paarweise_wechselwirkung(pfad, tabelle)
    return tabelle


def paarweise_wechselwirkung(pfad, tabelle):
    """§13.8: kein alt_i darf Teilstring eines alt_j oder neu_j sein (i ≠ j) —
    sonst überdeckt/überlappt die eine Ersetzung die andere, das Erzeugnis wird
    still falsch (R3-3, Szenen A/B)."""
    texte = [(alt.decode("utf-8"), neu.decode("utf-8")) for alt, neu, _ in tabelle]
    for i, (alt_i, _neu_i) in enumerate(texte, 1):
        for j, (alt_j, neu_j) in enumerate(texte, 1):
            if i == j:
                continue
            if alt_i in alt_j:
                raise Aufruffehler(
                    f"{pfad} Wechselwirkung Eintrag {i}/{j}: alt_{i} ist "
                    f"Teilstring von alt_{j} (§13.8) — nichts wird erzeugt")
            if alt_i in neu_j:
                raise Aufruffehler(
                    f"{pfad} Wechselwirkung Eintrag {i}/{j}: alt_{i} ist "
                    f"Teilstring von neu_{j} (§13.8) — nichts wird erzeugt")


def erzeuge_bytes(quelle, ziel):
    """Erzeugnis für ziel als Bytes — Bytes 1:1, nichts wird gestrippt (§13.6)."""
    quelle_bytes = lies_bytes(quelle / "CLAUDE.md")
    if ziel == "claude":
        return quelle_bytes
    ergebnis = quelle_bytes
    for i, (alt, neu, anzahl) in enumerate(lade_tabelle(quelle), 1):
        treffer = quelle_bytes.count(alt)     # `anzahl` gilt gegen die Quelle (§13.1)
        if treffer != anzahl:
            muster = repr(alt.decode("utf-8", "replace"))[:60]
            raise Aufruffehler(
                f"Ersetzung {i}: {muster} trifft {treffer}-mal auf die Quelle, "
                f"erwartet {anzahl} (§13.1) — nichts wird erzeugt")
        if quelle_bytes.count(neu) != 0:
            # §13.6: sonst ist Gate 2, die Rückwärtsanwendung, nicht eindeutig.
            muster = repr(neu.decode("utf-8", "replace"))[:60]
            raise Aufruffehler(
                f"Ersetzung {i}: {muster} kommt in der Quelle schon vor — "
                f"Rückwärtsanwendung (Gate 2) wäre nicht eindeutig (§13.6)")
        if ergebnis.count(alt) != anzahl:
            # §13.8 Punkt 3 (R3-3): vor JEDEM replace — die Ersetzung greift am
            # laufenden Ergebnis; überlappte Einträge dürfen nichts still
            # übergehen oder an der falschen Stelle treffen.
            muster = repr(alt.decode("utf-8", "replace"))[:60]
            raise Aufruffehler(
                f"Ersetzung {i}: {muster} trifft {ergebnis.count(alt)}-mal auf das "
                f"Zwischenergebnis, erwartet {anzahl} (§13.8) — nichts wird erzeugt")
        ergebnis = ergebnis.replace(alt, neu, anzahl)
    return ergebnis


def pruefe_ausgabe_ordner(aus):
    """--ausgabe darf nicht der Regel-Ordner unter der Heimat sein und — immer
    zusätzlich (§13.8 Punkt 9, O-2) — nicht das echte Path.home()/.claude oder
    .codex, auch nicht über Symlink aufgelöst (§13.6)."""
    aufgeloest = aus.resolve()
    basis_liste = [heimat()]
    echtes = Path.home()
    if echtes != heimat():
        basis_liste.append(echtes)
    for basis in basis_liste:
        for ordner_name, _datei_name in ZIELE.values():
            ziel_ordner = (basis / ordner_name).resolve()
            if aufgeloest == ziel_ordner:
                raise Aufruffehler(
                    f"--ausgabe darf nicht der Regel-Ordner {basis / ordner_name} "
                    f"sein — auch nicht über Symlink aufgelöst (§13.6, §13.8)")


# --- Unterbefehl: erzeuge (§13.2) ------------------------------------------------------

def run_erzeuge(args):
    quelle = quelle_ordner(args)
    erzeugt = erzeuge_bytes(quelle, args.ziel)
    if args.ausgabe:
        aus = Path(args.ausgabe)
        pruefe_ausgabe_ordner(aus)
        datei = aus / ZIELE[args.ziel][1]
        verweigere_symlink(datei)       # §13.8 Punkt 1 (R3-1): nie durch einen
        schreibe_atomar(datei, erzeugt)  # Symlink schreiben; os.replace ersetzt ihn
        print(f"regeln erzeuge: geschrieben: {datei} ({len(erzeugt)} Bytes)",
              file=sys.stderr)
    else:
        sys.stdout.buffer.write(erzeugt)
        sys.stdout.buffer.flush()
    return EXIT_OK


# --- Unterbefehl: pruefe (§13.2) ------------------------------------------------------

def erste_abweichende_zeile(installiert_text, erzeugt_text):
    """Nummer (1-basiert) der ersten abweichenden Zeile — Zeilen MIT Ende
    verglichen (splitlines(keepends=True), §13.8 Punkt 4), damit auch reine
    Zeilenenden-Drift eine Zeile nennt — oder None, wenn die Zeilen gleich sind."""
    a = installiert_text.splitlines(keepends=True)
    b = erzeugt_text.splitlines(keepends=True)
    for i in range(max(len(a), len(b))):
        if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None):
            return i + 1
    return None


def erste_abweichendes_byte(installiert_bytes, erzeugt_bytes):
    """Position (1-basiert) des ersten abweichenden Bytes; liegt die eine Datei
    komplett als Präfix in der anderen, die Position dahinter."""
    for i, (x, y) in enumerate(zip(installiert_bytes, erzeugt_bytes), 1):
        if x != y:
            return i
    return min(len(installiert_bytes), len(erzeugt_bytes)) + 1


def diff_u_kuerzen(installiert_bytes, erzeugt_bytes, von_name, bis_name):
    """diff -u (§13.2), gekürzt auf DIFF_MAX_ZEILEN Zeilen."""
    zeilen = list(difflib.unified_diff(
        installiert_bytes.decode("utf-8", "replace").splitlines(),
        erzeugt_bytes.decode("utf-8", "replace").splitlines(),
        fromfile=von_name, tofile=bis_name, lineterm=""))
    return zeilen[:DIFF_MAX_ZEILEN]


def pruefe_ein_ziel(quelle, ziel):
    """Ein Ziel prüfen (nur lesend) -> True bei Drift; Befunde nach stderr."""
    ziel_pfad = ziel_pfad_fuer(ziel)
    erzeugt = erzeuge_bytes(quelle, ziel)
    if ziel_pfad.is_symlink():
        print(f"regeln pruefe: {ziel_pfad} ist ein Symlink — als installierte "
              f"Datei unzulässig (Lehre 20.09., §13.2)", file=sys.stderr)
        return True
    if not ziel_pfad.is_file():
        # §13.6: eine fehlende installierte Datei ist Drift („fehlt“).
        print(f"regeln pruefe: {ziel_pfad} fehlt — Drift (§13.6)", file=sys.stderr)
        return True
    installiert = ziel_pfad.read_bytes()
    if installiert == erzeugt:
        return False
    nr = erste_abweichende_zeile(
        installiert.decode("utf-8", "replace"), erzeugt.decode("utf-8", "replace"))
    byte_n = erste_abweichendes_byte(installiert, erzeugt)
    if nr is None:
        # §13.8 Punkt 4: die Zeilen (mit Ende) sind gleich, die Bytes nicht —
        # die Diagnose darf nicht leer bleiben (R3-4).
        print(f"regeln pruefe: {ziel_pfad} weicht ab — die Dateien unterscheiden "
              f"sich nur in Zeilenenden/Steuerzeichen, erste Abweichung ab Byte "
              f"{byte_n} (§13.2, §13.8)", file=sys.stderr)
        return True
    print(f"regeln pruefe: {ziel_pfad} weicht ab — erste abweichende Zeile: {nr} "
          f"(§13.2)", file=sys.stderr)
    diff_zeilen = diff_u_kuerzen(installiert, erzeugt,
                                 f"{ziel_pfad} (installiert)",
                                 f"{ziel} (erzeugt aus {quelle.name})")
    for zeile in diff_zeilen:
        print(f"  {zeile}", file=sys.stderr)
    if not diff_zeilen:
        # Zeileninhalte sind gleich, aber eine Zeile trägt ein anderes Ende/
        # Steuerzeichen — das diff allein zeigt nichts, also den Ort nennen.
        print(f"  (die Zeileninhalte sind gleich — Unterschied nur in "
              f"Zeilenenden/Steuerzeichen, erste Abweichung ab Byte {byte_n})",
              file=sys.stderr)
    print("  wer hat die installierte Datei von Hand geändert? "
          "erst in regeln/ nachtragen (§13.2)", file=sys.stderr)
    return True


def run_pruefe(args):
    quelle = quelle_ordner(args)
    ziele = [args.ziel] if args.ziel else ["claude", "codex"]   # §13.6: ohne --ziel beide
    drift = False
    for ziel in ziele:
        drift = pruefe_ein_ziel(quelle, ziel) or drift
    if drift:
        print("regeln pruefe: Drift (Exit 1)", file=sys.stderr)
        return EXIT_DRIFT
    print(f"regeln pruefe: {', '.join(ziele)} bytegenau wie die Quelle "
          f"{quelle} (§13.2)", file=sys.stderr)
    return EXIT_OK


# --- Schreiben unter der Heimat (§13.2, §13.6) ---------------------------------------

def schreibe_atomar(pfad, inhalt_bytes):
    """Atomar schreiben (§13.2): temporäre Datei im selben Ordner + os.replace."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=pfad.parent, prefix=f".{pfad.name}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(inhalt_bytes)
        os.replace(tmp_name, pfad)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def sichere_installierte(ziel_pfad, ziel):
    """Installierte Datei nach $REGELN_HOME/.local/state/regeln/sicherung/
    <ISO-Zeit>-<ziel>.md sichern (§13.2); None, wenn nichts zu sichern ist
    (§13.6: Sicherungsdatei nur anlegen, wenn eine installierte Datei existiert).
    Die Zeit ist UTC mit Z-Suffix (§13.8 Punkt 5, R3-5) — nur so ist der Name
    lexikographisch immer chronologisch sortiert, auch über eine
    Zeitzonenumstellung hinweg. Ein Zählersuffix löst Kollisionen innerhalb
    derselben Sekunde, ohne die Sortierung zu verletzen."""
    ordner = heimat() / SICHERUNG_RELATIV
    inhalt = lies_bytes(ziel_pfad)     # Ziel ist z. B. ein Ordner → Exit 2, vorher nichts angelegt
    ordner.mkdir(parents=True, exist_ok=True)
    zeit = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ziel_datei = ordner / f"{zeit}-{ziel}.md"
    n = 0
    while ziel_datei.exists():
        n += 1
        ziel_datei = ordner / f"{zeit}.{n:02d}-{ziel}.md"
    ziel_datei.write_bytes(inhalt)
    return ziel_datei


def sicherungs_schluessel(pfad, ziel):
    """Sortierschlüssel für Sicherungsnamen: die geparste ISO-Zeit (§13.8
    Punkt 5), danach der Name. Altbestände mit Offset-Namen (vor der
    Umstellung) sortieren so ebenfalls chronologisch — ein Sommerzeit-Sprung
    zwischen zwei Sicherungen kippt die Reihenfolge nicht mehr (R3-5)."""
    stempel = pfad.name
    endung = f"-{ziel}.md"
    if stempel.endswith(endung):
        stempel = stempel[:-len(endung)]
    stueck = stempel.rsplit(".", 1)
    if len(stueck) == 2 and stueck[1].isdigit():     # Kollisions-Zähler .NN abtrennen
        stempel = stueck[0]
    try:
        zeit = datetime.fromisoformat(stempel.replace("Z", "+00:00"))
        if zeit.tzinfo is None:                      # Offset-freier Altbestand → UTC
            zeit = zeit.replace(tzinfo=timezone.utc)
    except ValueError:
        zeit = datetime.min.replace(tzinfo=timezone.utc)   # unparsebar → ganz alt
    return (zeit, pfad.name)                         # Name als Zweit-Schlüssel: …Z < …Z.01


def juengste_sicherung(ziel):
    """Jüngste Sicherung für ziel, chronologisch über den geparsten ISO-Namen
    (§13.8 Punkt 5); None, wenn es keine gibt (§13.2: rueckgabe ohne
    Sicherung Exit 2)."""
    ordner = heimat() / SICHERUNG_RELATIV
    if not ordner.is_dir():
        return None
    kandidaten = sorted(ordner.glob(f"*-{ziel}.md"),
                        key=lambda p: sicherungs_schluessel(p, ziel))
    return kandidaten[-1] if kandidaten else None


def verweigere_symlink(pfad, ab=None):
    """Nie durch einen Symlink schreiben (Lehre 20.09., §13.2/§13.8): `pfad`
    selbst und — wenn `ab` gegeben ist — jede Komponente von pfad ab `ab`
    (Heimat selbst ausgenommen, §13.8 Punkt 2) darf kein Symlink sein; sonst
    Exit 2, bevor irgendetwas gesichert oder geschrieben wird."""
    pfad_abs = Path(os.path.abspath(pfad))
    grenze = Path(os.path.abspath(ab)) if ab is not None else pfad_abs.parent
    kette = []
    aktuell = pfad_abs
    while aktuell != grenze and aktuell.parent != aktuell:
        kette.append(aktuell)
        aktuell = aktuell.parent
    for k in reversed(kette):                    # von außen nach innen melden
        if k.is_symlink():
            raise Aufruffehler(
                f"{k} ist ein Symlink — nie durch einen Symlink schreiben "
                f"(Lehre 20.09., §13.2, §13.8)")


def pruefe_schreibziele(ziel):
    """§13.8 Punkt 2 (R3-2): für installiere/rueckgabe darf KEINE Komponente
    des Zielpfads ab der Heimat (.claude/.codex und die Datei) und keine des
    Sicherungsordners (.local, state, regeln, sicherung) ein Symlink sein —
    Exit 2, nichts geschrieben, keine Sicherung."""
    basis = heimat()
    verweigere_symlink(ziel_pfad_fuer(ziel), ab=basis)
    verweigere_symlink(basis / SICHERUNG_RELATIV, ab=basis)


# --- Unterbefehle: installiere / rueckgabe (§13.2) ------------------------------------

def run_installiere(args):
    if not args.ja:
        raise Aufruffehler("installiere schreibt nur mit --ja (§13.2) — nichts geschehen")
    quelle = quelle_ordner(args)
    ziel_pfad = ziel_pfad_fuer(args.ziel)
    pruefe_schreibziele(args.ziel)     # §13.8: Ziel- und Sicherungspfad symlinkfrei
    erzeugt = erzeuge_bytes(quelle, args.ziel)
    if ziel_pfad.exists():
        sicherung = sichere_installierte(ziel_pfad, args.ziel)
        print(f"regeln installiere: gesichert: {sicherung}", file=sys.stderr)
    schreibe_atomar(ziel_pfad, erzeugt)
    print(f"regeln installiere: geschrieben: {ziel_pfad} ({len(erzeugt)} Bytes)",
          file=sys.stderr)
    return run_pruefe(args)          # §13.2: ruft danach selbst pruefe auf


def run_rueckgabe(args):
    if not args.ja:
        raise Aufruffehler("rueckgabe schreibt nur mit --ja (§13.2) — nichts geschehen")
    ziel_pfad = ziel_pfad_fuer(args.ziel)
    pruefe_schreibziele(args.ziel)     # §13.8: Ziel- und Sicherungspfad symlinkfrei
    # §13.8 Punkt 8 (O-1): erst die jüngste BESTEHENDE Sicherung bestimmen,
    # DANACH die aktuell installierte Datei sichern (sonst wäre die neue
    # Sicherung selbst die jüngste und rueckgabe ein Stillstand), erst dann
    # zurückschreiben — eine von Hand geänderte installierte Datei ist danach
    # in einer Sicherung erhalten, nichts geht verloren.
    sicherung = juengste_sicherung(args.ziel)
    if sicherung is None:
        raise Aufruffehler(
            f"keine Sicherung für {args.ziel} in "
            f"{heimat() / SICHERUNG_RELATIV} (§13.2) — nichts geschehen")
    inhalt = lies_bytes(sicherung)
    if ziel_pfad.exists():
        gesichert = sichere_installierte(ziel_pfad, args.ziel)
        print(f"regeln rueckgabe: gesichert: {gesichert}", file=sys.stderr)
    schreibe_atomar(ziel_pfad, inhalt)
    print(f"regeln rueckgabe: {sicherung.name} wiederhergestellt nach {ziel_pfad} "
          f"({len(inhalt)} Bytes)", file=sys.stderr)
    return EXIT_OK


# --- Kommandozeile ----------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="regeln.py",
        description="Eine Regelquelle für Claude und Codex (Vertrag §13). "
                    "~/.claude/CLAUDE.md und ~/.codex/AGENTS.md werden nur mit "
                    "installiere/rueckgabe --ja angetastet.")
    sub = parser.add_subparsers(dest="befehl", required=True)

    p_erzeuge = sub.add_parser("erzeuge", help="Erzeugnis nach stdout oder --ausgabe")
    p_erzeuge.add_argument("--ziel", choices=sorted(ZIELE), default="claude")
    p_erzeuge.add_argument("--ausgabe", metavar="ORDNER")
    p_erzeuge.add_argument("--quelle", metavar="ORDNER")

    p_pruefe = sub.add_parser("pruefe",
                              help="installierte Dateien bytegenau mit dem Erzeugnis vergleichen")
    p_pruefe.add_argument("--ziel", choices=sorted(ZIELE))
    p_pruefe.add_argument("--quelle", metavar="ORDNER")

    p_install = sub.add_parser("installiere",
                               help="Erzeugnis installieren — nur mit --ja, sichert vorher")
    p_install.add_argument("--ziel", choices=sorted(ZIELE), required=True)
    p_install.add_argument("--quelle", metavar="ORDNER")
    p_install.add_argument("--ja", action="store_true")

    p_rueck = sub.add_parser("rueckgabe",
                             help="jüngste Sicherung wiederherstellen — nur mit --ja")
    p_rueck.add_argument("--ziel", choices=sorted(ZIELE), required=True)
    p_rueck.add_argument("--quelle", metavar="ORDNER")
    p_rueck.add_argument("--ja", action="store_true")

    args = parser.parse_args(argv)
    laeufe = {"erzeuge": run_erzeuge, "pruefe": run_pruefe,
              "installiere": run_installiere, "rueckgabe": run_rueckgabe}
    try:
        return laeufe[args.befehl](args)
    except Aufruffehler as e:
        print(f"regeln: {e}", file=sys.stderr)
        return EXIT_AUFRUFFEHLER
    except OSError as e:
        # §13.8: E/A-Fehler (Ziel ist ein Ordner, --ausgabe ist eine Datei, …)
        # sind Aufruffehler Exit 2 mit Pfad in der Meldung — kein Traceback.
        pfad = f": {e.filename}" if getattr(e, "filename", None) else ""
        print(f"regeln: E/A-Fehler ({e}){pfad}", file=sys.stderr)
        return EXIT_AUFRUFFEHLER


if __name__ == "__main__":
    sys.exit(main())

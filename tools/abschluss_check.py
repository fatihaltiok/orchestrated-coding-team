"""Abschluss-Check: ungesicherte Arbeit und Fremd-Spuren je Repo melden
(Vertrag: INTERFACES.md §8.2, §9.3; E-021, E-023).

    python3 tools/abschluss_check.py [PFAD ...] [--alle] [--seit REV] [--json]
    python3 tools/abschluss_check.py --alle            # alle Repos aus tools/repos.json
    python3 tools/abschluss_check.py --seit v1.2.0     # Trailer-Suche ab REV (..HEAD)
    python3 tools/abschluss_check.py . --ohne-regeln   # Regelblock weglassen

Ohne Pfad gilt das aktuelle Verzeichnis. `--alle` nimmt die Liste aus
`tools/repos.json` (gepflegt nur vom Orchestrator). Liest nur, schreibt nie
in ein fremdes Repo (kein `git fetch`; verglichen wird mit dem zuletzt bekannten
Remote-Stand). `OFFENE_ARBEITSKOPIE` meldet weitere Einträge aus
`git worktree list --porcelain` neben der Hauptkopie (nur lesend).
Je Aufruf (nicht je Repo) zusätzlich die globalen Regeldateien: das Erzeugnis
aus der Standardquelle (§13) gegen die installierte Datei unter regeln.heimat()
— nur lesend, Befund-Kürzel REGELN_DRIFT, Block `Regeln (global):`; `--ohne-regeln`
lässt ihn ganz weg (Vertrag §13.10).
Exit 0 = kein Befund, 1 = mindestens ein Befund, 2 = Aufruffehler.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOS_JSON = Path(__file__).resolve().parent / "repos.json"

# regeln.py in-process laden (§13.10): die Logik wird benutzt, nie kopiert.
_regeln_spec = importlib.util.spec_from_file_location(
    "regeln", Path(__file__).resolve().parent / "regeln.py")
regeln = importlib.util.module_from_spec(_regeln_spec)
_regeln_spec.loader.exec_module(regeln)

TMP_SKRPIT_MUSTER = re.compile(r"/tmp/\S+\.(?:py|sh)")

# Reihenfolge der Tabelle in INTERFACES.md §8.2 (§9.3: OFFENE_ARBEITSKOPIE nach UNCOMMITTET)
KUERZEL_ORDNUNG = [
    "UNCOMMITTET",
    "OFFENE_ARBEITSKOPIE",
    "FREMDES_REPO",
    "KEIN_REPO",
    "NICHT_GESICHERT",
    "REMOTE_FEHLT",
    "FREMDER_TRAILER",
    "SCRATCHPAD_VERWEIS",
    "UNGESCHUETZTES_UNTERREPO",
    "REGELN_DRIFT",   # nicht je Repo, sondern im Block „Regeln (global):“ (§13.10)
]

HINWEIS_REGELN = ("regeln pruefe zeigt den Unterschied; "
                  "erst in regeln/ nachtragen")   # §13.10 Punkt 2, wörtlich


class Aufruffehler(Exception):
    """Aufruffehler (Exit 2), z.B. unbekannte Revision bei --seit."""


def git(pfad, *args):
    """Ein lesender Git-Befehl; nie fetch/pull/push."""
    return subprocess.run(
        ["git", "-C", str(pfad), *args],
        capture_output=True, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},  # status frischt sonst den Index auf (Schreiben)
    )


def lade_repos():
    """repos.json -> {realpath: {"remote_erwartet": bool, "elternrepo": bool}}."""
    try:
        daten = json.loads(REPOS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Aufruffehler(f"repos.json nicht lesbar ({REPOS_JSON}): {e}")
    tabelle = {}
    for eintrag in daten.get("repos", []):
        tabelle[os.path.realpath(eintrag["pfad"])] = {
            "remote_erwartet": bool(eintrag.get("remote_erwartet", False)),
            "elternrepo": bool(eintrag.get("elternrepo", False)),
        }
    return tabelle


def hat_upstream(pfad):
    return git(pfad, "rev-parse", "--verify", "--quiet", "@{u}").returncode == 0


def hat_head(pfad):
    return git(pfad, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0


def oberste_ebene(pfad):
    r = git(pfad, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        return os.path.realpath(pfad)  # z.B. kahles Repo: robust weiter
    return os.path.realpath(r.stdout.strip())


def trailer_commits(pfad, seit):
    """Hashes im Prüfbereich, deren Nachricht Co-Authored-By enthält (groß/klein egal)."""
    if seit:
        if git(pfad, "rev-parse", "--verify", "--quiet", seit).returncode != 0:
            raise Aufruffehler(f"unbekannte Revision --seit {seit!r} in {pfad}")
        bereich = [f"{seit}..HEAD"]
    elif hat_upstream(pfad):
        bereich = ["@{u}..HEAD"]
    else:
        if not hat_head(pfad):
            return []
        bereich = ["-1", "HEAD"]
    r = git(pfad, "log", "--format=%H", *bereich)
    if r.returncode != 0:
        raise Aufruffehler(f"git log scheitert in {pfad}: {r.stderr.strip()}")
    treffer = []
    for h in r.stdout.split():
        text = git(pfad, "show", "-s", "--format=%h %s%n%B", h).stdout
        if "co-authored-by" in text.lower():
            treffer.append(text.splitlines()[0] if text.splitlines() else h)
    return treffer


def scratchpad_treffer(pfad, wurzel):
    """(treffer, ausgenommen): versionierte .md-Dateien mit /tmp/*.py|sh-Verweis."""
    r = git(pfad, "ls-files", "--", "*.md")
    treffer, ausgenommen = [], 0
    for rel in sorted(r.stdout.splitlines()):
        datei = Path(wurzel) / rel
        try:
            zeilen = datei.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for nr, zeile in enumerate(zeilen, 1):
            if TMP_SKRPIT_MUSTER.search(zeile):
                if "scratchpad-ok" in zeile:
                    ausgenommen += 1
                else:
                    treffer.append(f"{rel}:{nr}")
    return treffer, ausgenommen


def ungeschuetzte_unterrepos(pfad):
    """Direkte Unterverzeichnisse mit eigenem .git, im Elternrepo unversioniert."""
    gefunden = []
    for eintrag in sorted(os.scandir(os.path.realpath(pfad)), key=lambda e: e.name):
        if not eintrag.is_dir() or not os.path.exists(os.path.join(eintrag.path, ".git")):
            continue
        if git(pfad, "check-ignore", "-q", "--", eintrag.name).returncode == 0:
            continue  # per .gitignore ausgeschlossen
        status = git(pfad, "status", "--porcelain", "--", eintrag.name).stdout
        if any(z.startswith("??") for z in status.splitlines()):
            gefunden.append(eintrag.name + "/")
    return gefunden


def offene_arbeitskopien(pfad):
    """Weitere Arbeitskopien aus `git worktree list --porcelain`, ohne die Hauptkopie.

    Je Eintrag "<pfad> (<zweig>)"; leer ohne weitere Kopien oder wenn die
    Liste nicht lesbar ist. Nur lesend (INTERFACES.md §9.3).
    """
    r = git(pfad, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        return []
    eintraege = []
    akt_pfad, akt_zweig = None, None

    def ablegen():
        if akt_pfad is not None:
            eintraege.append((akt_pfad, akt_zweig or "?"))

    for zeile in r.stdout.splitlines():
        if zeile.startswith("worktree "):
            if akt_pfad is not None:
                ablegen()
            akt_pfad = zeile[len("worktree "):].strip()
            akt_zweig = None
        elif zeile.startswith("branch "):
            ref = zeile[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                akt_zweig = ref[len("refs/heads/"):]
            else:
                akt_zweig = ref
        elif zeile.strip() in ("detached", "bare"):
            akt_zweig = zeile.strip()
    if akt_pfad is not None:
        ablegen()
    if len(eintraege) <= 1:
        return []
    return [f"{p} ({z})" for p, z in eintraege[1:]]


def kuerze(liste, grenze=10):
    if len(liste) <= grenze:
        return ", ".join(liste)
    return ", ".join(liste[:grenze]) + f" … und {len(liste) - grenze} weitere"


def pruefe_repo(pfad, seit, repos):
    """Ein Repo prüfen -> (anzeige_pfad, [(kuerzel, text)], [info_zeilen])."""
    befunde, infos = [], []
    echt = os.path.realpath(pfad)
    anzeige = os.path.abspath(pfad)

    if not os.path.isdir(pfad):
        return anzeige, [("KEIN_REPO", f"Pfad existiert nicht: {anzeige}")], infos
    if git(pfad, "rev-parse", "--git-dir").returncode != 0:
        return anzeige, [("KEIN_REPO", f"kein Git-Repo: {anzeige}")], infos

    wurzel = oberste_ebene(pfad)
    if wurzel != echt:
        befunde.append(("FREMDES_REPO",
                        f"{anzeige} liegt in fremdem Repo (oben: {wurzel})"))

    zeilen = [z for z in git(pfad, "status", "--porcelain").stdout.splitlines() if z.strip()]
    if zeilen:
        befunde.append(("UNCOMMITTET",
                        f"{len(zeilen)} ungesicherte Änderung(en) "
                        f"(git status --porcelain nicht leer)"))

    offene = offene_arbeitskopien(pfad)
    if offene:
        befunde.append(("OFFENE_ARBEITSKOPIE",
                        f"{len(offene)} offene Arbeitskopie(n): {kuerze(offene)} "
                        f"(git worktree list zeigt weitere Einträge neben der Hauptkopie)"))

    eintrag = repos.get(echt, {})
    if eintrag.get("remote_erwartet", False):
        if not git(pfad, "remote").stdout.split():
            befunde.append(("REMOTE_FEHLT",
                            "kein Remote eingetragen, aber Remote erwartet (repos.json)"))
        elif not hat_upstream(pfad):
            befunde.append(("NICHT_GESICHERT",
                            "kein Upstream eingerichtet (Vergleich mit zuletzt "
                            "bekanntem Remote-Stand, ohne fetch)"))
        else:
            r = git(pfad, "rev-list", "--count", "@{u}..HEAD")
            try:
                anzahl = int(r.stdout.strip())
            except ValueError:
                befunde.append(("NICHT_GESICHERT",
                                f"Vorsprung gegen Upstream nicht zählbar "
                                f"({r.stderr.strip()}; verglichen ohne fetch)"))
                anzahl = 0
            if r.returncode == 0 and anzahl > 0:
                befunde.append(("NICHT_GESICHERT",
                                f"{anzahl} Commit(s) nicht gesichert (noch nicht im "
                                f"zuletzt bekannten Remote-Stand; verglichen ohne fetch)"))

    trailer = trailer_commits(pfad, seit)
    if trailer:
        befunde.append(("FREMDER_TRAILER",
                        f"Co-Authored-By in {len(trailer)} Commit(s): {kuerze(trailer)}"))

    treffer, ausgenommen = scratchpad_treffer(pfad, wurzel)
    if treffer:
        befunde.append(("SCRATCHPAD_VERWEIS",
                        f"{len(treffer)} Verweis(e) auf /tmp-Skripte: {kuerze(treffer)} "
                        f"(Zeilen mit scratchpad-ok ausgenommen: {ausgenommen})"))
    elif ausgenommen:
        infos.append(f"{ausgenommen} /tmp-Verweis(e) mit scratchpad-ok ausgenommen "
                     f"(kein Befund)")

    if eintrag.get("elternrepo", False):
        for name in ungeschuetzte_unterrepos(pfad):
            befunde.append(("UNGESCHUETZTES_UNTERREPO",
                            f"ungeschütztes Unterrepo: {name} (eigenes .git, "
                            f"im Elternrepo unversioniert)"))

    rang = {k: i for i, k in enumerate(KUERZEL_ORDNUNG)}
    befunde.sort(key=lambda b: rang.get(b[0], 99))
    return anzeige, befunde, infos


def pruefe_regeln():
    """Globale Regeldateien einmal je Aufruf prüfen (§13.10) -> [(kuerzel, text)].

    In-Process über tools/regeln.py (oben per importlib geladen, Logik wird
    benutzt, nicht kopiert): das Erzeugnis aus der Standardquelle gegen die
    installierte Datei unter regeln.heimat() (REGELN_HOME beachtet) — für
    claude und codex, nur lesend. Befund REGELN_DRIFT, wenn die installierte
    Datei fehlt, ein Symlink ist oder bytegenau abweicht. Ist die Quelle nicht
    erzeugbar (regeln.Aufruffehler, z. B. Ersetzungstabelle kaputt), ein
    Befund REGELN_DRIFT mit dieser Meldung — kein Absturz, kein Exit 2.
    """
    befunde = []
    for ziel in sorted(regeln.ZIELE):
        try:
            ziel_pfad = regeln.ziel_pfad_fuer(ziel)   # wirft bei leerem REGELN_HOME (§13.8)
            erzeugt = regeln.erzeuge_bytes(regeln.STANDARD_QUELLE, ziel)
        except regeln.Aufruffehler as e:
            return [("REGELN_DRIFT", str(e))]
        if ziel_pfad.is_symlink():
            befunde.append(("REGELN_DRIFT",
                            f"{ziel_pfad} ist ein Symlink — {HINWEIS_REGELN}"))
            continue
        if not ziel_pfad.is_file():
            befunde.append(("REGELN_DRIFT", f"{ziel_pfad} fehlt — {HINWEIS_REGELN}"))
            continue
        try:
            installiert = regeln.lies_bytes(ziel_pfad)
        except regeln.Aufruffehler as e:
            befunde.append(("REGELN_DRIFT", f"{e} — {HINWEIS_REGELN}"))
            continue
        if installiert == erzeugt:
            continue
        nr = regeln.erste_abweichende_zeile(
            installiert.decode("utf-8", "replace"),
            erzeugt.decode("utf-8", "replace"))
        zeile = f", erste abweichende Zeile: {nr}" if nr else ""
        befunde.append(("REGELN_DRIFT", f"{ziel_pfad} weicht ab{zeile} — {HINWEIS_REGELN}"))
    return befunde


def sammle_pfade(args):
    per_alle = []
    if args.alle:
        try:
            tabelle = lade_repos()
        except Aufruffehler as e:
            print(f"abschluss_check: {e}", file=sys.stderr)
            raise
        # repos.json steuert die Reihenfolge nicht; sortiert für stabile Ausgabe
        per_alle = sorted(tabelle, key=lambda p: os.path.basename(p))
    gegebene = list(args.pfade) or ([] if args.alle else [os.getcwd()])
    gesehen, pfadliste = set(), []
    for p in per_alle + gegebene:
        schluessel = os.path.realpath(p) if os.path.exists(p) else os.path.abspath(p)
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            pfadliste.append(p)
    if args.alle:
        return pfadliste, tabelle
    try:
        return pfadliste, lade_repos()
    except Aufruffehler:
        return pfadliste, {}  # ohne --alle: fehlende Liste heißt „keine Remote-Erwartung“


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Abschluss-Check: meldet ungesicherte Arbeit und Fremd-Spuren je Repo.")
    parser.add_argument("pfade", nargs="*", metavar="PFAD",
                        help="zu prüfende Verzeichnisse (Vorgabe: aktuelles)")
    parser.add_argument("--alle", action="store_true",
                        help="alle Repos aus tools/repos.json prüfen")
    parser.add_argument("--seit", metavar="REV",
                        help="Trailer-Suche im Bereich REV..HEAD statt nur HEAD/Upstream")
    parser.add_argument("--json", action="store_true",
                        help="Ausgabe als JSON-Liste {pfad, befunde: [{kuerzel, text}]}")
    parser.add_argument("--ohne-regeln", action="store_true",
                        help="Regelblock ganz weglassen (für andere Rechner, §13.10)")
    args = parser.parse_args(argv)

    try:
        pfadliste, repos = sammle_pfade(args)
    except Aufruffehler as e:
        print(f"abschluss_check: {e}", file=sys.stderr)
        return 2

    try:
        ergebnisse = [pruefe_repo(p, args.seit, repos) for p in pfadliste]
    except Aufruffehler as e:
        print(f"abschluss_check: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("abschluss_check: git nicht gefunden", file=sys.stderr)
        return 2

    anzahl = sum(len(b) for _, b, _ in ergebnisse)
    regel_befunde = [] if args.ohne_regeln else pruefe_regeln()   # §13.10: einmal je Aufruf
    anzahl += len(regel_befunde)
    if args.json:
        elemente = [{"pfad": p, "befunde": [{"kuerzel": k, "text": t} for k, t in b]}
                    for p, b, _ in ergebnisse]
        if not args.ohne_regeln:
            elemente.append({"pfad": "regeln (global)",
                             "befunde": [{"kuerzel": k, "text": t}
                                         for k, t in regel_befunde]})
        print(json.dumps(elemente, ensure_ascii=False, indent=2))
    else:
        for p, b, infos in ergebnisse:
            print(f"Repo: {p}")
            if b:
                for k, t in b:
                    print(f"  {k}: {t}")
            else:
                print("  OK: keine Befunde")
            for info in infos:
                print(f"  Hinweis: {info}")
        if not args.ohne_regeln:
            print("Regeln (global):")
            if regel_befunde:
                for k, t in regel_befunde:
                    print(f"  {k}: {t}")
            else:
                print("  OK: installiert = Quelle")
        print(f"Summe: {len(ergebnisse)} Repo(s), {anzahl} Befund(e)")
    return 1 if anzahl else 0


if __name__ == "__main__":
    sys.exit(main())

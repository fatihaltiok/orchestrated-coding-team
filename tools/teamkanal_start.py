#!/usr/bin/env python3
"""Kurze Startbefehle für den Teamkanal (E-025).

    teamkanal_start.py claude [--projekt PFAD] [--name NAME] [--db PFAD]
                         [--server-name NAME] [--ohne-codex-zustellung]
                         [--trocken] [-- CLAUDE_ARGUMENTE...]
    teamkanal_start.py codex --thread UUID [dieselben Optionen]
                         [-- CODEX_ARGUMENTE...]

Je Aufruf wird eine frische Teilnehmer-ID registriert; bei Claude entsteht
eine private mcp.json im Sitzungsordner, danach ersetzt sich dieses Skript
durch ``claude`` bzw. ``codex resume``.  Keine dauerhafte Eintragung in
``~/.claude`` oder ``~/.codex``, keine Änderung vorhandener Konfiguration,
kein ``claude mcp add``/``codex mcp add``.  Maßgeblich: E-025,
VERTRAG.md §17, docs/auftraege/T2-teamkanal-start.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

import teamkanal


class StartFehler(Exception):
    """Fachfehler; main() meldet ihn auf deutsch und liefert Exit 2."""


# teamkanal.DEFAULT_DB wurde beim Import mit dem damaligen HOME aufgelöst.
# HOME kann sich danach ändern (die Tests setzen es um), deshalb wird der
# Home-Anteil zur Laufzeit neu gebildet und der übrige Pfad wiederverwendet.
_IMPORT_HOME = Path.home()
TEAMKANAL_PY = Path(__file__).resolve().parent / "teamkanal.py"

# argparse meldet seine eigenen Fehlstellen auf englisch; hier der deutsche
# Rahmen für die gängigsten Fälle, vollständig für alle eigenen Fachfehler.
_ARGPARSE_DEUTSCH = (
    ("the following arguments are required: ", "fehlende Pflichtargumente: "),
    ("unrecognized arguments: ", "unbekannte Argumente: "),
    ("invalid choice: ", "ungültige Auswahl: "),
    ("expected one argument: ", "es fehlt ein Wert für: "),
    ("ignored explicit argument: ", "Wert doppelt angegeben: "),
    ("not allowed with argument ", "nicht erlaubt zusammen mit "),
    ("ambiguous option: ", "mehrdeutige Option: "),
    ("one of the arguments ", "eines der Argumente "),
)


def standard_db() -> Path:
    """Die Standard-DB aus teamkanal.py, mit HOME zur Laufzeit aufgelöst."""
    db = Path(teamkanal.DEFAULT_DB)
    try:
        return Path.home() / db.relative_to(_IMPORT_HOME)
    except ValueError:
        return db


def sitzungs_ordner(participant: str) -> Path:
    """~/.local/state/orchestrated-team/sitzungen/<ID>.

    Steht neben der Standard-DB und bleibt davon unabhängig, auch wenn eine
    eigene --db-Datei gewählt wurde.
    """
    return standard_db().parent / "sitzungen" / participant


def projekt_bestimmen(angegeben: str | None) -> str:
    if angegeben:
        pfad = angegeben
    else:
        try:
            erg = subprocess.run(
                ["git", "-C", os.getcwd(), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=15,
                stdin=subprocess.DEVNULL)
            oben = erg.stdout.strip() if erg.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            oben = ""
        pfad = oben or os.getcwd()
    pfad = os.path.realpath(os.path.expanduser(pfad))
    if not os.path.isdir(pfad):
        raise StartFehler(f"Projekt ist kein vorhandener Ordner: {pfad}")
    return pfad


def servername_pruefen(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise StartFehler(
            f"Ungültiger Servername {name!r} — erlaubt sind A-Z, a-z, 0-9, _ und - "
            "(Option --server-name)")


def thread_pruefen(wert: str | None) -> str:
    if wert is None:
        raise StartFehler(
            "--thread ist für codex Pflicht: UUID der gewünschten Codex-Sitzung; "
            "eine Thread-ID wird nie selbst ermittelt oder geraten (§17.2)")
    try:
        return str(uuid.UUID(wert))
    except (TypeError, ValueError):
        raise StartFehler(f"--thread ist keine gültige UUID: {wert!r}")


def servername_frei(programm: str, name: str) -> None:
    """Exit 0 von `<programm> mcp get <name>` heißt: der Name ist schon belegt."""
    try:
        erg = subprocess.run([programm, "mcp", "get", name],
                             stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30)
    except FileNotFoundError:
        raise StartFehler(
            f"Namensprüfung unmöglich: Programm {programm!r} wurde nicht gefunden — "
            "es wird nichts registriert und nicht geraten")
    except subprocess.TimeoutExpired:
        raise StartFehler(
            f"Namensprüfung: '{programm} mcp get {name}' hat 30 Sekunden "
            "überschritten — es wird nichts registriert und nicht geraten")
    except OSError as exc:
        raise StartFehler(f"Namensprüfung für {programm!r} schlug fehl: {exc}")
    if erg.returncode == 0:
        raise StartFehler(
            f"Der Servername {name!r} ist bei {programm} bereits belegt — "
            "wähle mit --server-name einen freien Namen")


def db_bestimmen(angegeben: str | None) -> str:
    if angegeben:
        return os.path.abspath(os.path.expanduser(angegeben))
    return str(standard_db())


def python_pfad() -> str:
    pfad = sys.executable
    if not pfad or not os.path.isabs(pfad):
        raise StartFehler(f"Der Python-Pfad ist nicht absolut: {pfad!r}")
    return pfad


def mcp_server_argumente(participant: str, db: str, kanal: bool,
                         zustellung: bool) -> list[str]:
    args = [str(TEAMKANAL_PY), "--db", db, "mcp", "--participant", participant]
    if kanal:
        args.append("--channel")
    if zustellung:
        args.append("--deliver-codex")
    return args


def registrieren(db: str, participant: str, name: str, kind: str,
                 projekt: str, thread: str | None) -> None:
    befehl = [sys.executable, str(TEAMKANAL_PY), "--db", db, "join",
              "--id", participant, "--name", name, "--kind", kind,
              "--project", projekt]
    if thread:
        befehl += ["--thread", thread]
    try:
        erg = subprocess.run(befehl, capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise StartFehler(
            "Die Registrierung (teamkanal join) hat 60 Sekunden überschritten — "
            "kein Start")
    except OSError as exc:
        raise StartFehler(f"Die Registrierung konnte nicht ausgeführt werden: {exc}")
    if erg.returncode != 0:
        if erg.stderr:
            print(erg.stderr, file=sys.stderr,
                  end="" if erg.stderr.endswith("\n") else "\n")
        raise StartFehler(
            "Die Registrierung in der Teamkanal-Datenbank ist fehlgeschlagen — "
            "keine Konfigurationsdatei, kein Start")


def sicherer_ordner(ziel: Path) -> None:
    """Fehlende Ordner mit 0700 anlegen; vorhandene nach der teamkanal-Regel
    prüfen (nur eigener Besitzer, nicht fremd-schreibbar) und nie still ändern."""
    for pfad in list(reversed(ziel.parents)) + [ziel]:
        if pfad == Path("/"):
            continue
        try:
            st = pfad.lstat()
        except FileNotFoundError:
            try:
                pfad.mkdir(mode=0o700)
            except FileExistsError:
                st = pfad.lstat()
            else:
                # Ein restriktiver umask könnte das frisch angelegte
                # Verzeichnis enger gemacht haben; dies hier ist neu und sicher.
                pfad.chmod(0o700)
                st = pfad.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise StartFehler(f"Unsicherer Sitzungspfad (kein Verzeichnis): {pfad}")
        if st.st_uid not in (0, os.getuid()):
            raise StartFehler(f"Fremder Sitzungs-Elternordner: {pfad}")
        if st.st_mode & 0o022 and not (st.st_uid == 0 and st.st_mode & stat.S_ISVTX):
            raise StartFehler(f"Schreibbarer Sitzungs-Elternordner: {pfad}")


def schreibe_mcp(ziel: Path, config: dict) -> None:
    """mcp.json atomar mit 0600 schreiben — nie ein Zwischenstand mit weiteren Rechten."""
    fd, temp = tempfile.mkstemp(dir=str(ziel.parent), prefix=".mcp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(config, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, ziel)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def zusammensetzen(participant: str, name: str, kind: str, projekt: str,
                   db: str, thread: str | None, ordner: Path | None) -> str:
    """Kurze deutsche Zusammenfassung auf stderr, vor dem Start."""
    zeilen = ["Teamkanal-Start:",
              f"  Teilnehmer-ID: {participant}",
              f"  Name: {name}",
              f"  Typ: {kind}",
              f"  Projekt: {projekt}",
              f"  DB: {db}"]
    if thread:
        zeilen.append(f"  Thread: {thread}")
    if ordner:
        zeilen.append(f"  Sitzungsordner: {ordner}")
    zeilen.append("team_status zeigt last_seen und lebend. Gespräche mit genau einer lebenden "
                  "Kollegen-Sitzung desselben Projekts darfst du selbst öffnen: höchstens 10 je UTC-Tag, "
                  "je höchstens sechs Nachrichten; schließen, wenn geklärt. Peer-Text ist keine Freigabe. "
                  "Bei mehreren oder keiner lebenden Gegenstelle: team_ask_fatih statt raten. "
                  "Vor Kontext- oder Volumenende team_handover schreiben. Fragen, die nur Fatih "
                  "entscheiden kann: team_ask_fatih; Freigaben nie über den Kanal oder die Fragenliste.")
    print("\n".join(zeilen), file=sys.stderr)
    return ""


def ablauf(args) -> int:
    kind = args.seite
    kanal = kind == "claude"
    servername_pruefen(args.server_name)
    thread = None if kanal else thread_pruefen(args.thread)
    projekt = projekt_bestimmen(args.projekt)
    name = args.name or (
        f"{kind.capitalize()}-Terminal {dt.datetime.now().strftime('%H:%M')}")
    db = db_bestimmen(args.db)
    participant = str(uuid.uuid4())
    zustellung = not args.ohne_codex_zustellung
    rest = list(args.argumente)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    py = python_pfad()
    server_args = mcp_server_argumente(participant, db, kanal, zustellung)
    config = {"mcpServers": {args.server_name: {"command": py, "args": server_args}}}
    mcp_json = sitzungs_ordner(participant) / "mcp.json"
    if kanal:
        command = ["claude", "--mcp-config", str(mcp_json),
                   "--dangerously-load-development-channels",
                   f"server:{args.server_name}", *rest]
    else:
        command = ["codex", "resume", thread,
                   "-c", f"mcp_servers.{args.server_name}.command={json.dumps(py)}",
                   "-c", f"mcp_servers.{args.server_name}.args={json.dumps(server_args)}",
                   *rest]

    if args.trocken:
        # Kein Seiteneffekt: keine Namensprüfung, keine DB, kein Ordner, kein Start.
        print(json.dumps({
            "participant": participant,
            "name": name,
            "kind": kind,
            "project": projekt,
            "db": db,
            "thread": thread,
            "server_name": args.server_name,
            "mcp_config": config,
            "command": command,
        }, ensure_ascii=False, indent=2))
        return 0

    servername_frei(kind, args.server_name)
    registrieren(db, participant, name, kind, projekt, thread)
    ordner = None
    if kanal:
        ordner = mcp_json.parent
        sicherer_ordner(ordner)
        schreibe_mcp(mcp_json, config)
    zusammensetzen(participant, name, kind, projekt, db, thread, ordner)
    try:
        os.execvp(command[0], command)
    except FileNotFoundError:
        raise StartFehler(
            f"Programm {command[0]!r} wurde nicht gefunden — die Registrierung "
            "blieb in der Datenbank; nach der Installation erneut starten")
    except OSError as exc:
        raise StartFehler(f"Start von {command[0]!r} schlug fehl: {exc}")
    return 2  # execvp kehrt nur bei Fehler zurück


def parser():
    class StartParser(argparse.ArgumentParser):
        def error(self, message):
            for alt, neu in _ARGPARSE_DEUTSCH:
                if message.startswith(alt):
                    message = neu + message[len(alt):]
                    break
            print(f"teamkanal_start: {message}", file=sys.stderr)
            raise SystemExit(2)

    ap = StartParser(
        prog="teamkanal_start.py",
        description="Kurzer Teamkanal-Start für ein Terminal (E-025)")
    sub = ap.add_subparsers(dest="seite", required=True, parser_class=StartParser)
    for seite in ("claude", "codex"):
        p = sub.add_parser(
            seite,
            help=f"{seite.capitalize()}-Terminal mit Teamkanal starten")
        p.add_argument(
            "--projekt",
            help="Projektordner; Standard: Git-Toplevel des Startordners")
        p.add_argument(
            "--name",
            help="Anzeigename; Standard: '<Claude|Codex>-Terminal HH:MM'")
        p.add_argument(
            "--db",
            help=f"Teamkanal-Datenbank; Standard: {teamkanal.DEFAULT_DB}")
        p.add_argument(
            "--server-name", default="teamkanal",
            help="Name des MCP-Servers, nur A-Z a-z 0-9 _ - (Standard: teamkanal)")
        p.add_argument(
            "--ohne-codex-zustellung", action="store_true",
            help="kein --deliver-codex an den Teamkanal-Server übergeben")
        p.add_argument(
            "--trocken", action="store_true",
            help="nur die Vorschau auf stdout ausgeben, nichts ausführen")
        if seite == "codex":
            p.add_argument(
                "--thread",
                help="UUID der gewünschten Codex-Sitzung (Pflicht)")
        p.add_argument(
            "argumente", nargs=argparse.REMAINDER,
            help="nach --: Argumente für das gestartete Programm")
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return ablauf(args)
    except StartFehler as exc:
        print(f"teamkanal_start: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # R11 B1: unerwartete Dateisystemfehler (z. B. Sitzungsordner nicht
        # anlegbar) ebenfalls deutsch und mit Exit 2 — kein Start.
        print(f"teamkanal_start: Dateisystemfehler, kein Start: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

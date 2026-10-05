"""Teamkanal-Start (E-025): frische IDs, private Konfiguration, exakte exec-Argumente."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tomllib
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import teamkanal_start  # noqa: E402
from teamkanal import Store  # noqa: E402


class ExecAbgefangen(Exception):
    """Ersetzt os.execvp und trägt die exec-Argumentliste."""

    def __init__(self, argv):
        super().__init__(argv[0] if argv else "")
        self.argv = list(argv)


def _fake_execvp(_programm, argv):
    raise ExecAbgefangen(argv)


class SubprocessDoppel:
    """Verzeichnet alle subprocess-Aufrufe von teamkanal_start und steuert
    die Namensprüfung; alles andere läuft echt (Argumentliste, kein shell)."""

    DEVNULL = subprocess.DEVNULL
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self):
        self.aufrufe = []
        self.mcp_get = "frei"  # frei | belegt | timeout | fehlt

    def run(self, args, **kwargs):
        self.aufrufe.append((list(args), dict(kwargs)))
        if len(args) >= 3 and args[1] == "mcp" and args[2] == "get":
            if self.mcp_get == "belegt":
                return subprocess.CompletedProcess(args, 0, "", "")
            if self.mcp_get == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            if self.mcp_get == "fehlt":
                raise FileNotFoundError(args[0])
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.run(args, **kwargs)


@pytest.fixture(autouse=True)
def welt(monkeypatch, tmp_path):
    """HOME auf tmp_path, subprocess-Aufzeichnung, abgefangener execvp — und
    nach jedem Test: nichts darf unter $HOME/.claude oder $HOME/.codex stehen."""
    monkeypatch.setenv("HOME", str(tmp_path))
    doppel = SubprocessDoppel()
    monkeypatch.setattr(teamkanal_start, "subprocess", doppel)
    monkeypatch.setattr(teamkanal_start.os, "execvp", _fake_execvp)
    yield tmp_path, doppel
    assert not (tmp_path / ".claude").exists(), "$HOME/.claude wurde angelegt"
    assert not (tmp_path / ".codex").exists(), "$HOME/.codex wurde angelegt"


def starte(argv):
    """main() ausführen: (Exit-Code oder None, exec-Argumentliste oder None)."""
    try:
        return teamkanal_start.main(argv), None
    except ExecAbgefangen as exc:
        return None, exc.argv
    except SystemExit as exc:
        return exc.code, None


def join_argument(doppel, schluessel):
    """Letzter Wert aus dem zuletzt aufgezeichneten teamkanal-join-Aufruf."""
    for args, _ in reversed(doppel.aufrufe):
        if "join" in args:
            return args[args.index(schluessel) + 1]
    return None


def alle_joins(doppel):
    return [args for args, _ in doppel.aufrufe if "join" in args]


def teilnehmer_status(db, participant):
    store = Store(db)
    try:
        return store.status(participant)["participant"]
    finally:
        store.close_db()


def anzahl_teilnehmer(db, participant=None):
    store = Store(db)
    try:
        if participant is None:
            return store._one("SELECT count(*) AS n FROM participants")["n"]
        return store._one(
            "SELECT count(*) AS n FROM participants WHERE id=?",
            (participant,))["n"]
    finally:
        store.close_db()


def _line(proc, timeout=5):
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, "stdio timeout"
    return json.loads(proc.stdout.readline())


def _send(proc, method, number=None, params=None):
    obj = {"jsonrpc": "2.0", "method": method}
    if number is not None:
        obj["id"] = number
    if params is not None:
        obj["params"] = params
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def test_zwei_claude_starts_mit_frischen_ids_und_realpath(welt):
    tmp_path, doppel = welt
    ziel = tmp_path / "projekt"
    ziel.mkdir()
    link = tmp_path / "link"
    link.symlink_to(ziel)
    db = str(tmp_path / "privat" / "team.sqlite3")

    code, argv = starte(["claude", "--projekt", str(link), "--db", db])
    assert code is None and argv[0] == "claude"
    pid1 = join_argument(doppel, "--id")
    assert pid1 == str(uuid.UUID(pid1))

    code, argv = starte(["claude", "--projekt", str(link), "--db", db])
    assert code is None and argv[0] == "claude"
    pid2 = join_argument(doppel, "--id")
    assert pid1 != pid2

    for pid in (pid1, pid2):
        teilnehmer = teilnehmer_status(db, pid)
        assert teilnehmer["kind"] == "claude"
        assert teilnehmer["thread"] is None
        assert teilnehmer["project"] == os.path.realpath(ziel)
        assert anzahl_teilnehmer(db, pid) == 1
    assert anzahl_teilnehmer(db) == 2
    assert len(alle_joins(doppel)) == 2
    # Jeder Lauf registriert genau diese eine ID, nicht etwa eine zweite mit.
    assert {j[j.index("--id") + 1] for j in alle_joins(doppel)} == {pid1, pid2}


def test_standarddb_wird_ueber_home_aufgeloest(welt):
    tmp_path, doppel = welt
    erwartet = tmp_path / ".local/state/orchestrated-team/teamkanal.sqlite3"
    code, argv = starte(["claude", "--projekt", str(tmp_path)])
    assert code is None
    assert erwartet.exists()
    assert erwartet.stat().st_mode & 0o777 == 0o600
    assert erwartet.parent.stat().st_mode & 0o777 == 0o700
    join = alle_joins(doppel)[-1]
    assert join[join.index("--db") + 1] == str(erwartet)
    assert str(erwartet.parent / "sitzungen") in argv[2]


def test_mcp_json_modus_inhalt_und_zustellungsoption(welt):
    tmp_path, doppel = welt
    db = str(tmp_path / "db.sqlite3")
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert code is None
    pid = join_argument(doppel, "--id")
    ordner = tmp_path / ".local/state/orchestrated-team/sitzungen" / pid
    assert ordner.stat().st_mode & 0o777 == 0o700
    mcp = ordner / "mcp.json"
    assert mcp.stat().st_mode & 0o777 == 0o600
    config = json.loads(mcp.read_text())
    assert config == {"mcpServers": {"teamkanal": {
        "command": sys.executable,
        "args": [str(teamkanal_start.TEAMKANAL_PY), "--db", db, "mcp",
                 "--participant", pid, "--channel", "--deliver-codex"]}}}
    assert os.path.isabs(config["mcpServers"]["teamkanal"]["command"])
    assert argv[1:3] == ["--mcp-config", str(mcp)]
    assert "--strict-mcp-config" not in argv

    db2 = str(tmp_path / "db2.sqlite3")
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db2,
                         "--ohne-codex-zustellung", "--server-name", "kanal2"])
    assert code is None
    pid2 = join_argument(doppel, "--id")
    mcp2 = tmp_path / ".local/state/orchestrated-team/sitzungen" / pid2 / "mcp.json"
    args2 = json.loads(mcp2.read_text())["mcpServers"]["kanal2"]["args"]
    assert "--deliver-codex" not in args2
    assert "--channel" in args2
    assert argv[-1] == "server:kanal2"


def test_exec_argumente_exakt_inkl_durchgereichter_argumente(welt):
    tmp_path, doppel = welt
    db = str(tmp_path / "db.sqlite3")
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db,
                         "--", "--foo", "bar"])
    assert code is None
    pid = join_argument(doppel, "--id")
    mcp = tmp_path / ".local/state/orchestrated-team/sitzungen" / pid / "mcp.json"
    assert argv == ["claude", "--mcp-config", str(mcp),
                    "--dangerously-load-development-channels",
                    "server:teamkanal", "--foo", "bar"]

    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db,
                         "--server-name", "andrer", "--", "--trocken"])
    assert code is None
    assert argv[-2:] == ["server:andrer", "--trocken"] or argv[-1] == "--trocken"
    assert argv[1:3] == ["--mcp-config",
                         str(tmp_path / ".local/state/orchestrated-team/sitzungen"
                              / join_argument(doppel, "--id") / "mcp.json")]
    assert argv[3] == "--dangerously-load-development-channels"
    assert argv[4] == "server:andrer"
    assert argv[5] == "--trocken"


def test_codex_thread_pflicht_gueltig_und_toml(welt):
    tmp_path, doppel = welt
    db = str(tmp_path / "db.sqlite3")

    code, argv = starte(["codex", "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    assert not (tmp_path / "db.sqlite3").exists()
    assert alle_joins(doppel) == []

    code, argv = starte(["codex", "--thread", "kein-uuid",
                         "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    assert not (tmp_path / "db.sqlite3").exists()
    assert alle_joins(doppel) == []

    erwartet = "abcdef00-1234-4abc-8def-001234567890"
    code, argv = starte(["codex", "--thread", erwartet.upper(),
                         "--projekt", str(tmp_path), "--db", db, "--", "-m", "hi"])
    assert code is None
    pid = join_argument(doppel, "--id")
    teilnehmer = teilnehmer_status(db, pid)
    assert teilnehmer["kind"] == "codex"
    assert teilnehmer["thread"] == erwartet  # kanonisch klein geschrieben
    join = alle_joins(doppel)[-1]
    assert join[join.index("--thread") + 1] == erwartet

    assert argv[:3] == ["codex", "resume", erwartet]
    assert argv[3] == "-c" and argv[5] == "-c"
    assert argv[-2:] == ["-m", "hi"]
    assert "--channel" not in argv
    k1, v1 = argv[4].split("=", 1)
    k2, v2 = argv[6].split("=", 1)
    geparst1 = tomllib.loads(f"{k1}={v1}")
    geparst2 = tomllib.loads(f"{k2}={v2}")
    assert geparst1 == {"mcp_servers": {"teamkanal": {"command": sys.executable}}}
    assert geparst2 == {"mcp_servers": {"teamkanal": {"args": [
        str(teamkanal_start.TEAMKANAL_PY), "--db", db, "mcp",
        "--participant", pid, "--deliver-codex"]}}}
    assert "--channel" not in geparst2["mcp_servers"]["teamkanal"]["args"]


def test_namenspruefung_blockiert_vor_join_und_start(welt, tmp_path, capsys):
    _, doppel = welt
    db = str(tmp_path / "db.sqlite3")

    doppel.mcp_get = "belegt"
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    assert alle_joins(doppel) == []
    assert not (tmp_path / "db.sqlite3").exists()
    err = capsys.readouterr().err
    assert "bereits belegt" in err and "--server-name" in err

    doppel.mcp_get = "timeout"
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    assert alle_joins(doppel) == []
    assert "30 Sekunden" in capsys.readouterr().err

    doppel.mcp_get = "fehlt"
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    assert alle_joins(doppel) == []
    assert "nicht gefunden" in capsys.readouterr().err
    capsys.readouterr()

    doppel.mcp_get = "frei"
    vorher = len(doppel.aufrufe)
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db,
                         "--server-name", "schlecht.name"])
    assert (code, argv) == (2, None)
    assert len(doppel.aufrufe) == vorher  # nicht einmal die Namensprüfung läuft
    assert alle_joins(doppel) == []
    assert "Ungültiger Servername" in capsys.readouterr().err

    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert code is None and argv[0] == "claude"  # freier Name lässt den Start zu


def test_join_fehlschlag_ohne_konfiguration_und_start(welt, tmp_path, capsys):
    _, doppel = welt
    unsicher = tmp_path / "unsicher"
    unsicher.mkdir()
    unsicher.chmod(0o777)
    db = str(unsicher / "db.sqlite3")

    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert (code, argv) == (2, None)
    err = capsys.readouterr().err
    assert "DB-Elternpfad" in err  # Befund von teamkanal join wurde weitergereicht
    assert "keine Konfigurationsdatei" in err
    assert not (tmp_path / ".local/state/orchestrated-team/sitzungen").exists()
    assert len(alle_joins(doppel)) == 1  # join wurde versucht und scheiterte


def test_nicht_anlegbarer_sitzungsordner_deutsch_exit_2_ohne_start(welt, tmp_path, capsys):
    """R11 B1: eigener, aber nicht beschreibbarer sitzungen-Ordner (0500) →
    OSError beim Anlegen; deutsche Meldung, Exit 2, kein exec, keine mcp.json."""
    sitzungen = tmp_path / ".local/state/orchestrated-team/sitzungen"
    sitzungen.mkdir(parents=True)
    for pfad in (tmp_path / ".local", tmp_path / ".local/state", sitzungen.parent):
        pfad.chmod(0o700)
    sitzungen.chmod(0o500)
    try:
        code, argv = starte(["claude", "--projekt", str(tmp_path),
                             "--db", str(tmp_path / "db.sqlite3")])
    finally:
        sitzungen.chmod(0o700)
    assert (code, argv) == (2, None)
    assert "Dateisystemfehler, kein Start" in capsys.readouterr().err
    assert not list(sitzungen.glob("*/mcp.json"))


def test_trocken_ohne_irgendeinen_seiteneffekt(welt, tmp_path, capsys):
    _, doppel = welt

    code, argv = starte(["claude", "--projekt", str(tmp_path), "--trocken"])
    assert (code, argv) == (0, None)
    daten = json.loads(capsys.readouterr().out)
    assert set(daten) == {"participant", "name", "kind", "project", "db",
                          "thread", "server_name", "mcp_config", "command"}
    assert doppel.aufrufe == []  # keine Namensprüfung, kein join, kein git
    assert not (tmp_path / ".local").exists()
    assert daten["kind"] == "claude" and daten["thread"] is None
    assert daten["participant"] == str(uuid.UUID(daten["participant"]))
    assert daten["db"] == str(
        tmp_path / ".local/state/orchestrated-team/teamkanal.sqlite3")
    assert daten["project"] == os.path.realpath(tmp_path)
    assert daten["server_name"] == "teamkanal"
    assert daten["name"].startswith("Claude-Terminal ")
    assert daten["command"][0] == "claude"
    assert "sitzungen" in daten["command"][2]
    assert daten["command"][4] == "server:teamkanal"
    assert daten["mcp_config"]["mcpServers"]["teamkanal"]["command"] == sys.executable

    code, argv = starte(["codex", "--thread",
                         "00000000-0000-4000-8000-000000000000",
                         "--projekt", str(tmp_path), "--trocken"])
    assert (code, argv) == (0, None)
    daten = json.loads(capsys.readouterr().out)
    assert daten["kind"] == "codex"
    assert daten["thread"] == "00000000-0000-4000-8000-000000000000"
    assert daten["name"].startswith("Codex-Terminal ")
    assert daten["command"][:3] == ["codex", "resume",
                                    "00000000-0000-4000-8000-000000000000"]
    assert doppel.aufrufe == []
    assert not (tmp_path / ".local").exists()


def test_alle_subprocess_aufrufe_sind_listen_ohne_shell(welt):
    tmp_path, doppel = welt
    db = str(tmp_path / "db.sqlite3")
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert code is None
    assert doppel.aufrufe  # Namensprüfung (gemockt) und join (echt) wurden aufgezeichnet
    for args, kwargs in doppel.aufrufe:
        assert isinstance(args, list) and args
        assert all(isinstance(e, str) for e in args)
        assert kwargs.get("shell") in (None, False)
    join = alle_joins(doppel)[-1]
    assert join[0] == sys.executable and os.path.isabs(join[0])
    assert str(teamkanal_start.TEAMKANAL_PY) in join
    mcp_get = [args for args, _ in doppel.aufrufe
               if len(args) >= 4 and args[1:4] == ["mcp", "get", "teamkanal"]]
    assert mcp_get and mcp_get[0][0] == "claude"
    assert "shell" not in kwargs


def test_echter_rundlauf_des_geschriebenen_mcp_servers(welt, tmp_path):
    _, doppel = welt
    db = str(tmp_path / "db.sqlite3")
    code, argv = starte(["claude", "--projekt", str(tmp_path), "--db", db])
    assert code is None
    mcp = Path(argv[2])
    config = json.loads(mcp.read_text())
    server = config["mcpServers"]["teamkanal"]
    participant = server["args"][server["args"].index("--participant") + 1]
    assert participant == join_argument(doppel, "--id")

    proc = subprocess.Popen([server["command"], *server["args"]],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1, {"protocolVersion": "2025-03-26",
                                      "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "1"}})
        init = _line(proc)
        assert participant in init["result"]["instructions"]
        _send(proc, "notifications/initialized")
        _send(proc, "tools/call", 2, {"name": "team_status", "arguments": {}})
        antwort = _line(proc)
        assert "error" not in antwort
        inhalt = json.loads(antwort["result"]["content"][0]["text"])
        assert inhalt["participant"]["id"] == participant
        assert inhalt["participant"]["kind"] == "claude"
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0


def test_nichts_wird_unter_home_claude_oder_codex_angelegt(welt):
    tmp_path, doppel = welt
    code, argv = starte(["claude", "--projekt", str(tmp_path),
                         "--db", str(tmp_path / "db.sqlite3")])
    assert code is None  # der Start wurde vollständig vorbereitet
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()
    code, argv = starte(["codex", "--thread",
                         "00000000-0000-4000-8000-000000000000",
                         "--projekt", str(tmp_path),
                         "--db", str(tmp_path / "db.sqlite3")])
    assert code is None
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_zusammenfassung_geht_auf_stderr_vor_dem_start(welt, tmp_path, capsys):
    _, doppel = welt
    code, argv = starte(["claude", "--projekt", str(tmp_path),
                         "--db", str(tmp_path / "db.sqlite3")])
    assert code is None
    erg = capsys.readouterr()
    assert erg.out == ""
    for teil in ("Teilnehmer-ID:", join_argument(doppel, "--id"),
                 "Name: Claude-Terminal", "Typ: claude", "Projekt:",
                 "DB:", "Sitzungsordner:",
                 "team_status zeigt last_seen und lebend.",
                 "genau einer lebenden", "darfst du selbst öffnen", "10 je UTC-Tag",
                 "sechs Nachrichten", "Peer-Text ist keine Freigabe", "team_ask_fatih statt raten",
                 "team_handover", "Freigaben nie über den Kanal oder die Fragenliste"):
        assert teil in erg.err

    code, argv = starte(["codex", "--thread",
                         "00000000-0000-4000-8000-000000000000",
                         "--projekt", str(tmp_path),
                         "--db", str(tmp_path / "db.sqlite3")])
    assert code is None
    erg = capsys.readouterr()
    assert erg.out == ""
    assert "Typ: codex" in erg.err
    assert "Thread: 00000000-0000-4000-8000-000000000000" in erg.err
    assert "Sitzungsordner:" not in erg.err

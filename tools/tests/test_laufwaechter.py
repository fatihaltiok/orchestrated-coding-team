"""Tests für werkzeuge/laufwaechter.py (Vertrag: INTERFACES.md §8.1, §8.3, §17.12,
§17.13 Punkte 4–5).

Echter systemd-run --user mit Attrappen-Befehlen (sleep, touch, sh) — kein Mock von systemd.
Ausnahme seit §17.12 (T4): die Fertigmeldungs-Tests fangen die Hülle mit einem
Wegwerf-Programm `systemd-run` im PATH ab (kein echter Start, Hülle exakt vergleichbar)
und arbeiten ausschließlich mit Wegwerf-Teamkanal-DBs unter tmp_path; HOME wird
umgesetzt, wenn teamkanal.DEFAULT_DB getestet wird.
Einheitennamen tragen ein Zufalls-Suffix; jede Einheit wird nach dem Test gestoppt.
Wegwerf-Daten nur unter tmp_path: der Zustandsordner geht über LAUFWAECHTER_ZUSTAND dorthin,
der Prüftakt über LAUFWAECHTER_TAKT. Je Exit-Code aus dem Vertrag (0, 2, 3, 4, 5, 6, 7)
gibt es mindestens einen Test, der ihn auslöst.
"""
import json
import os
import pathlib
import random
import re
import shlex
import string
import subprocess
import sys
import time
import uuid

import pytest

WURZEL = pathlib.Path(__file__).resolve().parents[2]
SKRIPT = WURZEL / "tools" / "laufwaechter.py"
TAKT = "0.5"  # Sekunden zwischen zwei Prüfungen (Vorgabe 10, im Vertrag für Tests kürzer)

sys.path.insert(0, str(WURZEL / "tools"))
import teamkanal  # noqa: E402


def aufruf(*args):
    return subprocess.run([sys.executable, str(SKRIPT), *args],
                          capture_output=True, text=True, timeout=180)


def einheit_aktiv(einheit):
    r = subprocess.run(["systemctl", "--user", "is-active", einheit],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


@pytest.fixture
def werk(tmp_path, monkeypatch):
    """Zustand und Takt auf Wegwerf-Daten; räumt die Einheit des Laufs hinterher ab."""
    monkeypatch.setenv("LAUFWAECHTER_ZUSTAND", str(tmp_path / "zustand"))
    monkeypatch.setenv("LAUFWAECHTER_TAKT", TAKT)
    name = "lwpruef" + "".join(random.choices(string.ascii_lowercase, k=8))
    ordner = tmp_path / "arbeit"
    ordner.mkdir()
    bündel = {
        "name": name,
        "einheit": "lw-" + name,
        "ordner": ordner,
        "marke": ordner / "fertig.marke",
        "log": ordner / "lauf.log",
        "zustand": tmp_path / "zustand" / (name + ".json"),
    }
    yield bündel
    subprocess.run(["systemctl", "--user", "stop", bündel["einheit"]], capture_output=True)
    subprocess.run(["systemctl", "--user", "reset-failed", bündel["einheit"]],
                   capture_output=True)


def starte(b, befehl):
    """befehl: Liste, wie sie nach -- an laufwaechter.py start gehen würde."""
    return aufruf("start", b["name"], "--ordner", str(b["ordner"]),
                  "--marke", str(b["marke"]), "--log", str(b["log"]),
                  "--", *befehl)


def sh(zeile):
    return ["/bin/sh", "-c", zeile]


def warte_auf_bedingung(bedingung, grenz_sekunden=20):
    ende = time.monotonic() + grenz_sekunden
    while time.monotonic() < ende:
        if bedingung():
            return
        time.sleep(0.2)
    pytest.fail("Bedingung nach 20 s nicht erfüllt")


# --- der saubere Fall: start und warte mit Exit 0 --------------------------------

def test_relative_pfade_in_fehlendem_unterordner_exit0(werk):
    """Befund Handlungsprüfung 19.09.: fehlt docs/auftraege beim Start, scheiterte die
    Log-Umleitung der Hülle, bevor der Befehl lief (Exit 4). So steht es in der Regel-Beispielzeile."""
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", "docs/auftraege/x-FERTIG", "--log", "docs/auftraege/x-lauf.log",
               "--", *sh("sleep 1; touch docs/auftraege/x-FERTIG"))
    assert r.returncode == 0, r.stderr
    r = aufruf("warte", werk["name"], "--max-sekunden", "60")
    assert r.returncode == 0, r.stdout + r.stderr
    log = werk["ordner"] / "docs" / "auftraege" / "x-lauf.log"
    warte_auf_bedingung(lambda: log.exists() and "LAUFWAECHTER-EXIT 0" in log.read_text("utf-8"))


def test_start_und_warte_fertig_exit0(werk):
    """Exit 0: FERTIG, sobald die Marke da ist; relative marke/log gelten ab --ordner."""
    zeile = f"sleep 1; touch {shlex.quote(str(werk['marke']))}"
    r = starte(werk, sh(zeile))
    assert r.returncode == 0, r.stderr
    # relative Pfade wurden gegen --ordner aufgelöst und im Zustand absolut gespeichert
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert z["einheit"] == werk["einheit"]
    assert z["ordner"] == str(werk["ordner"])
    assert z["marke"] == str(werk["marke"])
    assert z["log"] == str(werk["log"])
    assert z["befehl"] == sh(zeile)
    assert "T" in z["gestartet"]  # ISO-Zeit

    r = aufruf("warte", werk["name"], "--max-sekunden", "60")
    assert r.returncode == 0, r.stderr
    assert "FERTIG" in r.stdout
    # die Hülle hängt den Exit des Befehls ans Log
    warte_auf_bedingung(lambda: "LAUFWAECHTER-EXIT 0" in werk["log"].read_text("utf-8"))


def test_warte_fertig_mit_hinweis_exit0(werk):
    """Exit 0 mit Hinweis, falls die Einheit neben der Marke noch läuft."""
    r = starte(werk, sh(f"touch {shlex.quote(str(werk['marke']))}; sleep 30"))
    assert r.returncode == 0, r.stderr
    r = aufruf("warte", werk["name"], "--max-sekunden", "30")
    assert r.returncode == 0, r.stderr
    assert "FERTIG" in r.stdout
    assert "läuft noch" in r.stdout  # sleep 30 hält die Einheit, warte kehrt trotzdem heim
    assert einheit_aktiv(werk["einheit"])


# --- Exit 2: unbekannter Name, unbrauchbarer Aufruf -------------------------------

def test_warte_unbekannt_exit2(werk):
    r = aufruf("warte", "gibtsnicht" + werk["name"])
    assert r.returncode == 2
    assert "Unbekannter Lauf" in r.stderr


def test_status_unbekannt_exit2(werk):
    r = aufruf("status", "gibtsnicht" + werk["name"])
    assert r.returncode == 2
    assert "Unbekannter Lauf" in r.stderr


# --- Exit 3: LÄUFT NOCH ----------------------------------------------------------

def test_laeuft_noch_exit3(werk):
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 0, r.stderr
    r = aufruf("warte", werk["name"], "--max-sekunden", "2")
    assert r.returncode == 3
    assert "LÄUFT NOCH" in r.stdout
    assert "warte" in r.stdout  # Hinweis auf erneutes Aufrufen


# --- Exit 4: ABGEBROCHEN ---------------------------------------------------------

def test_abgebrochen_exit4(werk):
    r = starte(werk, sh("echo hallo-vom-lauf; exit 3"))
    assert r.returncode == 0, r.stderr
    r = aufruf("warte", werk["name"], "--max-sekunden", "60")
    assert r.returncode == 4
    assert "ABGEBROCHEN" in r.stdout
    assert "hallo-vom-lauf" in r.stdout          # die letzten 5 Logzeilen kommen mit
    assert "LAUFWAECHTER-EXIT 3" in r.stdout


def test_abgebrochen_ohne_eigene_logzeilen_exit4(werk):
    """Abbruch mit stummer Attrappe; auch dann steht der Exit der Hülle im Log."""
    r = starte(werk, sh("exit 7"))
    assert r.returncode == 0, r.stderr
    r = aufruf("warte", werk["name"], "--max-sekunden", "60")
    assert r.returncode == 4
    assert "LAUFWAECHTER-EXIT 7" in r.stdout


# --- Exit 5: Doppelstart ---------------------------------------------------------

def test_doppelstart_exit5(werk):
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 0, r.stderr
    assert einheit_aktiv(werk["einheit"])
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 5
    assert "Doppelstart" in r.stderr


# --- Exit 6: Marke existiert schon ----------------------------------------------

def test_marke_existiert_exit6(werk):
    werk["marke"].write_text("schon fertig", encoding="utf-8")
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 6
    assert "Marke" in r.stderr and "existiert schon" in r.stderr


# --- Exit 7: HÄNGT VERMUTLICH -----------------------------------------------------

def test_haengt_vermutlich_exit7(werk):
    """Keine Dateiänderung seit --stille-sekunden: Meldung, Einheit bleibt an."""
    alt = werk["ordner"].joinpath("alt.txt")
    alt.write_text("seit langem still", encoding="utf-8")
    vergangen = time.time() - 300
    os.utime(alt, (vergangen, vergangen))
    r = starte(werk, ["sleep", "60"])  # der Lauf selbst schreibt nichts unter --ordner
    assert r.returncode == 0, r.stderr
    # das Log frischt den Stillstand beim Start auf — erst die folgende Ruhe zählt
    r = aufruf("warte", werk["name"], "--max-sekunden", "60", "--stille-sekunden", "2")
    assert r.returncode == 7
    assert "HÄNGT VERMUTLICH" in r.stdout
    assert einheit_aktiv(werk["einheit"])  # der Laufwächter beendet die Einheit nicht


# --- start ersetzt beendeten Zustand; status zeigt ihn ---------------------------

def test_start_ersetzt_beendeten_zustand(werk):
    alt = {"name": werk["name"], "einheit": werk["einheit"], "ordner": str(werk["ordner"]),
           "marke": str(werk["marke"]), "log": str(werk["log"]),
           "befehl": ["sleep", "42"],
           "gestartet": "2026-09-18T10:00:00+02:00"}
    werk["zustand"].parent.mkdir(parents=True, exist_ok=True)
    werk["zustand"].write_text(json.dumps(alt), encoding="utf-8")
    r = starte(werk, ["sleep", "1"])
    assert r.returncode == 0, r.stderr
    assert "ersetze" in r.stdout
    neu = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert neu["befehl"] == ["sleep", "1"] and neu["gestartet"] != alt["gestartet"]


def test_status_zeigt_zustand_exit0(werk):
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 0, r.stderr
    r = aufruf("status", werk["name"])
    assert r.returncode == 0, r.stderr
    assert werk["einheit"] in r.stdout
    assert "aktiv" in r.stdout
    assert "fehlt" in r.stdout  # Marke noch nicht da
    assert "sleep 60" in r.stdout


# --- sonstige Aufruffehler (Exit 2) ----------------------------------------------

def test_start_ohne_befehl_exit2(werk):
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]))
    assert r.returncode == 2


def test_start_mit_kaputter_env_exit2(werk):
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--env", "OHNEGLEICHZEICHEN", "--", "/bin/true")
    assert r.returncode == 2


def test_start_mit_fehlendem_ordner_exit2(werk):
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"] / "fehlt"),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]), "--", "/bin/true")
    assert r.returncode == 2


# --- Exit 1: systemd-run selbst schlägt fehl --------------------------------------

def test_start_mit_verweigertem_einheitenname_exit1(werk):
    werk["name"] = "a" * 250           # Einheitenname zu lang, systemd lehnt ab
    werk["einheit"] = "lw-" + werk["name"]
    r = starte(werk, ["/bin/true"])
    assert r.returncode == 1
    assert "systemd-run fehlgeschlagen" in r.stderr
    assert not werk["zustand"].exists()


# --- Quelltext-Regel: nie Prozessnamen (Vertrag §8.1) -----------------------------

def test_quelltext_nennt_keine_prozessnamen():
    quelle = SKRIPT.read_text(encoding="utf-8")
    for wort in ("pgrep", "pidof"):
        assert wort not in quelle
    assert not re.search(r"\bps\b", quelle)


# --- §15.1: Warnung KONTEXT_FEHLT, wenn das Kontextpaket fehlt ---------------------

WARNUNG_A = ("WARNUNG KONTEXT_FEHLT: docs/auftraege/x-kontext.md fehlt — das Repo hat "
             "eine KONTEXT.json (E-018). Bauen: kontextpaket bau . --auftrag-datei "
             "docs/auftraege/x.md > docs/auftraege/x-kontext.md")
WARNUNG_E = ("WARNUNG KONTEXT_FEHLT: docs/auftraege/x-kontext.md fehlt (liegt vor, ist "
             "aber nicht committet) — das Repo hat eine KONTEXT.json (E-018). Bauen: "
             "kontextpaket bau . --auftrag-datei docs/auftraege/x.md > "
             "docs/auftraege/x-kontext.md")

IDENTITAET = {
    "GIT_AUTHOR_NAME": "Pruefstand",
    "GIT_AUTHOR_EMAIL": "pruefstand@example.com",
    "GIT_COMMITTER_NAME": "Pruefstand",
    "GIT_COMMITTER_EMAIL": "pruefstand@example.com",
}


def git(repo, *args):
    """Git im Wegwerf-Repo; die Identität kommt aus der Umgebung, nie global."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env=dict(os.environ, **IDENTITAET))
    assert r.returncode == 0, f"git {' '.join(args)} scheiterte: {r.stderr}"
    return r.stdout


def repo_mit_kontext(pfad):
    """Wegwerf-Git-Repo mit committeter KONTEXT.json (für die Arbeitskopie-Fälle)."""
    pfad.mkdir()
    git(pfad, "init", "-q", "-b", "main")
    (pfad / "KONTEXT.json").write_text("{}\n", encoding="utf-8")
    git(pfad, "add", "-A")
    git(pfad, "commit", "-q", "-m", "Anfangsstand")
    return pfad


def starte_marke_fertig(werk, ordner, *extra):
    """start mit Marke docs/auftraege/x-FERTIG — der Auftrags-Form aus §15.1."""
    return aufruf("start", werk["name"], "--ordner", str(ordner), *extra,
                  "--marke", "docs/auftraege/x-FERTIG",
                  "--log", "docs/auftraege/x.log", "--", "sleep", "60")


def test_kontextwarnung_paket_fehlt_warnung_start_laeuft(werk):
    """§15.1 (a): KONTEXT.json da, Marke endet auf -FERTIG, Paket fehlt → eine
    Warnungszeile auf stderr im Wortlaut des Vertrags, der Start läuft trotzdem."""
    (werk["ordner"] / "KONTEXT.json").write_text("{}\n", encoding="utf-8")
    r = starte_marke_fertig(werk, werk["ordner"])
    assert r.returncode == 0, r.stderr
    assert WARNUNG_A in r.stderr.splitlines()
    assert werk["zustand"].exists()  # der Lauf wurde gestartet, nichts abgebrochen


def test_kontextwarnung_paket_da_keine_warnung(werk):
    """§15.1 (b): die Paket-Datei liegt vor → keine Warnung."""
    (werk["ordner"] / "KONTEXT.json").write_text("{}\n", encoding="utf-8")
    paket = werk["ordner"] / "docs" / "auftraege" / "x-kontext.md"
    paket.parent.mkdir(parents=True)
    paket.write_text("# Paket\n", encoding="utf-8")
    r = starte_marke_fertig(werk, werk["ordner"])
    assert r.returncode == 0, r.stderr
    assert "KONTEXT_FEHLT" not in r.stderr


def test_kontextwarnung_ohne_kontext_json_keine_warnung(werk):
    """§15.1 (c): ohne KONTEXT.json wird gar nicht erst geprüft → keine Warnung."""
    r = starte_marke_fertig(werk, werk["ordner"])
    assert r.returncode == 0, r.stderr
    assert "KONTEXT_FEHLT" not in r.stderr


def test_kontextwarnung_marke_ohne_fertig_keine_warnung(werk):
    """§15.1 (d): KONTEXT.json da, aber die Marke endet nicht auf -FERTIG → keine
    Prüfung, keine Warnung."""
    (werk["ordner"] / "KONTEXT.json").write_text("{}\n", encoding="utf-8")
    r = starte(werk, ["sleep", "60"])  # Marke fertig.marke — kein -FERTIG-Endstück
    assert r.returncode == 0, r.stderr
    assert "KONTEXT_FEHLT" not in r.stderr


def test_kontextwarnung_arbeitskopie_nur_uncommittet(werk, tmp_path, monkeypatch):
    """§15.1 (e): --arbeitskopie, das Paket liegt nur uncommittet vor → Warnung mit
    dem Zusatz '(liegt vor, ist aber nicht committet)', der Start läuft trotzdem."""
    monkeypatch.setenv("LAUFWAECHTER_ARBEITSKOPIEN", str(tmp_path / "kopien"))
    repo = repo_mit_kontext(tmp_path / "repo")
    paket = repo / "docs" / "auftraege" / "x-kontext.md"
    paket.parent.mkdir(parents=True)
    paket.write_text("noch nicht committet\n", encoding="utf-8")
    r = starte_marke_fertig(werk, repo, "--arbeitskopie", "--trotz-uncommittet")
    assert r.returncode == 0, r.stderr
    assert WARNUNG_E in r.stderr.splitlines()
    assert werk["zustand"].exists()


def test_kontextwarnung_arbeitskopie_paket_committet(werk, tmp_path, monkeypatch):
    """§15.1 (f): --arbeitskopie, das Paket steht in HEAD des Hauptrepos → keine
    Warnung (nur Committetes kommt in die Arbeitskopie — hier ist es committet)."""
    monkeypatch.setenv("LAUFWAECHTER_ARBEITSKOPIEN", str(tmp_path / "kopien"))
    repo = repo_mit_kontext(tmp_path / "repo")
    paket = repo / "docs" / "auftraege" / "x-kontext.md"
    paket.parent.mkdir(parents=True)
    paket.write_text("committet\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Paket")
    r = starte_marke_fertig(werk, repo, "--arbeitskopie")
    assert r.returncode == 0, r.stderr
    assert "KONTEXT_FEHLT" not in r.stderr


def test_kontextwarnung_arbeitskopie_paket_fehlt_ganz(werk, tmp_path, monkeypatch):
    """§15.1 Punkt 3, erster Fall: --arbeitskopie, das Paket fehlt ganz → Warnung
    ohne den Uncommittet-Zusatz."""
    monkeypatch.setenv("LAUFWAECHTER_ARBEITSKOPIEN", str(tmp_path / "kopien"))
    repo = repo_mit_kontext(tmp_path / "repo")
    r = starte_marke_fertig(werk, repo, "--arbeitskopie")
    assert r.returncode == 0, r.stderr
    assert WARNUNG_A in r.stderr.splitlines()


# --- §17.12: Fertigmeldung über den Teamkanal (T4) --------------------------------

# Hülle OHNE --melde-an, hart kodiert für das feste Beispiel befehl=["/bin/true"]:
# exakt der String, den starte_einheit vor dieser Änderung erzeugt (Pflichttest 1).
HUELLE_SCHABLONE = (
    "/bin/true </dev/null >{log} 2>&1; "
    "printf 'LAUFWAECHTER-EXIT %s\\n' \"$?\" >>{log}")


def fake_programm(tmp_path, monkeypatch, name, exit_code=0):
    """NAME (systemd-run, codex) als Wegwerf-Programm vor den Systempfad legen;
    jedes Argument wird abgelegt, davor eine Trennzeile ---; exit_code ist der
    Rückgabewert (0 = Erfolg, 1 = z. B. kaputte Codex-Queue, §17.13 Punkt 5)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    aufrufe = tmp_path / f"{name}-aufrufe.txt"
    skript = bindir / name
    skript.write_text("#!/bin/sh\n"
                      f"printf '%s\\n' --- >> {shlex.quote(str(aufrufe))}\n"
                      f"printf '%s\\n' \"$@\" >> {shlex.quote(str(aufrufe))}\n"
                      f"exit {exit_code}\n", encoding="utf-8")
    skript.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return aufrufe


def huelle_aus(aufrufe):
    """Die letzte Zeile der aufgezeichneten Argumente ist der sh -c-Text der Hülle."""
    return aufrufe.read_text(encoding="utf-8").splitlines()[-1]


def teamkanal_db(tmp_path, kind="claude", thread=None):
    """Wegwerf-Teamkanal-DB unter tmp_path mit genau einem Teilnehmer."""
    projekt = tmp_path / "projekt"
    projekt.mkdir(exist_ok=True)
    db = tmp_path / "teamkanal" / "wegwerf.sqlite3"
    store = teamkanal.Store(db)
    tid = str(uuid.uuid4())
    store.register(tid, "Prüfer", kind, str(projekt), thread)
    store.close_db()
    return db, tid


def melde_ausfuehren(werk, empf, db, stdout=None, lauf=None):
    """melde so aufrufen wie die Hülle; stdout=None bedeutet abfangen, sonst ins Log
    umleiten (so schreibt die Hülle es ins Lauf-Log). lauf=None stellt den Aufruf von
    Hand ohne --lauf (§17.12) dar, sonst die Hülle eines start mit --melde-an."""
    argumente = [sys.executable, str(SKRIPT), "melde",
                 "--name", werk["name"], "--marke", str(werk["marke"]),
                 "--log", str(werk["log"]), "--an", empf, "--db", str(db)]
    if lauf is not None:
        argumente += ["--lauf", lauf]
    if stdout is None:
        return subprocess.run(argumente, capture_output=True, text=True, timeout=60)
    with open(stdout, "ab") as huelle_log:
        return subprocess.run(argumente, stdout=huelle_log,
                              stderr=subprocess.STDOUT, timeout=60)


def melde_log_schreiben(werk, exit_zahl=None):
    """Lauf-Log im Zustand nach dem Laufende, die EXIT-Zeile der Hülle inklusive."""
    zeilen = ["vom Lauf geschrieben"]
    if exit_zahl is not None:
        zeilen.append(f"LAUFWAECHTER-EXIT {exit_zahl}")
    werk["log"].write_text("\n".join(zeilen) + "\n", encoding="utf-8")


def start_mit_melde_pruefung(werk, tmp_path, monkeypatch, db, empf):
    """start mit --melde-an gegen eine Fake-systemd-run; Rückgabe (Ergebnis, Argumente)."""
    aufrufe = fake_programm(tmp_path, monkeypatch, "systemd-run")
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--melde-an", empf, "--teamkanal-db", str(db), "--", "/bin/true")
    return r, aufrufe


def pruefung_fehlgeschlagen(r, werk, aufrufe, *meldeteile):
    """Exit 2 der Vorprüfung: deutsche Meldung, aber keinerlei Seiteneffekt."""
    assert r.returncode == 2, r.stdout + r.stderr
    for teil in meldeteile:
        assert teil in r.stderr
    assert not werk["zustand"].exists()   # kein Zustand
    assert not aufrufe.exists()           # kein systemd-run


def test_huelle_ohne_melde_option_ist_bytegleich(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 1: ohne --melde-an bleibt die Hülle byte-gleich zum Stand vor T4 —
    hart kodiert für das feste Beispiel /bin/true, und der Zustand trägt die beiden
    melde-Schlüssel nicht."""
    aufrufe = fake_programm(tmp_path, monkeypatch, "systemd-run")
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--", "/bin/true")
    assert r.returncode == 0, r.stderr
    erwartet = HUELLE_SCHABLONE.format(log=shlex.quote(str(werk["log"])))
    assert huelle_aus(aufrufe) == erwartet
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert "melde_an" not in z and "teamkanal_db" not in z


def test_huelle_mit_melde_option_haengt_melde_an(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 3 + §17.13 Punkt 4: mit --melde-an steht der melde-Aufruf NACH
    der EXIT-Zeile, shlex.quote gequotet, Pfade absolut, sys.executable statt
    python3, und die Hülle enthält --lauf mit der im Zustand gespeicherten
    Lauf-ID."""
    aufrufe = fake_programm(tmp_path, monkeypatch, "systemd-run")
    db, empf = teamkanal_db(tmp_path)
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--melde-an", empf, "--teamkanal-db", str(db), "--", "/bin/true")
    assert r.returncode == 0, r.stderr
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    lauf = z["melde_lauf"]
    assert str(uuid.UUID(lauf)) == lauf  # §17.13 Punkt 4: uuid4 im Zustand
    huelle = huelle_aus(aufrufe)
    erwartet = (HUELLE_SCHABLONE.format(log=shlex.quote(str(werk["log"])))
                + f"; {shlex.quote(sys.executable)} {shlex.quote(str(SKRIPT.resolve()))} melde"
                + f" --name {shlex.quote(werk['name'])}"
                + f" --marke {shlex.quote(str(werk['marke']))}"
                + f" --log {shlex.quote(str(werk['log']))}"
                + f" --an {shlex.quote(empf)} --db {shlex.quote(str(db))}"
                + f" --lauf {shlex.quote(lauf)}"
                + f" >>{shlex.quote(str(werk['log']))} 2>&1")
    assert huelle == erwartet
    assert huelle.index("LAUFWAECHTER-EXIT") < huelle.index(" melde ")


def test_melde_pruefung_ungueltige_uuid_exit2(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 2 (a): ungültige UUID → Exit 2 vor jedem Seiteneffekt."""
    db, _ = teamkanal_db(tmp_path)
    r, aufrufe = start_mit_melde_pruefung(werk, tmp_path, monkeypatch, db, "keine-uuid")
    pruefung_fehlgeschlagen(r, werk, aufrufe, "ungültige UUID")


def test_melde_pruefung_unbekannter_teilnehmer_exit2(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 2 (b): gültige, aber nicht registrierte UUID → Exit 2."""
    db, _ = teamkanal_db(tmp_path)
    r, aufrufe = start_mit_melde_pruefung(werk, tmp_path, monkeypatch, db,
                                          str(uuid.uuid4()))
    pruefung_fehlgeschlagen(r, werk, aufrufe, "Teilnehmer unbekannt")


def test_melde_pruefung_fehlende_db_exit2(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 2 (c): DB fehlt → Exit 2, und sie wird dabei NICHT neu angelegt."""
    db = tmp_path / "teamkanal" / "gibt-es-nicht.sqlite3"
    r, aufrufe = start_mit_melde_pruefung(werk, tmp_path, monkeypatch, db,
                                          str(uuid.uuid4()))
    pruefung_fehlgeschlagen(r, werk, aufrufe, "existiert nicht")
    assert not db.exists()


def test_melde_pruefung_manual_teilnehmer_exit2(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 2 (d): Teilnehmer vom Typ manual → Exit 2; empfangsberechtigt
    sind nur claude und codex."""
    db, manual = teamkanal_db(tmp_path, kind="manual")
    r, aufrufe = start_mit_melde_pruefung(werk, tmp_path, monkeypatch, db, manual)
    pruefung_fehlgeschlagen(r, werk, aufrufe, "manual")


def test_melde_fertig_eine_pending_nachricht_mit_pflichtsaetzen(werk, tmp_path):
    """§17.12 Punkt 3/5: Marke vorhanden → Status fertig, Exit-Zahl aus dem letzten
    LAUFWAECHTER-EXIT, alle Pflichtsätze, genau eine pending-Nachricht beim
    Claude-Empfänger, genau eine Ausgabezeile."""
    db, empf = teamkanal_db(tmp_path)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    r = melde_ausfuehren(werk, empf, db)
    assert r.returncode == 0, r.stderr
    assert re.fullmatch(r"LAUFWAECHTER-MELDUNG gesendet [0-9a-f-]{36}",
                        r.stdout.strip()), r.stdout
    store = teamkanal.Store(db)
    nachrichten = store.pending(empf)
    store.close_db()
    assert len(nachrichten) == 1
    nachricht = nachrichten[0]
    assert nachricht["kind"] == "note"
    assert "Status: fertig" in nachricht["body"]
    assert "Letzter Exit: 0" in nachricht["body"]
    assert f"Nächster Schritt: laufwaechter warte {werk['name']}" in nachricht["body"]
    assert ("Meldung des Laufwächters, keine Nutzerfreigabe; maßgeblich bleibt "
            "laufwaechter warte.") in nachricht["body"]


def test_melde_abgebrochen_exit_zahl_aus_dem_log(werk, tmp_path):
    """§17.12 Punkt 3: keine Marke → abgebrochen, mit der Zahl der letzten
    LAUFWAECHTER-EXIT-Zeile im Log."""
    db, empf = teamkanal_db(tmp_path)
    melde_log_schreiben(werk, 3)
    r = melde_ausfuehren(werk, empf, db)
    assert r.returncode == 0, r.stderr
    store = teamkanal.Store(db)
    [nachricht] = store.pending(empf)
    store.close_db()
    assert "Status: abgebrochen" in nachricht["body"]
    assert "Letzter Exit: 3" in nachricht["body"]
    assert f"Nächster Schritt: laufwaechter warte {werk['name']}" in nachricht["body"]


def test_melde_ohne_log_unbekannte_exit_zahl(werk, tmp_path):
    """Ohne Log (oder ohne EXIT-Zeile) steht 'unbekannt' statt einer Zahl."""
    db, empf = teamkanal_db(tmp_path)
    r = melde_ausfuehren(werk, empf, db)
    assert r.returncode == 0, r.stderr
    store = teamkanal.Store(db)
    [nachricht] = store.pending(empf)
    store.close_db()
    assert "Letzter Exit: unbekannt" in nachricht["body"]


def test_melde_zweimal_gibt_genau_eine_nachricht(werk, tmp_path):
    """§17.12 Punkt 4/5: zweites melde mit gleichem Status erzeugt keine zweite
    Nachricht — das offene Gespräch „Laufwächter: NAME“ wird wiederverwendet."""
    db, empf = teamkanal_db(tmp_path)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    r1 = melde_ausfuehren(werk, empf, db)
    r2 = melde_ausfuehren(werk, empf, db)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert r1.stdout == r2.stdout  # dieselbe message-id
    store = teamkanal.Store(db)
    assert len(store.pending(empf)) == 1
    gespraeche = [c for c in store.status(empf)["conversations"]
                  if c["topic"] == f"Laufwächter: {werk['name']}"]
    assert len(gespraeche) == 1
    assert len(store.read(empf, gespraeche[0]["id"])["messages"]) == 1
    store.close_db()


def test_melde_codex_zustellung_genau_einmal(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 5: Codex-Empfänger mit Thread → deliver_codex ruft die
    Fake-queue genau einmal auf, auch über zwei melde-Läufe hinweg."""
    aufrufe = fake_programm(tmp_path, monkeypatch, "codex")
    thread = str(uuid.uuid4())
    db, empf = teamkanal_db(tmp_path, kind="codex", thread=thread)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    r1 = melde_ausfuehren(werk, empf, db)
    r2 = melde_ausfuehren(werk, empf, db)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    aufruf_text = aufrufe.read_text(encoding="utf-8")
    assert aufruf_text.splitlines().count("---") == 1  # genau ein queue-Aufruf
    assert "queue" in aufruf_text
    assert thread in aufruf_text


def test_melde_db_kaputt_fehlgeschlagen_marke_unberuehrt(werk, tmp_path):
    """§17.12 Punkt 5/7: DB nicht lesbar (Verzeichnis) oder Pfad ungültig (Datei als
    Elternpfad) → genau eine Logzeile „fehlgeschlagen“, Exit 1, Marke unverändert."""
    kaputt = tmp_path / "ist-ein-verzeichnis"
    kaputt.mkdir()
    datei = tmp_path / "ist-eine-datei"
    datei.write_text("nichts\n", encoding="utf-8")
    werk["marke"].write_text("nicht angetastet", encoding="utf-8")
    for db in (kaputt, datei / "teamkanal.sqlite3"):
        r = melde_ausfuehren(werk, str(uuid.uuid4()), db, stdout=werk["log"])
        assert r.returncode == 1, r.stdout + r.stderr
    log = werk["log"].read_text(encoding="utf-8")
    assert log.count("LAUFWAECHTER-MELDUNG fehlgeschlagen:") == 2
    assert werk["marke"].read_text(encoding="utf-8") == "nicht angetastet"


def test_start_melde_an_speichert_zustand_und_huelle(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 1/7 + §17.13 Punkt 4 (Pflichttest): --melde-an landet im
    gespeicherten Zustand, die übergebene Hülle enthält den melde-Aufruf — und der
    Zustand speichert melde_lauf, dessen Wert die Hülle als --lauf mitgibt."""
    aufrufe = fake_programm(tmp_path, monkeypatch, "systemd-run")
    db, empf = teamkanal_db(tmp_path)
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--melde-an", empf, "--teamkanal-db", str(db), "--", "/bin/true")
    assert r.returncode == 0, r.stderr
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert z["melde_an"] == empf
    assert z["teamkanal_db"] == str(db)
    assert str(uuid.UUID(z["melde_lauf"])) == z["melde_lauf"]  # uuid4 je start
    huelle = huelle_aus(aufrufe)
    assert " melde " in huelle
    assert f"--an {shlex.quote(empf)}" in huelle
    assert f"--db {shlex.quote(str(db))}" in huelle
    assert f"--lauf {shlex.quote(z['melde_lauf'])}" in huelle


def test_start_melde_an_arbeitskopie(werk, tmp_path, monkeypatch):
    """§17.12 Punkt 1: --melde-an gilt auch zusammen mit --arbeitskopie."""
    monkeypatch.setenv("LAUFWAECHTER_ARBEITSKOPIEN", str(tmp_path / "kopien"))
    aufrufe = fake_programm(tmp_path, monkeypatch, "systemd-run")
    db, empf = teamkanal_db(tmp_path)
    repo = repo_mit_kontext(tmp_path / "repo")
    r = starte_marke_fertig(werk, repo, "--arbeitskopie",
                            "--melde-an", empf, "--teamkanal-db", str(db))
    assert r.returncode == 0, r.stderr
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert z["melde_an"] == empf and z["teamkanal_db"] == str(db)
    assert z["arbeitskopie"]
    huelle = huelle_aus(aufrufe)
    assert " melde " in huelle and empf in huelle


def test_start_melde_an_nutzt_default_db_ueber_home(werk, tmp_path, monkeypatch):
    """Ohne --teamkanal-db gilt teamkanal.DEFAULT_DB; ~ wird über HOME aufgelöst
    (hier: HOME auf tmp_path umgesetzt, die Default-DB dort angelegt)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    projekt = tmp_path / "projekt"
    projekt.mkdir(exist_ok=True)
    db = home / ".local" / "state" / "orchestrated-team" / "teamkanal.sqlite3"
    store = teamkanal.Store(db)
    empf = str(uuid.uuid4())
    store.register(empf, "Prüfer", "claude", str(projekt))
    store.close_db()
    fake_programm(tmp_path, monkeypatch, "systemd-run")
    r = aufruf("start", werk["name"], "--ordner", str(werk["ordner"]),
               "--marke", str(werk["marke"]), "--log", str(werk["log"]),
               "--melde-an", empf, "--", "/bin/true")
    assert r.returncode == 0, r.stderr
    z = json.loads(werk["zustand"].read_text(encoding="utf-8"))
    assert z["melde_an"] == empf
    assert z["teamkanal_db"] == str(db)
    assert str(uuid.UUID(z["melde_lauf"])) == z["melde_lauf"]


# --- §17.13 Punkte 4–5: Laufbezug und ehrliche Logzeile (T4n) -----------------------

def test_melde_zwei_laeufe_gleicher_name_zwei_gespraeche(werk, tmp_path):
    """§17.13 Punkt 4 (Pflichttest): zwei Läufe mit gleichem Namen und verschiedener
    Lauf-ID ergeben zwei Gespräche mit je einer Nachricht — auch dann, wenn der
    Empfänger die erste bestätigt und das Gespräch geschlossen hat."""
    db, empf = teamkanal_db(tmp_path)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    lauf1, lauf2 = str(uuid.uuid4()), str(uuid.uuid4())
    r1 = melde_ausfuehren(werk, empf, db, lauf=lauf1)
    assert r1.returncode == 0, r1.stderr
    store = teamkanal.Store(db)
    [nachricht1] = store.pending(empf)
    store.ack(empf, nachricht1["id"])          # Empfänger bestätigt …
    store.close(empf, nachricht1["conversation"])  # … und schließt das Gespräch
    store.close_db()
    r2 = melde_ausfuehren(werk, empf, db, lauf=lauf2)
    assert r2.returncode == 0, r2.stderr
    store = teamkanal.Store(db)
    gespraeche = {c["topic"]: c for c in store.status(empf)["conversations"]}
    store.close_db()
    thema1 = f"Laufwächter: {werk['name']} (Lauf {lauf1[:8]})"
    thema2 = f"Laufwächter: {werk['name']} (Lauf {lauf2[:8]})"
    assert thema1 in gespraeche and thema2 in gespraeche  # zwei Gespräche
    assert len(gespraeche) == 2
    store = teamkanal.Store(db)
    assert len(store.read(empf, gespraeche[thema1]["id"])["messages"]) == 1
    assert len(store.read(empf, gespraeche[thema2]["id"])["messages"]) == 1
    offen = store.pending(empf)  # die zweite Meldung geht neu raus, die erste ist bestätigt
    store.close_db()
    assert len(offen) == 1
    assert offen[0]["conversation"] == gespraeche[thema2]["id"]
    assert offen[0]["id"] != nachricht1["id"]


def test_melde_gleiche_lauf_id_zweimal_genau_eine_nachricht(werk, tmp_path):
    """§17.13 Punkt 4 (Pflichttest): dieselbe Lauf-ID zweimal → genau eine Nachricht
    in genau einem Gespräch (Idempotenz hängt an der Lauf-ID)."""
    db, empf = teamkanal_db(tmp_path)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    lauf = str(uuid.uuid4())
    r1 = melde_ausfuehren(werk, empf, db, lauf=lauf)
    r2 = melde_ausfuehren(werk, empf, db, lauf=lauf)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert r1.stdout == r2.stdout  # dieselbe message-id
    store = teamkanal.Store(db)
    assert len(store.pending(empf)) == 1
    thema = f"Laufwächter: {werk['name']} (Lauf {lauf[:8]})"
    gespraeche = [c for c in store.status(empf)["conversations"] if c["topic"] == thema]
    assert len(gespraeche) == 1
    assert len(store.read(empf, gespraeche[0]["id"])["messages"]) == 1
    store.close_db()


def test_melde_codex_queue_fehlgeschlagen_gespeichert_exit1(werk, tmp_path, monkeypatch):
    """§17.13 Punkt 5 (Pflichttest): Fake-codex mit Exit 1 → die Logzeile lautet
    „gespeichert <id>, Codex-Zustellung fehlgeschlagen: <Grund>“, Exit 1, und die
    Nachricht bleibt in der DB (delivery=queue_failed)."""
    fake_programm(tmp_path, monkeypatch, "codex", exit_code=1)
    thread = str(uuid.uuid4())
    db, empf = teamkanal_db(tmp_path, kind="codex", thread=thread)
    werk["marke"].write_text("fertig", encoding="utf-8")
    melde_log_schreiben(werk, 0)
    r = melde_ausfuehren(werk, empf, db)
    assert r.returncode == 1, r.stdout + r.stderr
    assert re.fullmatch(r"LAUFWAECHTER-MELDUNG gespeichert [0-9a-f-]{36}, "
                        r"Codex-Zustellung fehlgeschlagen: .+",
                        r.stdout.strip()), r.stdout
    store = teamkanal.Store(db)
    nachrichten = store.pending(empf)
    store.close_db()
    assert len(nachrichten) == 1  # die Nachricht ist gespeichert und abrufbar
    assert nachrichten[0]["delivery"] == "queue_failed"
    assert nachrichten[0]["delivery_error"]

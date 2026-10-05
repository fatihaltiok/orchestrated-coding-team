"""Tests für werkzeuge/arbeitskopie.py und den Arbeitskopie-Modus des Laufwächters
(Vertrag: INTERFACES.md §9.4 und §9.5; der Abschluss-Check-Teil von §9.3/§9.4 gehört
zu P4d).

Echter systemd-run --user mit Attrappen-Befehlen — kein Mock von systemd. Wegwerf-Repos
nur unter tmp_path, Arbeitskopien und Zustand ebenso (LAUFWAECHTER_ARBEITSKOPIEN,
LAUFWAECHTER_ZUSTAND, LAUFWAECHTER_TAKT). Die Git-Identität für die Wegwerf-Repos kommt
nur über die Umgebung — nie global. Je neuer Exit-Code aus §9.2/§9.4 (1, 2, 5, 8, 9, 10,
11, 12) gibt es mindestens einen Test, der ihn wirklich auslöst; dazu der saubere
Durchlauf und der Quelltext-Test mit Erlaubnisliste für die zwei dokumentierten
Aufräum-Stellen (§9.1 gescheiterter Start, §9.5 Punkt 7 nach der Übernahme). Zu jedem
Punkt aus §9.5 (Basis-Vorfahre, Kopie statt Verweis für Dateien, verlinkt zählt nie,
Ergebnis relativ zum Hauptzweig, unversioniert im Hauptrepo erlaubt, Nachprüfung nach
den Gates, Gewalt-Aufräumen nur für Gate-Reste) gibt es je einen auslösenden Test. Zu
§9.6 (fremde Verweise, Exit 13) je ein auslösender Test für alle sieben Fälle, dazu
direkte Prüfungen von Prüfung A und B.
"""
import ast
import json
import os
import pathlib
import random
import shlex
import string
import subprocess
import sys
import time

import pytest

WURZEL = pathlib.Path(__file__).resolve().parents[2]
LAUFWAECHTER = WURZEL / "tools" / "laufwaechter.py"
sys.path.insert(0, str(WURZEL / "tools"))
import arbeitskopie  # noqa: E402 — direkte Prüfungen der §9.6-Prüfungen A und B
TAKT = "0.5"  # Sekunden zwischen zwei Prüfungen (Vorgabe 10, im Vertrag für Tests kürzer)

IDENTITAET = {
    "GIT_AUTHOR_NAME": "Pruefstand",
    "GIT_AUTHOR_EMAIL": "pruefstand@example.com",
    "GIT_COMMITTER_NAME": "Pruefstand",
    "GIT_COMMITTER_EMAIL": "pruefstand@example.com",
}


def aufruf(*args):
    umgebung = dict(os.environ)
    umgebung.update(IDENTITAET)
    return subprocess.run([sys.executable, str(LAUFWAECHTER), *args],
                          capture_output=True, text=True, timeout=180, env=umgebung)


def git(repo, *args):
    """Git im Wegwerf-Repo; die Identität kommt aus der Umgebung, nie global."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env=dict(os.environ, **IDENTITAET))
    assert r.returncode == 0, f"git {' '.join(args)} scheiterte: {r.stderr}"
    return r.stdout


def sh(zeile):
    return ["/bin/sh", "-c", zeile]


def identitaet_als_env():
    """Die Identität für Commits, die der Agent selbst in der Einheit macht (--env)."""
    argumente = []
    for schluessel, wert in IDENTITAET.items():
        argumente += ["--env", f"{schluessel}={wert}"]
    return argumente


def einheit_aktiv(einheit):
    r = subprocess.run(["systemctl", "--user", "is-active", einheit],
                       capture_output=True, text=True)
    return r.stdout.strip() in ("active", "activating", "reloading")


def warte_auf_bedingung(bedingung, grenz_sekunden=20):
    ende = time.monotonic() + grenz_sekunden
    while time.monotonic() < ende:
        if bedingung():
            return
        time.sleep(0.2)
    pytest.fail("Bedingung nach 20 s nicht erfüllt")


@pytest.fixture
def werk(tmp_path, monkeypatch):
    """Wegwerf-Repo mit einem Anfangs-Commit; Zustand, Takt und Arbeitskopien nach
    tmp_path; räumt die Einheit des Laufs hinterher ab."""
    monkeypatch.setenv("LAUFWAECHTER_ZUSTAND", str(tmp_path / "zustand"))
    monkeypatch.setenv("LAUFWAECHTER_TAKT", TAKT)
    monkeypatch.setenv("LAUFWAECHTER_ARBEITSKOPIEN", str(tmp_path / "kopien"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "bestand.txt").write_text("zeile-eins\nzeile-zwei\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\ngeheim*\nlogs/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Anfangsstand")
    (repo / ".env").write_text("GEHEIM=42\n", encoding="utf-8")
    name = "lauf" + "".join(random.choices(string.ascii_lowercase, k=8))
    bündel = {
        "name": name,
        "einheit": "lw-" + name,
        "einheiten": ["lw-" + name],  # Tests mit weiteren Einheiten tragen sie hier nach
        "repo": repo,
        "kopie": tmp_path / "kopien" / name,
        "marke": tmp_path / "kopien" / name / "fertig.marke",
        "log": tmp_path / "kopien" / name / "lauf.log",
        "zustand": tmp_path / "zustand",
        "zweig": "lw/" + name,
    }
    yield bündel
    for einheit in bündel["einheiten"]:
        subprocess.run(["systemctl", "--user", "stop", einheit], capture_output=True)
        subprocess.run(["systemctl", "--user", "reset-failed", einheit],
                       capture_output=True)


def starte(b, befehl, *extra):
    """start --arbeitskopie; relative marke/log gelten ab der Arbeitskopie."""
    return aufruf("start", b["name"], "--ordner", str(b["repo"]), "--arbeitskopie",
                  "--marke", "fertig.marke", "--log", "lauf.log", *extra, "--", *befehl)


def warte_fertig(b, name=None):
    r = aufruf("warte", name or b["name"], "--max-sekunden", "60")
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def warte_bis_einheit_weg(b, name=None):
    warte_auf_bedingung(lambda: not einheit_aktiv("lw-" + (name or b["name"])))


def zustand(b, name=None):
    return json.loads((b["zustand"] / ((name or b["name"]) + ".json")).read_text("utf-8"))


def zweig_existiert(b, zweig=None):
    r = subprocess.run(["git", "-C", str(b["repo"]), "rev-parse", "--verify", "--quiet",
                        "refs/heads/" + (zweig or b["zweig"])], capture_output=True)
    return r.returncode == 0


def diff_datei(b, name=None):
    return b["zustand"] / ((name or b["name"]) + ".diff")


def mit_venv_ignore(repo):
    """Ordner .venv im Wegwerf-Repo anlegen und per .gitignore-Muster `.venv/`
    ignorieren (das Muster mit Schrägstrich, an dem sich der Symlink nachweislich
    stoß: er ist kein Ordner, git status zeigte `?? .venv`)."""
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    with open(repo / ".gitignore", "a", encoding="utf-8") as hand:
        hand.write(".venv/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", ".venv ignoriert")


# --- der saubere Durchlauf: starten, ansehen, übernehmen (§9.4) -------------------

def test_sauberer_durchlauf_start_ergebnis_uebernahme_exit0(werk):
    r = starte(werk, sh("echo inhalt > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    assert werk["kopie"].is_dir()
    assert werk["zweig"] in r.stdout and "Arbeitskopie" in r.stdout
    # Zustand: der Arbeitsordner der Einheit ist die Arbeitskopie; relative marke/log
    # wurden gegen die Arbeitskopie aufgelöst
    z = zustand(werk)
    assert z["ordner"] == str(werk["kopie"])
    assert z["arbeitskopie"] == str(werk["kopie"])
    assert z["marke"] == str(werk["marke"]) and z["log"] == str(werk["log"])
    assert z["repo"] == os.path.realpath(werk["repo"])
    assert z["hauptzweig"] == "main" and z["zweig"] == werk["zweig"]
    assert z["basis"] == git(werk["repo"], "rev-parse", "HEAD").strip()
    assert z["ergebnis_commit"] is None and z["abgeschlossen"] is None

    warte_fertig(werk)
    warte_bis_einheit_weg(werk)

    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "neu.txt" in r.stdout and "fertig.marke" in r.stdout   # beide neuen Dateien
    diff = diff_datei(werk)
    assert diff.is_file()
    assert "+inhalt" in diff.read_text("utf-8")                   # der Inhalt steckt drin
    assert zustand(werk)["ergebnis_commit"] is not None

    kopf_vorher = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--gate", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (werk["repo"] / "neu.txt").read_text("utf-8").strip() == "inhalt"
    assert len(git(werk["repo"], "log", "-1", "--format=%P").split()) == 2  # Merge-Commit
    nachricht = git(werk["repo"], "log", "-1", "--format=%B")
    assert werk["name"] in nachricht and "Co-Authored-By" not in nachricht
    assert git(werk["repo"], "rev-parse", "HEAD").strip() != kopf_vorher
    assert not werk["kopie"].exists()      # Arbeitskopie weg
    assert not zweig_existiert(werk)       # Zweig weg
    assert zustand(werk)["abgeschlossen"] == "uebernommen"


# --- Exit 12: Ergebnis nicht (oder nicht in diesem Stand) angesehen ----------------

def test_uebernahme_ohne_ergebnis_und_nach_aenderung_exit12(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)

    # nie angesehen → Exit 12 (Review vor Übernahme ist Pflicht)
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 12
    assert "ergebnis" in r.stderr

    assert aufruf("ergebnis", werk["name"]).returncode == 0

    # nach dem Ansehen dazugekommen → Exit 12, nichts übernommen
    (werk["kopie"] / "neu.txt").write_text("nachgereicht\n", encoding="utf-8")
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 12
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf
    assert werk["kopie"].exists()


# --- Agent committet selbst und hinterlässt Uncommittetes (§9.4) -------------------

def test_agenten_commits_und_uncommittetes_beides_im_ergebnis(werk):
    agent = ("echo vom-agenten > agent.txt && git add agent.txt && "
             "git commit -q -m 'vom agenten selbst' && "
             "echo ungesichert > uncommit.txt && touch fertig.marke")
    r = starte(werk, sh(agent), *identitaet_als_env())
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "vom agenten selbst" in r.stdout      # der Agenten-Commit in der Liste
    assert "uncommit.txt" in r.stdout            # das Uncommittete dabei
    z = zustand(werk)
    botschaft = git(werk["repo"], "log", "-1", "--format=%B", z["ergebnis_commit"])
    # der Sichern-Commit: Nachricht ohne jeden Trailer, Autor fest nach §9.7.
    # *(Bis 20.09.2026 stand hier `autor == IDENTITAET["GIT_AUTHOR_NAME"]`, also die
    # Identität aus der Umgebung. Das war die alte Regel; §9.7 hat sie bewusst
    # abgelöst, weil der Autor sonst davon abhing, ob jemand `git config` im neuen
    # Repo gesetzt hat. Die Zusicherung wurde also wegen einer Vertragsänderung
    # nachgezogen, nicht um ein rotes Gate grün zu bekommen.)*
    assert "laufwaechter: Stand von" in botschaft and "Co-Authored-By" not in botschaft
    autor = git(werk["repo"], "log", "-1", "--format=%an", z["ergebnis_commit"]).strip()
    assert autor == arbeitskopie.AUTOR_NAME_VORGABE
    inhalt = diff_datei(werk).read_text("utf-8")
    assert "+ungesichert" in inhalt and "+vom-agenten" in inhalt  # beides im vollständigen Diff


# --- Hauptrepo läuft während des Laufs weiter (§9.4) -------------------------------

def test_hauptrepo_laeuft_weiter_beide_aenderungen_ankommen(werk):
    r = starte(werk, sh("echo agentenarbeit > agent.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    (werk["repo"] / "weiter.txt").write_text("parallel\n", encoding="utf-8")
    git(werk["repo"], "add", "-A")
    git(werk["repo"], "commit", "-q", "-m", "parallelstand")
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "weitergelaufen" in r.stdout          # Hinweis auf den Zwischenstand
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (werk["repo"] / "agent.txt").read_text("utf-8").strip() == "agentenarbeit"
    assert (werk["repo"] / "weiter.txt").exists()  # beide Änderungen im Hauptrepo


def test_konflikt_beim_zusammenfuehren_exit11(werk):
    r = starte(werk, sh("printf 'agenten-zeile\\nzeile-zwei\\n' > bestand.txt; "
                        "touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    # das Hauptrepo ändert dieselbe Zeile anders
    (werk["repo"] / "bestand.txt").write_text("hauptrepo-zeile\nzeile-zwei\n",
                                              encoding="utf-8")
    git(werk["repo"], "add", "-A")
    git(werk["repo"], "commit", "-q", "-m", "gleiche zeile, andere meinung")
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 11
    assert "Konflikt" in r.stderr and "bestand.txt" in r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf  # Hauptrepo unverändert
    assert werk["kopie"].exists()                                  # Arbeitskopie steht
    assert (werk["kopie"] / "bestand.txt").read_text("utf-8").startswith("agenten-zeile")


# --- Gate rot (§9.4) ----------------------------------------------------------------

def test_gate_rot_exit10(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--gate", "false")
    assert r.returncode == 10
    assert "Gate rot" in r.stderr and "false" in r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf
    assert werk["kopie"].exists()                # Arbeitskopie noch da


def test_gate_ausgabe_durchgereicht_und_abbruch_beim_ersten_rot(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    r = aufruf("abschliessen", werk["name"], "--uebernehmen",
               "--gate", "echo erster-gate-laeuft", "--gate", "false",
               "--gate", "echo nie-erreicht")
    assert r.returncode == 10
    assert "erster-gate-laeuft" in r.stdout      # Ausgabe des ersten Gates durchgereicht
    assert "nie-erreicht" not in r.stdout        # nach dem roten Gate läuft nichts mehr


# --- verwerfen (§9.4) ----------------------------------------------------------------

def test_verwerfen_sichert_arbeitskopie_weg_zweig_bleibt(werk):
    r = starte(werk, sh("echo gerettet > gerettet.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("abschliessen", werk["name"], "--verwerfen")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not werk["kopie"].exists()                        # Arbeitskopie weg
    assert zweig_existiert(werk)                             # Zweig bleibt stehen
    assert git(werk["repo"], "show", f"{werk['zweig']}:gerettet.txt").strip() == "gerettet"
    assert f"git branch -D {werk['zweig']}" in r.stdout      # Handgriff für den Menschen
    assert zustand(werk)["abgeschlossen"] == "verworfen"
    # nochmal abschliessen → Exit 2 (schon abgeschlossen)
    assert aufruf("abschliessen", werk["name"], "--verwerfen").returncode == 2


# --- Zweitbau: zwei Läufe gleichzeitig aus derselben Basis (§9.4) --------------------

def test_zwei_laeufe_gleichzeitig_aus_gleicher_basis(werk):
    name_a, name_b = werk["name"] + "-a", werk["name"] + "-b"
    for name, datei in ((name_a, "nur-a.txt"), (name_b, "nur-b.txt")):
        r = aufruf("start", name, "--ordner", str(werk["repo"]), "--arbeitskopie",
                   "--marke", "fertig.marke", "--log", "lauf.log", "--",
                   *sh(f"echo {name} > {datei}; touch fertig.marke"))
        assert r.returncode == 0, r.stderr
    kopie_a, kopie_b = werk["kopie"].parent / name_a, werk["kopie"].parent / name_b
    assert kopie_a.is_dir() and kopie_b.is_dir() and kopie_a != kopie_b
    z_a, z_b = zustand(werk, name_a), zustand(werk, name_b)
    assert z_a["basis"] == z_b["basis"]           # dieselbe Basis (Zweitbau)
    assert z_a["zweig"] != z_b["zweig"]
    for name in (name_a, name_b):
        warte_fertig(werk, name)
        warte_bis_einheit_weg(werk, name)
        assert aufruf("ergebnis", name).returncode == 0
    assert aufruf("abschliessen", name_a, "--uebernehmen", "--ohne-gate").returncode == 0
    assert aufruf("abschliessen", name_b, "--verwerfen").returncode == 0
    assert (werk["repo"] / "nur-a.txt").exists()    # der eine übernommen
    assert not (werk["repo"] / "nur-b.txt").exists()  # der andere nicht
    assert not kopie_b.exists()
    assert zweig_existiert(werk, "lw/" + name_b)    # sein Zweig bleibt stehen


# --- Exit 9: Lauf läuft noch ----------------------------------------------------------

def test_ergebnis_und_abschliessen_waehrend_des_laufs_exit9(werk):
    r = starte(werk, ["sleep", "60"])
    assert r.returncode == 0, r.stderr
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 9
    assert "läuft noch" in r.stderr
    assert aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate").returncode == 9
    assert aufruf("abschliessen", werk["name"], "--verwerfen").returncode == 9


# --- Exit 8: Hauptrepo nicht bereit -----------------------------------------------------

def test_uncommittete_aenderungen_exit8_und_start_trotzdem(werk):
    (werk["repo"] / "bestand.txt").write_text("ungebunden\n", encoding="utf-8")
    (werk["repo"] / "brandneu.txt").write_text("neu\n", encoding="utf-8")
    r = starte(werk, sh("echo etwas > kopie.txt; touch fertig.marke"))
    assert r.returncode == 8
    assert "bestand.txt" in r.stderr and "brandneu.txt" in r.stderr
    assert "NICHT in die Arbeitskopie" in r.stderr
    assert not werk["kopie"].exists() and not zweig_existiert(werk)
    # --trotz-uncommittet erlaubt den Start und wiederholt die Warnung
    r = starte(werk, sh("echo etwas > kopie.txt; touch fertig.marke"),
               "--trotz-uncommittet")
    assert r.returncode == 0, r.stderr
    assert "bestand.txt" in r.stderr and "trotzdem" in r.stdout  # die Warnung kommt erneut
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    # die uncommitteten Änderungen sind NICHT in die Arbeitskopie gekommen
    assert (werk["kopie"] / "bestand.txt").read_text("utf-8") == "zeile-eins\nzeile-zwei\n"
    assert not (werk["kopie"] / "brandneu.txt").exists()
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    # und die Übernahme verweigert sich, solange das Hauptrepo unruhig ist (Exit 8)
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 8
    assert "nichts wird umgeschaltet" in r.stderr
    # aufräumen: verwerfen prüft das Hauptrepo nicht
    assert aufruf("abschliessen", werk["name"], "--verwerfen").returncode == 0


def test_detached_head_exit8(werk):
    git(werk["repo"], "checkout", "--detach", "HEAD")   # nur im Wegwerf-Repo
    r = starte(werk, sh("touch fertig.marke"))
    assert r.returncode == 8
    assert "detached" in r.stderr
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


# --- Exit 5: Zweig bzw. Arbeitskopie-Pfad existiert schon --------------------------------

def test_zweig_existiert_schon_exit5(werk):
    git(werk["repo"], "branch", werk["zweig"])   # von Hand angelegt, wie es Menschen tun
    r = starte(werk, sh("touch fertig.marke"))
    assert r.returncode == 5
    assert werk["zweig"] in r.stderr and f"git branch -D {werk['zweig']}" in r.stderr
    assert not werk["kopie"].exists()


def test_arbeitskopie_pfad_existiert_exit5(werk):
    werk["kopie"].parent.mkdir(parents=True, exist_ok=True)
    werk["kopie"].write_text("hier war schon jemand\n", encoding="utf-8")
    r = starte(werk, sh("touch fertig.marke"))
    assert r.returncode == 5
    assert str(werk["kopie"]) in r.stderr
    assert not zweig_existiert(werk)


# --- Exit 2: unbrauchbare Aufrufe ----------------------------------------------------------

def test_ordner_nicht_oberste_ebene_exit2(werk):
    unter = werk["repo"] / "unter"
    unter.mkdir()
    r = aufruf("start", werk["name"], "--ordner", str(unter), "--arbeitskopie",
               "--marke", "fertig.marke", "--log", "lauf.log", "--", "/bin/true")
    assert r.returncode == 2
    assert "oberste Ebene" in r.stderr
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


def test_verlinke_versionierte_datei_exit2_und_aufgeraeumt(werk):
    r = starte(werk, sh("sleep 1; touch fertig.marke"), "--verlinke", "bestand.txt")
    assert r.returncode == 2
    assert "bestand.txt" in r.stderr
    assert not werk["kopie"].exists()           # keine Arbeitskopie übrig
    assert not zweig_existiert(werk)            # kein Zweig übrig
    liste = git(werk["repo"], "worktree", "list", "--porcelain")
    assert str(werk["kopie"]) not in liste      # auch in der Worktree-Verwaltung nicht


def test_verlinke_datei_ist_kopie_hauptrepo_bleibt_unberuehrt(werk):
    """§9.5 Punkt 2: --verlinke auf eine Datei kopiert sie — der Agent kann die Datei
    des Hauptrepos nicht verändern (früher Symlink: unsichtbarer Durchgriff,
    Gutachten Befund 3)."""
    r = starte(werk, sh("echo GEHEIM=99 > .env; cat .env > gelesen.txt; touch fertig.marke"),
               "--verlinke", ".env")
    assert r.returncode == 0, r.stderr
    kopie_env = werk["kopie"] / ".env"
    assert kopie_env.is_file() and not kopie_env.is_symlink()
    assert kopie_env.read_text("utf-8") == "GEHEIM=99\n"
    assert zustand(werk)["verlinkt"] == [".env"]
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert (werk["repo"] / ".env").read_text("utf-8") == "GEHEIM=42\n"   # unberührt geblieben
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert ".env" not in r.stdout          # verlinkt zählt nie zur Arbeit (§9.5 Punkt 3)
    assert "gelesen.txt" in r.stdout
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (werk["repo"] / ".env").read_text("utf-8") == "GEHEIM=42\n"   # immer noch
    assert (werk["repo"] / "gelesen.txt").read_text("utf-8") == "GEHEIM=99\n"
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


def test_ergebnis_und_abschliessen_ohne_arbeitskopie_exit2(werk):
    normal = werk["repo"].parent / "ohne-ak"
    normal.mkdir()
    r = aufruf("start", werk["name"], "--ordner", str(normal),
               "--marke", str(normal / "fertig.marke"), "--log", str(normal / "lauf.log"),
               "--", "/bin/true")
    assert r.returncode == 0, r.stderr
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 2 and "Arbeitskopie" in r.stderr
    assert aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate").returncode == 2
    assert aufruf("abschliessen", werk["name"], "--verwerfen").returncode == 2


def test_unbekannter_name_exit2(werk):
    for unterbefehl in (("ergebnis",), ("abschliessen", "--uebernehmen", "--ohne-gate"),
                        ("abschliessen", "--verwerfen")):
        r = aufruf(*unterbefehl, "gibtsnicht" + werk["name"])
        assert r.returncode == 2
        assert "Unbekannter Lauf" in r.stderr


def test_gate_optionen_falsch_exit2(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    # keins von beidem
    r = aufruf("abschliessen", werk["name"], "--uebernehmen")
    assert r.returncode == 2
    assert "--gate" in r.stderr and "--ohne-gate" in r.stderr
    # beides zugleich
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--gate", "true", "--ohne-gate")
    assert r.returncode == 2
    # --uebernehmen und --verwerfen zusammen
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--verwerfen", "--ohne-gate")
    assert r.returncode == 2
    # keins von beiden
    assert aufruf("abschliessen", werk["name"]).returncode == 2
    # Gate-Optionen bei --verwerfen sind unsinnig
    assert aufruf("abschliessen", werk["name"], "--verwerfen", "--ohne-gate").returncode == 2
    # sauber zu Ende: mit --ohne-gate klappt die Übernahme
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr


# --- Exit 1: Start scheitert, Arbeitskopie und Zweig werden aufgeräumt (§9.4) -------------

def test_gescheiterter_start_raeumt_arbeitskopie_und_zweig_weg_exit1(werk):
    lang = "a" * 250            # systemd lehnt den Einheitennamen ab (echte Verweigerung)
    r = aufruf("start", lang, "--ordner", str(werk["repo"]), "--arbeitskopie",
               "--marke", "fertig.marke", "--log", "lauf.log", "--", "/bin/true")
    assert r.returncode == 1
    assert "systemd-run fehlgeschlagen" in r.stderr
    assert not (werk["kopie"].parent / lang).exists()
    assert not zweig_existiert(werk, "lw/" + lang)
    liste = git(werk["repo"], "worktree", "list", "--porcelain")
    assert str(werk["kopie"].parent / lang) not in liste


# --- status zeigt zusätzlich Arbeitskopie, Zweig, Basis-Commit, abgeschlossen? (§9.1) ------

def test_status_zeigt_arbeitskopie_zweig_basis_abschluss(werk):
    basis_kurz = git(werk["repo"], "rev-parse", "--short", "HEAD").strip()
    r = starte(werk, sh("sleep 30"))
    assert r.returncode == 0, r.stderr
    r = aufruf("status", werk["name"])
    assert r.returncode == 0, r.stderr
    assert "Arbeitskopie" in r.stdout
    assert werk["zweig"] in r.stdout
    assert basis_kurz in r.stdout
    assert "noch nicht angesehen" in r.stdout
    assert "nein" in r.stdout            # abgeschlossen?
    # ein normaler Lauf zeigt diese Zusatzzeilen nicht
    anderer = werk["repo"].parent / "ohne-ak"
    anderer.mkdir()
    zweiter = "lw-" + werk["name"] + "n"
    werk["einheiten"].append(zweiter)
    r = aufruf("start", werk["name"] + "n", "--ordner", str(anderer),
               "--marke", str(anderer / "m"), "--log", str(anderer / "l"),
               "--", "sleep", "30")
    assert r.returncode == 0, r.stderr
    r = aufruf("status", werk["name"] + "n")
    assert r.returncode == 0, r.stderr
    assert "Arbeitskopie" not in r.stdout


# --- §9.5 Punkt 1: --basis muss Vorfahre des Hauptzweigs sein --------------------------------

def test_basis_auf_fremdem_zweig_exit2_nichts_uebrig(werk):
    git(werk["repo"], "checkout", "-q", "-b", "fremd")   # nur im Wegwerf-Repo
    (werk["repo"] / "fremd.txt").write_text("fremde historie\n", encoding="utf-8")
    git(werk["repo"], "add", "-A")
    git(werk["repo"], "commit", "-q", "-m", "fremde arbeit")
    git(werk["repo"], "checkout", "-q", "main")
    r = starte(werk, sh("touch fertig.marke"), "--basis", "fremd")
    assert r.returncode == 2
    assert "Vorfahre" in r.stderr and "fremd" in r.stderr
    assert not werk["kopie"].exists() and not zweig_existiert(werk)
    assert str(werk["kopie"]) not in git(werk["repo"], "worktree", "list", "--porcelain")


# --- §9.5 Punkt 3: verlinkter Ordner (.venv, Symlink) wird nie versioniert --------------------

def test_verlinke_venv_symlink_nie_versioniert_arbeitskopie_weg(werk):
    """Der vom Orchestrator gemessene Fall: Symlink `.venv` erfüllt das .gitignore-
    Muster `.venv/` nicht — ohne Ausschluss würde er committet und ins Hauptrepo
    übernommen, und `git worktree remove` ohne Gewalt scheiterte."""
    mit_venv_ignore(werk["repo"])
    r = starte(werk, sh("cat .venv/pyvenv.cfg > gelesen.txt; touch fertig.marke"),
               "--verlinke", ".venv")
    assert r.returncode == 0, r.stderr
    assert "WARNUNG" in r.stdout and "Schreibzugriffe" in r.stdout  # Punkt 2: Ordner-Warnung
    assert (werk["kopie"] / ".venv").is_symlink()
    assert zustand(werk)["verlinkt"] == [".venv"]
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert ".venv" not in r.stdout           # der Verweis zählt nie zur Arbeit (Punkt 3)
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    versioniert = git(werk["repo"], "ls-files")
    assert ".venv" not in versioniert and "pyvenv.cfg" not in versioniert
    assert not (werk["repo"] / ".venv").is_symlink()
    assert not werk["kopie"].exists()        # der Verweis wurde vorher entfernt (Punkt 7)
    assert not zweig_existiert(werk)


# --- §9.5 Punkt 6: Gates dürfen den geprüften Stand nicht verändern ---------------------------

def test_gate_aendert_versionierte_datei_exit10(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen",
               "--gate", "echo geaendert >> bestand.txt")
    assert r.returncode == 10
    assert "Gate hat den geprüften Stand verändert" in r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf   # nichts übernommen
    assert werk["kopie"].exists() and zweig_existiert(werk)
    assert (werk["kopie"] / "bestand.txt").read_text("utf-8").endswith("geaendert\n")


def test_gate_commitet_exit10(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen",
               # Identität erbt aufruf; nach dem Sichern ist der Baum sauber, das Gate
               # legt erst eigene Arbeit an, dann committet es
               "--gate", "echo vom-gate > gate.txt && git add -A && git commit -q -m vom-gate")
    assert r.returncode == 10
    assert "Gate hat den geprüften Stand verändert" in r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf
    assert werk["kopie"].exists() and zweig_existiert(werk)


def test_gate_hinterlaesst_nur_unversionierte_uebernahme_gelingt(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    r = aufruf("abschliessen", werk["name"], "--uebernehmen",
               "--gate", "echo gate-rest > rest.txt")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not werk["kopie"].exists()      # unversionierte Reste stammen von den Gates:
    assert not zweig_existiert(werk)       # Gewalt-Aufräumen war hier erlaubt (Punkt 7)
    assert not (werk["repo"] / "rest.txt").exists()


# --- §9.5 Punkt 5: unversionierte Dateien im Hauptrepo blockieren die Übernahme nicht ---------

def test_unversionierte_datei_im_hauptrepo_blockiert_nicht(werk):
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    (werk["repo"] / "ungebunden.txt").write_text("ungebunden\n", encoding="utf-8")
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (werk["repo"] / "ungebunden.txt").read_text("utf-8") == "ungebunden\n"
    assert (werk["repo"] / "neu.txt").exists()


# --- §9.5 Punkt 4 und 8: ergebnis relativ zum Hauptzweig; rotes Gate verlangt neues ergebnis --

def test_ergebnis_nach_hereingeholtem_hauptstand_ohne_hauptrepo_datei(werk):
    r = starte(werk, sh("echo agentenarbeit > agent.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    (werk["repo"] / "weiter.txt").write_text("parallel\n", encoding="utf-8")
    git(werk["repo"], "add", "-A")
    git(werk["repo"], "commit", "-q", "-m", "parallelstand")
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    # rotes Gate: der Hauptstand wird zuvor in die Arbeitskopie geholt und bleibt dort
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--gate", "false")
    assert r.returncode == 10
    # Punkt 8: der hereingeholte Stand steht auf lw/NAME — neuer Versuch braucht ergebnis
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 12
    # Punkt 4: das zweite ergebnis nennt die Datei des Hauptrepos nicht als Agentenarbeit
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "weiter.txt" not in r.stdout
    assert "agent.txt" in r.stdout
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() != kopf
    assert (werk["repo"] / "weiter.txt").read_text("utf-8") == "parallel\n"
    assert (werk["repo"] / "agent.txt").exists()


# --- §9.6: fremde Verweise werden nie übernommen (Exit 13) -----------------------------------

def git_mini_repo(tmp_path):
    """Wegwerf-Repo mit einem Anfangs-Commit für die direkten §9.6-Prüfungen."""
    repo = tmp_path / "mini"
    repo.mkdir()
    (repo / "bestand.txt").write_text("zeile\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "anfang")
    return repo


def mit_daten_ordner(repo, gemustet=False):
    """Nicht versionierte Daten im Hauptrepo — ein typisches Muster: sie
    reisen nie in die Arbeitskopie, deshalb legt der Mensch sie sich dort von Hand
    als Verweis hinein. gemustet=True trägt das Muster `daten/` MIT Schrägstrich in
    die .gitignore ein (Fall 2); ohne Muster zählt der Ordner als uncommittet, der
    Start braucht --trotz-uncommittet (Fall 1)."""
    daten = repo / "daten"
    daten.mkdir()
    (daten / "inhalt.txt").write_text("2,2-GB-Ersatz\n", encoding="utf-8")
    if gemustet:
        with open(repo / ".gitignore", "a", encoding="utf-8") as hand:
            hand.write("daten/\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "daten/ gemustert")
    return daten


def test_96_beurteilung_ohne_dem_ziel_zu_folgen(tmp_path):
    """§9.6 Punkt 1: beurteilt wird der normalisierte Zielpfad (normpath, relativ zum
    Ort des Symlinks), nie resolve/realpath auf dem Symlink; keinem Symlink wird beim
    Durchlaufen gefolgt; .git/ bleibt außen vor; angemeldete (ausschliessen) zählen nie."""
    repo = git_mini_repo(tmp_path)
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (repo / "innen.txt").write_text("innen\n", encoding="utf-8")
    (repo / "verweis-aussen").symlink_to(aussen)                    # absolut, außerhalb
    (repo / "verweis-ins-leere").symlink_to(tmp_path / "nirgends")  # existiert nicht
    (repo / "verweis-hoch").symlink_to("../hoch")                   # relativ, heraus
    (repo / "verweis-innen").symlink_to("innen.txt")                # relativ, hinein
    (repo / "verweis-wurzel").symlink_to(repo)                      # Ziel == Wurzel
    (repo / "unter").mkdir()
    (repo / "unter" / "verweis-tief").symlink_to("../../aussen")    # relativ, aus Tiefe
    (repo / "tor").symlink_to(aussen)                               # Verweis auf Ordner da draußen
    (aussen / "falle").symlink_to("/etc")      # dürfte nie erscheinen: tor wird nicht betreten
    (repo / ".git" / "hoehle").symlink_to(aussen)                   # .git/ bleibt außen vor
    funde = dict(arbeitskopie.fremde_verweise(repo))
    assert funde == {
        "verweis-aussen": str(aussen),
        "verweis-ins-leere": str(tmp_path / "nirgends"),
        "verweis-hoch": "../hoch",
        "unter/verweis-tief": "../../aussen",
        "tor": str(aussen),
    }
    # angemeldete Verweise (verlinkt, §9.5 Punkt 3) sind nie fremd
    rest = dict(arbeitskopie.fremde_verweise(repo, ausschliessen=("tor",)))
    assert "tor" not in rest and len(rest) == 4


def test_96_pruefung_a_sieht_gesicherte_verweise_nicht_mehr(tmp_path):
    """§9.6 Punkt 3: steckt der Verweis schon unverändert im Zweig, ist er Geschichte —
    Prüfung A überspringt ihn, Prüfung B meldet ihn."""
    repo = git_mini_repo(tmp_path)
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (repo / "daten").symlink_to(aussen)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "verweis im zweig")
    assert arbeitskopie.fremde_verweise(repo) == []
    assert arbeitskopie.fremde_verweise_im_zweig(repo, "HEAD") == [("daten", str(aussen))]


def test_96_pruefung_b_im_zweig_liest_blob_und_beurteilt_wie_punkt1(tmp_path):
    """§9.6 Punkt 3 (Prüfung B): jeder ls-tree-Eintrag mit Modus 120000, Ziel aus dem
    Blob, beurteilt wie Punkt 1 relativ zum Ort des Eintrags; Verweise nach innen bleiben."""
    repo = git_mini_repo(tmp_path)
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (repo / "verweis-innen").symlink_to("bestand.txt")
    (repo / "daten").symlink_to(aussen)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "zwei verweise")
    assert arbeitskopie.fremde_verweise_im_zweig(repo, "HEAD") == [("daten", str(aussen))]


def test_96_fall1_verweis_von_hand_ergebnis_exit13_nichts_gesichert(werk):
    """Fall 1: Symlink von Hand in die Arbeitskopie, Ziel außerhalb → ergebnis Exit 13,
    kein neuer Commit auf lw/NAME, Ziel unverändert, Arbeitskopie steht."""
    daten = mit_daten_ordner(werk["repo"])
    r = starte(werk, sh("sleep 1; touch fertig.marke"), "--trotz-uncommittet")
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    zweig_vorher = git(werk["repo"], "rev-parse", werk["zweig"]).strip()
    (werk["kopie"] / "daten").symlink_to(daten)   # von Hand — nicht per --verlinke
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 13, r.stdout + r.stderr
    assert "daten ->" in r.stderr
    assert "rm daten" in r.stderr and "rm -r" in r.stderr and "--verlinke daten" in r.stderr
    assert git(werk["repo"], "rev-parse", werk["zweig"]).strip() == zweig_vorher  # kein Commit
    assert "?? daten" in git(werk["kopie"], "status", "--porcelain")  # nichts gestagt
    assert (daten / "inhalt.txt").read_text("utf-8") == "2,2-GB-Ersatz\n"  # Ziel heil
    assert werk["kopie"].exists()                 # Arbeitskopie steht


def test_96_fall2_gitignore_mit_schraegstrich_retter_nichts_exit13(werk):
    """Fall 2 — der wichtigste des Pakets, genau die 2,2-GB-Konstellation: die
    .gitignore sperrt `daten/` MIT Schrägstrich; das Muster passt nie auf einen
    Symlink gleichen Namens (git status zeigt ihn weiter als `??`), und die Prüfung
    geht ohnehin den Baum statt den Status. Immer noch Exit 13."""
    daten = mit_daten_ordner(werk["repo"], gemustet=True)
    r = starte(werk, sh("sleep 1; touch fertig.marke"), "--trotz-uncommittet")
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    zweig_vorher = git(werk["repo"], "rev-parse", werk["zweig"]).strip()
    (werk["kopie"] / "daten").symlink_to(daten)   # von Hand angelegt, wie im untersuchten Symlink-Fehlerfall
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 13, r.stdout + r.stderr
    assert "daten ->" in r.stderr
    assert git(werk["repo"], "rev-parse", werk["zweig"]).strip() == zweig_vorher
    # das Muster `daten/` hat den Symlink NICHT abgefangen — er liegt weiter unversioniert
    assert "?? daten" in git(werk["kopie"], "status", "--porcelain")
    assert (daten / "inhalt.txt").read_text("utf-8") == "2,2-GB-Ersatz\n"
    assert werk["kopie"].exists()


def test_96_fall3_agent_hat_verweis_commitet_abschliessen_exit13(werk):
    """Fall 3: der Agent hat den Verweis selbst committet → ergebnis Exit 0 (er liegt
    in der Geschichte des Zweigs, wo Prüfung A ihn nicht mehr sieht), aber
    abschliessen --uebernehmen Exit 13, Hauptrepo-HEAD unverändert."""
    daten = mit_daten_ordner(werk["repo"])
    agent = (f"ln -s {shlex.quote(str(daten))} daten && git add daten && "
             "git commit -q -m verweis-vom-agenten && touch fertig.marke")
    r = starte(werk, sh(agent), "--trotz-uncommittet", *identitaet_als_env())
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    kopf = git(werk["repo"], "rev-parse", "HEAD").strip()
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 13, r.stdout + r.stderr
    assert "daten ->" in r.stderr
    assert git(werk["repo"], "rev-parse", "HEAD").strip() == kopf   # Hauptrepo unberührt
    assert werk["kopie"].exists() and (werk["kopie"] / "daten").is_symlink()
    assert zweig_existiert(werk)


def test_96_fall4_verweis_nach_innen_kommt_als_verweis_an(werk):
    """Fall 4: Symlink mit Ziel innerhalb der Arbeitskopie → Übernahme gelingt, der
    Symlink kommt als Symlink im Hauptrepo an."""
    r = starte(werk, sh("ln -s bestand.txt verweis-innen; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert arbeitskopie.fremde_verweise(str(werk["kopie"])) == []
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (werk["repo"] / "verweis-innen").is_symlink()
    assert os.readlink(werk["repo"] / "verweis-innen") == "bestand.txt"
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


def test_96_fall5_verlinke_bleibt_angemeldet_exit0(werk):
    """Fall 5: --verlinke .venv wie bisher → Übernahme gelingt, Exit 0, kein .venv im
    Hauptrepo versioniert; die start-Warnung nennt zusätzlich Exit 13 (§9.6 Punkt 6)."""
    mit_venv_ignore(werk["repo"])
    r = starte(werk, sh("cat .venv/pyvenv.cfg > gelesen.txt; touch fertig.marke"),
               "--verlinke", ".venv")
    assert r.returncode == 0, r.stderr
    assert "Exit 13" in r.stdout                    # §9.6 Punkt 6: ergänzter Warnsatz
    assert (werk["kopie"] / ".venv").is_symlink()
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr   # Prüfung A: angemeldet zählt nie
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr   # Prüfung B: der Baum enthält ihn nie
    assert ".venv" not in git(werk["repo"], "ls-files")
    assert not (werk["repo"] / ".venv").is_symlink()
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


def test_96_fall6_verweis_ins_leere_exit13(werk):
    """Fall 6: Symlink ins Leere mit Ziel außerhalb → Exit 13 (beurteilt wird der
    normalisierte Zielpfad, nicht der Inhalt des Ziels — das gibt es ja nicht)."""
    r = starte(werk, sh("sleep 1; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    (werk["kopie"] / "toter-link").symlink_to(werk["kopie"].parent / "verschwunden")
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 13, r.stdout + r.stderr
    assert "toter-link ->" in r.stderr
    assert werk["kopie"].exists()


def test_96_fall7_gegenprobe_ohne_symlinks_exit0(werk):
    """Fall 7 — Gegenprobe zur Prüfung selbst: ein Lauf ganz ohne Symlinks bleibt bei
    Exit 0. Eine Prüfung, die immer anschlägt, prüft nichts."""
    r = starte(werk, sh("echo etwas > neu.txt; touch fertig.marke"))
    assert r.returncode == 0, r.stderr
    warte_fertig(werk)
    warte_bis_einheit_weg(werk)
    assert arbeitskopie.fremde_verweise(str(werk["kopie"])) == []
    r = aufruf("ergebnis", werk["name"])
    assert r.returncode == 0, r.stdout + r.stderr
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not werk["kopie"].exists() and not zweig_existiert(werk)


# --- Quelltext-Regel (§9.4): verbotene Handgriffe nur an der erlaubten Stelle ---------------

VERBOTENE_HANDGRIFFE = ("checkout", "reset", "stash", "push", "fetch",
                        "branch -D", '"-D"', "'-D'", "--force")
# Erlaubnisliste: in arbeitskopie.py die Aufräum-Stelle nach fehlgeschlagenem Start
# (§9.1) und — als zweite erlaubte Stelle, eng an diese eine Funktion gebunden — das
# Aufräumen nach der Übernahme samt seines Gewalt-Handgriffs (§9.5 Punkt 7); in
# laufwaechter.py allein die Meldungstexte für den Menschen, weil §9.1 verlangt, dass
# start (Zweig existiert) und --verwerfen den Handgriff nennen, und §9.5 Punkt 7, dass
# die Aufräum-Warnung die genauen Handgriffe nennt.
ERLAUBNISSE = {
    "arbeitskopie.py": {"raeume_gescheiterten_start_weg", "zweig_loeschen_erzwungen",
                        "arbeitskopie_gewaltsam_entfernen", "raume_nach_uebernahme"},
    "laufwaechter.py": {"hinweis_zweig_loeschen", "hinweis_aufraeumen"},
}


@pytest.mark.parametrize("datei", sorted(ERLAUBNISSE))
def test_quelltext_nennt_verbotene_handgriffe_nur_an_erlaubter_stelle(datei):
    quelle = (WURZEL / "tools" / datei).read_text(encoding="utf-8")
    erlaubte_zeilen = set()
    for knoten in ast.walk(ast.parse(quelle)):
        if (isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
                and knoten.name in ERLAUBNISSE[datei]):
            erlaubte_zeilen.update(range(knoten.lineno, knoten.end_lineno + 1))
    for nummer, zeile in enumerate(quelle.splitlines(), start=1):
        for wort in VERBOTENE_HANDGRIFFE:
            if wort in zeile:
                assert nummer in erlaubte_zeilen, (
                    f"{datei}:{nummer} nennt {wort!r} außerhalb der Erlaubnisliste: "
                    f"{zeile.strip()}")


# ---------------------------------------------------------------------------
# §9.7 — die EIGENEN Commits des Laufwächters tragen einen festen Autor
# ---------------------------------------------------------------------------


def _autor(repo, revision="HEAD"):
    return git(repo, "log", "-1", "--format=%an <%ae>", revision).strip()


def test_sicherungs_commit_traegt_festen_autor_trotz_fremder_repo_konfiguration(werk):
    """§9.7 Punkt 1: Das Repo hat absichtlich eine andere `user.email` — der
    Sicherungs-Commit des Laufwächters trägt trotzdem die Vorgabe. Genau diese
    Abhängigkeit von der Repo-Konfiguration war am 20.09.2026 die Ursache für
    drei verschiedene Autor-Adressen in einem Repo."""
    git(werk["repo"], "config", "user.name", "Fremder")
    git(werk["repo"], "config", "user.email", "fremd@example.com")
    assert starte(werk, ["sh", "-c", "echo neu > neu.txt && echo ok > fertig.marke"]).returncode == 0
    warte_fertig(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    assert _autor(werk["repo"], werk["zweig"]) == "Orchestrated Team <team@example.com>"
    # Und zwar NICHT die Identität aus der Umgebung, die der Testlauf setzt
    # (GIT_AUTHOR_* schlägt `-c user.email` — genau daran scheiterte die erste Fassung).
    assert "Pruefstand" not in _autor(werk["repo"], werk["zweig"])


def test_merge_commit_traegt_festen_autor_trotz_fremder_repo_konfiguration(werk):
    """§9.7 Punkt 1, zweite Stelle: auch der Merge-Commit im Hauptrepo."""
    git(werk["repo"], "config", "user.name", "Fremder")
    git(werk["repo"], "config", "user.email", "fremd@example.com")
    assert starte(werk, ["sh", "-c", "echo neu > neu.txt && echo ok > fertig.marke"]).returncode == 0
    warte_fertig(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    r = aufruf("abschliessen", werk["name"], "--uebernehmen", "--ohne-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _autor(werk["repo"]) == "Orchestrated Team <team@example.com>"


def test_autor_ist_ueber_umgebung_ueberschreibbar(werk, monkeypatch):
    """§9.7 Punkt 2: sonst prüfte der Test oben nur, dass IRGENDEINE feste
    Zeichenkette ankommt — mit dieser Gegenprobe ist belegt, dass der Wert
    wirklich aus `autor_optionen()` stammt und nicht zufällig stimmt."""
    monkeypatch.setenv("LAUFWAECHTER_AUTOR_NAME", "Probe Person")
    monkeypatch.setenv("LAUFWAECHTER_AUTOR_MAIL", "probe@example.com")
    assert starte(werk, ["sh", "-c", "echo neu > neu.txt && echo ok > fertig.marke"]).returncode == 0
    warte_fertig(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    assert _autor(werk["repo"], werk["zweig"]) == "Probe Person <probe@example.com>"


def test_commit_des_agenten_behaelt_seinen_eigenen_autor(werk):
    """§9.7 Punkt 3: Nur die eigenen Commits des Laufwächters bekommen den festen
    Autor. Was der Agent selbst committet, ist seine Arbeit und bleibt unberührt."""
    # Der Agent setzt seinen Autor über die Umgebung — dieselbe Rangstufe, die der
    # Laufwächter für seine eigenen Commits benutzt (§9.7). Würde er hier `-c`
    # verwenden, prüfte der Test nur die schwächere Stufe.
    befehl = ("echo neu > neu.txt && git add -A && "
              "GIT_AUTHOR_NAME=Agent GIT_AUTHOR_EMAIL=agent@example.com "
              "GIT_COMMITTER_NAME=Agent GIT_COMMITTER_EMAIL=agent@example.com "
              "git commit -q -m 'vom Agenten' && echo ok > fertig.marke")
    assert starte(werk, ["sh", "-c", befehl]).returncode == 0
    warte_fertig(werk)
    assert aufruf("ergebnis", werk["name"]).returncode == 0
    # Der Agenten-Commit liegt unverändert in der Geschichte des Zweigs.
    autoren = git(werk["repo"], "log", "--format=%an <%ae>", werk["zweig"])
    assert "Agent <agent@example.com>" in autoren

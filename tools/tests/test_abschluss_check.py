"""Tests für werkzeuge/abschluss_check.py (Vertrag: INTERFACES.md §8.2, §8.3).

Nur Wegwerf-Repos unter tmp_path (mit Bare-Repo als Remote), nie echte Projekte.
Jeder Exit-Code (0/1/2) und jedes Befund-Kürzel wird von mindestens einem Test
ausgelöst; dazu ein sauberer Fall mit Exit 0.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "abschluss_check", WERKZEUGE / "abschluss_check.py")
ac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac)

# regeln.py benutzen, nicht nochmals laden: das Werkzeug lädt es in-process (§13.10)
rc = ac.regeln


@pytest.fixture(autouse=True)
def hermetische_regeln(tmp_path, monkeypatch, regelquelle):
    """§13.10 Punkt 5: JEDER Test in dieser Datei bekommt REGELN_HOME auf einen
    Ordner unter tmp_path, in den die korrekten Erzeugnisse geschrieben sind —
    so bleibt der Regelblock in allen Tests OK, egal wie das echte ~ aussieht,
    und kein Test berührt ~/.claude, ~/.codex oder ~/.local/state."""
    heimat = tmp_path / "regel-heimat"
    monkeypatch.setattr(rc, "STANDARD_QUELLE", regelquelle)
    monkeypatch.setenv("REGELN_HOME", str(heimat))
    for ziel, (ordner_name, datei_name) in rc.ZIELE.items():
        p = heimat / ordner_name / datei_name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rc.erzeuge_bytes(rc.STANDARD_QUELLE, ziel))
    return heimat


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} in {cwd}: {r.stderr}"
    return r


def neues_repo(pfad):
    pfad.mkdir(parents=True, exist_ok=True)
    git("init", cwd=pfad)
    git("config", "user.email", "test@example.com", cwd=pfad)
    git("config", "user.name", "Test", cwd=pfad)
    (pfad / "datei.txt").write_text("hallo\n")
    git("add", ".", cwd=pfad)
    git("commit", "-m", "anfang", cwd=pfad)
    return pfad


def commit(pfad, name="datei.txt", inhalt="mehr\n", botschaft="weiter"):
    (pfad / name).write_text(inhalt)
    git("add", ".", cwd=pfad)
    git("commit", "-m", botschaft, cwd=pfad)


def kahl_remote(pfad):
    pfad.mkdir(parents=True, exist_ok=True)
    git("init", "--bare", cwd=pfad)
    return pfad


def mit_repos(monkeypatch, tmp_path, eintraege):
    """repos.json unter tmp_path ablegen und das Werkzeug darauf zeigen lassen."""
    datei = tmp_path / "repos.json"
    datei.write_text(json.dumps({"repos": eintraege}), encoding="utf-8")
    monkeypatch.setattr(ac, "REPOS_JSON", datei)
    return datei


def eintrag(pfad, remote_erwartet=False, elternrepo=False):
    d = {"pfad": str(pfad), "remote_erwartet": remote_erwartet}
    if elternrepo:
        d["elternrepo"] = True
    return d


def lauf(capsys, *args):
    code = ac.main([str(a) for a in args])
    return code, capsys.readouterr().out


# --- Exit 0: sauberer Fall ----------------------------------------------------

def test_sauber_exit_0_ohne_befund(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "sauber")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 0
    assert "keine Befunde" in out
    assert "Summe: 1 Repo(s), 0 Befund(e)" in out


# --- UNCOMMITTET ---------------------------------------------------------------

def test_uncommittet_meldet_anzahl(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "schmutzig")
    (repo / "datei.txt").write_text("geaendert\n")
    (repo / "neu.txt").write_text("neu\n")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "UNCOMMITTET" in out
    assert "2 ungesicherte" in out


# --- FREMDES_REPO ----------------------------------------------------------------

def test_fremdes_repo_unterverzeichnis(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "mutter")
    unter = repo / "unter"
    unter.mkdir()
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, unter)
    assert code == 1
    assert "FREMDES_REPO" in out


# --- KEIN_REPO -------------------------------------------------------------------

def test_kein_repo_leeres_verzeichnis(tmp_path, monkeypatch, capsys):
    leer = tmp_path / "leer"
    leer.mkdir()
    mit_repos(monkeypatch, tmp_path, [])
    code, out = lauf(capsys, leer)
    assert code == 1
    assert "KEIN_REPO" in out


def test_kein_repo_fehlender_pfad(tmp_path, monkeypatch, capsys):
    mit_repos(monkeypatch, tmp_path, [])
    code, out = lauf(capsys, tmp_path / "gibt-es-nicht")
    assert code == 1
    assert "KEIN_REPO" in out


# --- NICHT_GESICHERT ---------------------------------------------------------------

def test_nicht_gesichert_zaehlt_voraus(tmp_path, monkeypatch, capsys):
    remote = kahl_remote(tmp_path / "remote.git")
    repo = neues_repo(tmp_path / "voraus")
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-u", "origin", "HEAD", cwd=repo)
    commit(repo, inhalt="voraus\n", botschaft="voraus")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo, remote_erwartet=True)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "NICHT_GESICHERT" in out
    assert "1 Commit(s)" in out
    assert "ohne fetch" in out


def test_nicht_gesichert_ohne_upstream(tmp_path, monkeypatch, capsys):
    remote = kahl_remote(tmp_path / "remote.git")
    repo = neues_repo(tmp_path / "ohne-upstream")
    git("remote", "add", "origin", str(remote), cwd=repo)
    mit_repos(monkeypatch, tmp_path, [eintrag(repo, remote_erwartet=True)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "NICHT_GESICHERT" in out
    assert "kein Upstream" in out


# --- REMOTE_FEHLT --------------------------------------------------------------------

def test_remote_fehlt_ohne_doppelbefund(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "ohne-remote")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo, remote_erwartet=True)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "REMOTE_FEHLT" in out
    assert "NICHT_GESICHERT" not in out  # kein Remote -> nur REMOTE_FEHLT


def test_remote_checks_entfallen_ohne_eintrag(tmp_path, monkeypatch, capsys):
    """Repo nicht in repos.json: NICHT_GESICHERT/REMOTE_FEHLT entfallen."""
    remote = kahl_remote(tmp_path / "remote.git")
    repo = neues_repo(tmp_path / "fremd")
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-u", "origin", "HEAD", cwd=repo)
    commit(repo, inhalt="voraus\n", botschaft="voraus")
    mit_repos(monkeypatch, tmp_path, [])  # Repo steht nicht in der Liste
    code, out = lauf(capsys, repo)
    assert code == 0
    assert "NICHT_GESICHERT" not in out
    assert "REMOTE_FEHLT" not in out


# --- FREMDER_TRAILER -------------------------------------------------------------------

def test_fremder_trailer_nur_head(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "trailer")
    commit(repo, inhalt="mit\n", botschaft="thema\n\nCo-Authored-By: Mit-Autor <m@example.com>")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "FREMDER_TRAILER" in out


def test_fremder_trailer_kleinschreibung(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "trailer-klein")
    commit(repo, inhalt="mit\n", botschaft="thema\n\nco-authored-by: klein <k@example.com>")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "FREMDER_TRAILER" in out


def test_fremder_trailer_seit_bereich(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "bereich")
    commit(repo, inhalt="eins\n", botschaft="eins")
    commit(repo, inhalt="zwei\n", botschaft="zwei\n\nCo-Authored-By: Alt <a@example.com>")
    commit(repo, inhalt="drei\n", botschaft="drei")
    alt = git("rev-parse", "HEAD~2", cwd=repo).stdout.strip()  # Stand vor dem Trailer
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)  # nur HEAD (sauber) wird geprüft
    assert code == 0
    assert "FREMDER_TRAILER" not in out
    code, out = lauf(capsys, repo, "--seit", alt)  # Bereich schließt Trailer ein
    assert code == 1
    assert "FREMDER_TRAILER" in out


def test_fremder_trailer_upstream_bereich(tmp_path, monkeypatch, capsys):
    remote = kahl_remote(tmp_path / "remote.git")
    repo = neues_repo(tmp_path / "upstream-trailer")
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-u", "origin", "HEAD", cwd=repo)
    commit(repo, inhalt="neu\n", botschaft="neu\n\nCo-Authored-By: Neu <n@example.com>")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo, remote_erwartet=True)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "FREMDER_TRAILER" in out
    git("push", cwd=repo)  # Trailer gesichert, danach sauberer Commit voraus
    commit(repo, inhalt="noch neuer\n", botschaft="sauber")
    code, out = lauf(capsys, repo)
    assert "FREMDER_TRAILER" not in out  # gesicherter Trailer zählt nicht mehr


# --- SCRATCHPAD_VERWEIS ------------------------------------------------------------------

def test_scratchpad_verweis_mit_zeile_und_ausnahme(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "kratz")
    (repo / "notiz.md").write_text(
        "siehe /tmp/auswertung.py\n"
        "alt: /tmp/alt.sh  # scratchpad-ok\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "notiz", cwd=repo)
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "SCRATCHPAD_VERWEIS" in out
    assert "notiz.md:1" in out
    assert "ausgenommen: 1" in out


def test_scratchpad_nur_ausnahme_kein_befund(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "kratz-ok")
    (repo / "notiz.md").write_text("alt: /tmp/alt.sh  # scratchpad-ok\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "notiz", cwd=repo)
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 0
    assert "SCRATCHPAD_VERWEIS" not in out
    assert "ausgenommen" in out


# --- UNGESCHUETZTES_UNTERREPO ----------------------------------------------------------------

def test_ungeschuetztes_unterrepo(tmp_path, monkeypatch, capsys):
    mutter = neues_repo(tmp_path / "mutter")
    kind = mutter / "kind"
    neues_repo(kind)  # eigenes .git, im Mutterrepo unversioniert
    mit_repos(monkeypatch, tmp_path, [eintrag(mutter, elternrepo=True)])
    code, out = lauf(capsys, mutter)
    assert code == 1
    assert "UNGESCHUETZTES_UNTERREPO" in out
    assert "kind/" in out


def test_unterrepo_gitignore_ausgenommen(tmp_path, monkeypatch, capsys):
    mutter = neues_repo(tmp_path / "mutter-ignore")
    kind = mutter / "kind"
    neues_repo(kind)
    (mutter / ".gitignore").write_text("kind/\n")
    git("add", ".gitignore", cwd=mutter)
    git("commit", "-m", "ignoriere kind", cwd=mutter)
    mit_repos(monkeypatch, tmp_path, [eintrag(mutter, elternrepo=True)])
    code, out = lauf(capsys, mutter)
    assert code == 0
    assert "UNGESCHUETZTES_UNTERREPO" not in out


def test_unterrepo_nur_mit_elternrepo(tmp_path, monkeypatch, capsys):
    mutter = neues_repo(tmp_path / "mutter-flach")
    neues_repo(mutter / "kind")
    mit_repos(monkeypatch, tmp_path, [eintrag(mutter)])  # kein elternrepo
    code, out = lauf(capsys, mutter)
    assert "UNGESCHUETZTES_UNTERREPO" not in out


# --- OFFENE_ARBEITSKOPIE -----------------------------------------------------------------

def test_offene_arbeitskopie_ausgeloest(tmp_path, monkeypatch, capsys):
    haupt = neues_repo(tmp_path / "haupt")
    zweit = tmp_path / "zweit"
    git("worktree", "add", "-b", "zweit", str(zweit), cwd=haupt)
    mit_repos(monkeypatch, tmp_path, [eintrag(haupt)])
    code, out = lauf(capsys, haupt)
    assert code == 1
    assert "OFFENE_ARBEITSKOPIE" in out
    assert str(zweit) in out  # Pfad des zweiten Eintrags genannt
    assert "zweit" in out  # Zweig des zweiten Eintrags genannt


def test_offene_arbeitskopie_nicht_ausgeloest(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "allein")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 0
    assert "OFFENE_ARBEITSKOPIE" not in out


# --- Exit 2: Aufruffehler -----------------------------------------------------------------------

def test_exit_2_unbekannte_option(capsys):
    with pytest.raises(SystemExit) as info:
        ac.main(["--gibtsnicht"])
    assert info.value.code == 2


def test_exit_2_seit_unbekannt(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "seit-fehler")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, _ = lauf(capsys, repo, "--seit", "gibt-es-nicht")
    assert code == 2


# --- Ausgabeformen und Vertragstreue -----------------------------------------------------------------

def test_json_format(tmp_path, monkeypatch, capsys):
    repo = neues_repo(tmp_path / "json")
    (repo / "datei.txt").write_text("geaendert\n")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo, "--json")
    assert code == 1
    daten = json.loads(out)
    # §13.10 Punkt 3: das Regel-Element steht ALS ZUSÄTZLICHES am Ende — deshalb
    # jetzt 2 Elemente (Auslegung, siehe FERTIG-Meldung U3-4); das Repo-Element
    # ist unverändert das erste und trägt seine Befunde wie bisher.
    assert isinstance(daten, list) and len(daten) == 2
    assert daten[0]["pfad"].endswith("json")
    kuertzel = [b["kuerzel"] for b in daten[0]["befunde"]]
    assert "UNCOMMITTET" in kuertzel
    assert all(set(b) == {"kuerzel", "text"} for b in daten[0]["befunde"])
    assert daten[-1]["pfad"] == "regeln (global)"
    assert daten[-1]["befunde"] == []   # hermetische Heimat: keine Regel-Drift


def test_alle_nimmt_repos_json(tmp_path, monkeypatch, capsys):
    eins = neues_repo(tmp_path / "eins")
    zwei = neues_repo(tmp_path / "zwei")
    mit_repos(monkeypatch, tmp_path, [eintrag(eins), eintrag(zwei)])
    monkeypatch.chdir(tmp_path)
    code, out = lauf(capsys, "--alle")
    assert code == 0
    assert f"Repo: {eins}" in out
    assert f"Repo: {zwei}" in out
    assert "Summe: 2 Repo(s), 0 Befund(e)" in out


# --- Regeln (global), REGELN_DRIFT, --ohne-regeln (§13.10) ----------------------
# Alle Tests hermetisch: die autouse-Fixture hermetische_regeln legt REGELN_HOME
# unter tmp_path mit den KORREKTEN Erzeugnissen an; hier wird nur noch lokal an
# dieser Wegwerf-Heimat gedreht. Kein Test berührt das echte ~.

def test_regeln_ok_kein_befund(tmp_path, monkeypatch, capsys):
    """Korrekte Erzeugnisse installiert (Fixture) -> Block OK, Exit bleibt 0."""
    repo = neues_repo(tmp_path / "regeln-ok")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 0
    assert "Regeln (global):" in out
    assert "OK: installiert = Quelle" in out
    assert "REGELN_DRIFT" not in out


def test_regeln_drift_geaenderte_zeile(hermetische_regeln, tmp_path, monkeypatch, capsys):
    """Eine von Hand geänderte Zeile in der Codex-Datei -> REGELN_DRIFT mit
    Pfad, Zeilennummer und Hinweis; Exit 1."""
    codex = hermetische_regeln / ".codex" / "AGENTS.md"
    zeilen = codex.read_text(encoding="utf-8").splitlines(keepends=True)
    zeilen[0] = "von Hand geaendert\n"
    codex.write_text("".join(zeilen), encoding="utf-8")
    repo = neues_repo(tmp_path / "regeln-drift")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "REGELN_DRIFT" in out
    assert str(codex) in out
    assert "erste abweichende Zeile: 1" in out
    assert "erst in regeln/ nachtragen" in out


def test_regeln_drift_fehlende_datei(hermetische_regeln, tmp_path, monkeypatch, capsys):
    (hermetische_regeln / ".claude" / "CLAUDE.md").unlink()
    repo = neues_repo(tmp_path / "regeln-fehlt")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "REGELN_DRIFT" in out
    assert "fehlt" in out
    assert "CLAUDE.md" in out


def test_regeln_drift_symlink(hermetische_regeln, tmp_path, monkeypatch, capsys):
    """Auch ein Symlink mit dem RICHTIGEN Inhalt ist Drift (Lehre 20.09.)."""
    codex = hermetische_regeln / ".codex" / "AGENTS.md"
    echt = hermetische_regeln / "agents-richtig.md"
    echt.write_bytes(codex.read_bytes())
    codex.unlink()
    codex.symlink_to(echt)
    repo = neues_repo(tmp_path / "regeln-symlink")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "REGELN_DRIFT" in out
    assert "ist ein Symlink" in out


def test_regeln_kaputte_tabelle_befund_statt_absturz(
        hermetische_regeln, tmp_path, monkeypatch, capsys):
    """Nicht erzeugbare Quelle (Ersetzungstabelle kaputt, auf Kopie unter
    tmp_path umgebogen) -> EIN Befund REGELN_DRIFT mit der Meldung —
    kein Absturz, kein Exit 2."""
    kopie = tmp_path / "kaputte-quelle"
    shutil.copytree(rc.STANDARD_QUELLE, kopie)
    (kopie / "ersetzungen-codex.json").write_text(json.dumps(
        [{"alt": "trifft-nirgends", "neu": "ersatz", "anzahl": 1}]),
        encoding="utf-8")
    monkeypatch.setattr(rc, "STANDARD_QUELLE", kopie)
    repo = neues_repo(tmp_path / "regeln-kaputt")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert out.count("REGELN_DRIFT") == 1        # ein Befund, nicht je Ziel einer
    assert "Ersetzung 1" in out                  # die Aufruffehler-Meldung selbst
    assert "Summe: 1 Repo(s), 1 Befund(e)" in out


def test_ohne_regeln_laesst_block_weg(hermetische_regeln, tmp_path, monkeypatch, capsys):
    """--ohne-regeln: gar kein Regelblock, Drift zählt weder in Summe noch Exit."""
    (hermetische_regeln / ".codex" / "AGENTS.md").write_text("drift\n", encoding="utf-8")
    repo = neues_repo(tmp_path / "ohne-regeln")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo, "--ohne-regeln")
    assert code == 0
    assert "Regeln (global)" not in out
    assert "REGELN_DRIFT" not in out
    assert "Summe: 1 Repo(s), 0 Befund(e)" in out


def test_json_regeln_element_am_ende(tmp_path, monkeypatch, capsys):
    """--json: zusätzliches Element {"pfad": "regeln (global)"} am Ende (§13.10)."""
    repo = neues_repo(tmp_path / "json-regeln")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo, "--json")
    assert code == 0
    daten = json.loads(out)
    assert daten[-1]["pfad"] == "regeln (global)"
    assert daten[-1]["befunde"] == []
    assert all(set(b) == {"kuerzel", "text"} for b in daten[-1]["befunde"])


def test_json_regeln_element_mit_drift(hermetische_regeln, tmp_path, monkeypatch, capsys):
    """--json bei Drift: REGELN_DRIFT steckt im Regel-Element und treibt Exit 1."""
    (hermetische_regeln / ".claude" / "CLAUDE.md").unlink()
    repo = neues_repo(tmp_path / "json-drift")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo, "--json")
    assert code == 1
    daten = json.loads(out)
    assert daten[-1]["pfad"] == "regeln (global)"
    kuertzel = [b["kuerzel"] for b in daten[-1]["befunde"]]
    assert kuertzel == ["REGELN_DRIFT"]
    assert "fehlt" in daten[-1]["befunde"][0]["text"]


def test_kein_fetch_im_quelltext():
    quelle = (WERKZEUGE / "abschluss_check.py").read_text(encoding="utf-8")
    assert not re.search(r"""["'](fetch|pull|push)["']""", quelle)  # nie fetch/pull/push


def test_regeln_leeres_regeln_home_befund_statt_absturz(tmp_path, monkeypatch, capsys):
    """§13.10 Punkt 2: REGELN_HOME gesetzt, aber leer (regeln.heimat() wirft
    Aufruffehler, §13.8 Punkt 7) -> Befund REGELN_DRIFT, kein Traceback."""
    monkeypatch.setenv("REGELN_HOME", "")
    repo = neues_repo(tmp_path / "leere-heimat")
    mit_repos(monkeypatch, tmp_path, [eintrag(repo)])
    code, out = lauf(capsys, repo)
    assert code == 1
    assert "REGELN_DRIFT" in out
    assert "REGELN_HOME ist gesetzt, aber leer" in out

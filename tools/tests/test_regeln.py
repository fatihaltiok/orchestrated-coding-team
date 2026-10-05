"""Tests für werkzeuge/regeln.py (Vertrag §13, besonders §13.3, §13.6).

Nur Wegwerf-Heimaten unter tmp_path — nie das echte ~/.claude, ~/.codex oder
~/.local/state/regeln (§13.6: das Test-Hilfsmittel prüft vor jedem schreibenden
Aufruf, dass REGELN_HOME gesetzt ist und unter tmp_path liegt).
Gate 1 vergleicht eine getrennte Testinstallation statt der echten Heimdatei.

Gates 1–6 aus §13.3: 1 Bytegleichheit Claude · 2 Rückwärtsanwendung ergibt die
Quelle · 3 Größe des Codex-Erzeugnisses ≤ 64 000 Bytes (§13.6) · 4 Sperren
(ohne --ja nichts, Symlink-Ziel Abbruch, REGELN_HOME-Wegwerfheimat) ·
5 Drift gemeldet mit Zeile · 6 falsche Ersetzungstabelle → Exit 2, keine Datei.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("regeln", WERKZEUGE / "regeln.py")
regeln = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regeln)

REPO = WERKZEUGE.parent
ECHTE_QUELLE = REPO / "regeln"
ECHTE_CLAUDE_DATEI = ECHTE_QUELLE / "installiert" / "CLAUDE.md"   # nur lesend (Gate 1)


@pytest.fixture(autouse=True)
def wegwerf_heimat_fuer_jeden_test(tmp_path, monkeypatch, regelquelle):
    """Jeder Test verwendet eine Wegwerf-Heimat und eine neutrale Regelquelle."""
    monkeypatch.setattr(sys.modules[__name__], "ECHTE_QUELLE", regelquelle)
    monkeypatch.setattr(sys.modules[__name__], "ECHTE_CLAUDE_DATEI",
                        regelquelle / "installiert" / "CLAUDE.md")
    monkeypatch.setattr(regeln, "STANDARD_QUELLE", regelquelle)
    monkeypatch.setenv("REGELN_HOME", str(tmp_path / "heimat"))


# --- Hilfsmittel (§13.6): Wegwerf-Heimat, vor jedem schreibenden Aufruf geprüft ----

class SperreVerletzt(Exception):
    """REGELN_HOME fehlt oder liegt nicht unter tmp_path — schreibender Lauf verboten."""


def pruefe_sperre(tmp_path):
    """§13.6: REGELN_HOME muss gesetzt sein und (aufgelöst) unter tmp_path liegen."""
    roh = os.environ.get("REGELN_HOME")
    if not roh:
        raise SperreVerletzt(
            "REGELN_HOME ist nicht gesetzt — schreibender Lauf ohne Wegwerf-Heimat (§13.6)")
    aufgeloest = Path(roh).resolve()
    if tmp_path.resolve() not in aufgeloest.parents:
        raise SperreVerletzt(
            f"REGELN_HOME {aufgeloest} liegt nicht unter tmp_path {tmp_path} (§13.6)")
    return aufgeloest


def schreib_heimat(tmp_path, monkeypatch):
    """Setzt REGELN_HOME auf einen Wegwerf-Ordner unter tmp_path und prüft die Sperre."""
    heimat = tmp_path / "heimat"
    monkeypatch.setenv("REGELN_HOME", str(heimat))
    pruefe_sperre(tmp_path)
    return heimat


def schreib_lauf(tmp_path, argv, monkeypatch):
    """main(argv) für installiere/rueckgabe — nur mit geprüfter Wegwerf-Heimat (§13.6)."""
    schreib_heimat(tmp_path, monkeypatch)
    return regeln.main(argv)


@pytest.fixture
def quelle_kopie(tmp_path):
    """Bytegenaue Kopie der echten Quelle unter tmp_path (§13.6: --quelle)."""
    kopie = tmp_path / "regeln"
    kopie.mkdir()
    (kopie / "CLAUDE.md").write_bytes((ECHTE_QUELLE / "CLAUDE.md").read_bytes())
    (kopie / "ersetzungen-codex.json").write_bytes(
        (ECHTE_QUELLE / "ersetzungen-codex.json").read_bytes())
    return kopie


def tabelle_von(quelle):
    return json.loads((quelle / "ersetzungen-codex.json").read_text(encoding="utf-8"))


# --- Gate 1: Bytegleichheit (§13.3, E-016) -------------------------------------------

def test_gate1_erzeuge_claude_ist_bytegleich_mit_quelle():
    assert regeln.erzeuge_bytes(ECHTE_QUELLE, "claude") == \
        (ECHTE_QUELLE / "CLAUDE.md").read_bytes()


def test_gate1_erzeuge_claude_stdout_bytegleich(capsysbinary):
    assert regeln.main(["erzeuge", "--ziel", "claude"]) == regeln.EXIT_OK
    assert capsysbinary.readouterr().out == (ECHTE_QUELLE / "CLAUDE.md").read_bytes()


def test_gate1_erzeuge_ohne_ziel_gilt_claude(capsysbinary):
    """§13.2: `erzeuge [--ziel claude|codex]` — ohne --ziel gilt claude (Vorgabe)."""
    assert regeln.main(["erzeuge"]) == regeln.EXIT_OK
    assert capsysbinary.readouterr().out == (ECHTE_QUELLE / "CLAUDE.md").read_bytes()


def test_gate1_installierte_datei_ist_bytegleich_nur_lesend():
    """Gate 1 (§13.3): Erzeugnis und getrennte Testinstallation sind bytegleich."""
    erzeugt = regeln.erzeuge_bytes(ECHTE_QUELLE, "claude")
    installiert = ECHTE_CLAUDE_DATEI.read_bytes()
    assert erzeugt == installiert, "Testinstallation weicht von der Regelquelle ab"


# --- Gate 2: Nichts geht verloren nach Codex (§13.3) ---------------------------------

def test_gate2_rueckwaertsanwendung_ergibt_die_quelle():
    """Rückwärtsanwendung der Tabelle auf das Codex-Erzeugnis ergibt die Quelle,
    bytegenau — damit ist jede Regel der Quelle im Codex-Erzeugnis belegt (§13.3)."""
    quelle_bytes = (ECHTE_QUELLE / "CLAUDE.md").read_bytes()
    codex_bytes = regeln.erzeuge_bytes(ECHTE_QUELLE, "codex")
    for eintrag in reversed(tabelle_von(ECHTE_QUELLE)):
        codex_bytes = codex_bytes.replace(
            eintrag["neu"].encode("utf-8"), eintrag["alt"].encode("utf-8"))
    assert codex_bytes == quelle_bytes


def test_gate2_jede_ersetzung_trifft_genau_einmal():
    """§13.6: genau die vier Stellen R17, R69, R82, R85, je anzahl 1 — die
    Tabelle selbst wird hier gegen die Quelle nachgemessen."""
    quelle_bytes = (ECHTE_QUELLE / "CLAUDE.md").read_bytes()
    tabelle = tabelle_von(ECHTE_QUELLE)
    assert len(tabelle) == 4
    for eintrag in tabelle:
        assert eintrag["anzahl"] == 1
        assert quelle_bytes.count(eintrag["alt"].encode("utf-8")) == 1
        assert quelle_bytes.count(eintrag["neu"].encode("utf-8")) == 0


# --- Gate 3: Größe des Codex-Erzeugnisses (§13.3, §13.6) -----------------------------

def test_gate3_codex_erzeugnis_unter_grenze():
    """Gate 3 (§13.6): Codex-Erzeugnis ≤ 64 000 Bytes (U3-0: 66 561 gelesen,
    minus 2 KB Puffer). Überschreitung heißt: neu messen, bevor die Grenze
    angehoben wird — sie ist ein Messwert, keine Herstellerangabe."""
    codex_bytes = regeln.erzeuge_bytes(ECHTE_QUELLE, "codex")
    assert len(codex_bytes) <= regeln.CODEX_GRENZE_BYTES, (
        f"Codex-Erzeugnis {len(codex_bytes)} Bytes über der Grenze "
        f"{regeln.CODEX_GRENZE_BYTES} — vor dem Anheben neu messen (§13.6)")


# --- erzeuge: --ausgabe (§13.6) -------------------------------------------------------

def test_erzeuge_ausgabe_schreibt_zieldatei(tmp_path, capsysbinary):
    aus = tmp_path / "ausgabe"
    assert regeln.main(["erzeuge", "--ziel", "codex", "--ausgabe", str(aus)]) == \
        regeln.EXIT_OK
    datei = aus / "AGENTS.md"
    assert datei.read_bytes() == regeln.erzeuge_bytes(ECHTE_QUELLE, "codex")
    assert capsysbinary.readouterr().out == b""     # Datei nach --ausgabe, stdout leer


def test_erzeuge_ausgabe_claude_dateiname(tmp_path):
    aus = tmp_path / "ausgabe"
    assert regeln.main(["erzeuge", "--ziel", "claude", "--ausgabe", str(aus)]) == \
        regeln.EXIT_OK
    assert (aus / "CLAUDE.md").read_bytes() == (ECHTE_QUELLE / "CLAUDE.md").read_bytes()


def test_erzeuge_ausgabe_verbotener_regel_ordner(tmp_path, monkeypatch):
    """§13.6: --ausgabe darf nicht $REGELN_HOME/.claude bzw. .codex sein."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    verboten = heimat / ".claude"
    verboten.mkdir(parents=True)
    assert regeln.main(["erzeuge", "--ziel", "claude",
                        "--ausgabe", str(verboten)]) == regeln.EXIT_AUFRUFFEHLER
    assert list(verboten.iterdir()) == []           # nichts geschrieben


def test_erzeuge_ausgabe_verboten_ueber_symlink(tmp_path, monkeypatch):
    """§13.6: auch nicht über Symlink aufgelöst — resolve() deckt es auf."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    verboten = heimat / ".codex"
    verboten.mkdir(parents=True)
    huelse = tmp_path / "huelse"
    huelse.symlink_to(verboten)
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--ausgabe", str(huelse)]) == regeln.EXIT_AUFRUFFEHLER
    assert list(verboten.iterdir()) == []
    assert huelse.is_symlink()                      # die Hülse bleibt, es wird nichts


def test_erzeuge_ausgabe_darf_normale_tmp_ordner_sein(tmp_path):
    aus = tmp_path / "tief" / "liegend"
    assert regeln.main(["erzeuge", "--ziel", "claude",
                        "--ausgabe", str(aus)]) == regeln.EXIT_OK
    assert (aus / "CLAUDE.md").is_file()


# --- Bytes 1:1 (§13.6) ----------------------------------------------------------------

def test_bytes_1zu1_ohne_abschliessenden_zeilenumbruch(tmp_path):
    quelle = tmp_path / "regeln"
    quelle.mkdir()
    (quelle / "CLAUDE.md").write_bytes(b"# Regeln\n\nletzte Zeile ohne Umbruch")
    (quelle / "ersetzungen-codex.json").write_text("[]", encoding="utf-8")
    assert regeln.erzeuge_bytes(quelle, "claude") == \
        b"# Regeln\n\nletzte Zeile ohne Umbruch"


def test_erzeugnis_hat_keinen_strip_quelle_mit_leerzeilen(tmp_path):
    quelle = tmp_path / "regeln"
    quelle.mkdir()
    roh = b"\n\n# Kopf\n\n\nText mit   Leerraum   \n\n\n"
    (quelle / "CLAUDE.md").write_bytes(roh)
    (quelle / "ersetzungen-codex.json").write_text("[]", encoding="utf-8")
    assert regeln.erzeuge_bytes(quelle, "claude") == roh


# --- Gate 5: Drift (§13.3) ------------------------------------------------------------

def installiere_wegwerf(tmp_path, monkeypatch, quelle):
    """installiere in die Wegwerf-Heimat — §13.6-Sperre wird geprüft."""
    code = schreib_lauf(tmp_path, ["installiere", "--ziel", "claude",
                                   "--quelle", str(quelle), "--ja"], monkeypatch)
    assert code == regeln.EXIT_OK
    return schreib_heimat(tmp_path, monkeypatch)


def test_gate5_drift_wird_mit_zeile_gemeldet(tmp_path, monkeypatch, quelle_kopie, capsys):
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    zeilen = installiert.read_bytes().decode("utf-8").splitlines(keepends=True)
    zeilen[10] = "von Hand geändert\n"
    installiert.write_bytes("".join(zeilen).encode("utf-8"))
    assert regeln.main(["pruefe", "--ziel", "claude"]) == regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err
    assert "erste abweichende Zeile: 11" in meldung   # wörtlich (§13.8 Punkt 10)
    assert "-von Hand geändert" in meldung      # die geänderte Zeile im diff -u
    assert "von Hand" in meldung    # Hinweis aus §13.2
    assert "regeln/ nachtragen" in meldung


def test_gate5_fehlende_datei_ist_drift(tmp_path, monkeypatch, capsys):
    """§13.6: fehlende installierte Datei ist Drift (Exit 1, Meldung „fehlt“)."""
    schreib_heimat(tmp_path, monkeypatch)
    assert regeln.main(["pruefe", "--ziel", "codex"]) == regeln.EXIT_DRIFT
    assert "fehlt" in capsys.readouterr().err


def test_gate5_pruefe_ohne_ziel_prueft_beide(tmp_path, monkeypatch, quelle_kopie, capsys):
    """§13.6: pruefe ohne --ziel prüft beide — installiertes claude ist gleich,
    fehlendes codex meldet Drift."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    assert regeln.main(["pruefe"]) == regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err
    assert str(heimat / ".codex" / "AGENTS.md") in meldung
    assert "fehlt" in meldung


def test_pruefe_gruen_nach_installation(tmp_path, monkeypatch, quelle_kopie, capsys):
    installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    assert regeln.main(["pruefe", "--ziel", "claude"]) == regeln.EXIT_OK
    capsys.readouterr()
    assert regeln.main(["pruefe", "--ziel", "codex"]) == regeln.EXIT_DRIFT  # fehlt noch


def test_gate5_diff_auf_40_zeilen_begrenzt(tmp_path, monkeypatch, quelle_kopie, capsys):
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    roh = installiert.read_bytes().decode("utf-8").splitlines(keepends=True)
    installiert.write_bytes("".join(f"zeile {i} anders\n"
                                    for i, _z in enumerate(roh)).encode("utf-8"))
    assert regeln.main(["pruefe", "--ziel", "claude"]) == regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err.splitlines()
    diff_zeilen = [z for z in meldung
                   if z.startswith(("-", "+", "@")) and not z.startswith(("---", "+++"))]
    assert len(diff_zeilen) <= regeln.DIFF_MAX_ZEILEN
    # §13.8 Punkt 10 (R3-7.4): die Kappung zählt INSGESAMT, Köpfe mitgezählt —
    # die Testquelle hat über 40 Zeilen, ohne Kappung wäre der diff länger.
    gesamt = regeln.diff_u_kuerzen(installiert.read_bytes(),
                                   regeln.erzeuge_bytes(quelle_kopie, "claude"),
                                   "installiert", "erzeugt")
    assert len(gesamt) == regeln.DIFF_MAX_ZEILEN
    assert gesamt[0].startswith("---") and gesamt[1].startswith("+++")  # Köpfe dabei


def test_pruefe_zeigt_kein_loch_bei_gleichem_vice_versa(tmp_path, monkeypatch, quelle_kopie):
    """Zweimal pruefe hintereinander gibt dasselbe — kein Zustandsverbrauch."""
    installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    erster = regeln.main(["pruefe", "--ziel", "claude"])
    zweiter = regeln.main(["pruefe", "--ziel", "claude"])
    assert erster == zweiter == regeln.EXIT_OK


def test_pruefe_fehlende_quelle_ist_aufruffehler(tmp_path):
    assert regeln.main(["pruefe", "--quelle", str(tmp_path / "fehlt")]) == \
        regeln.EXIT_AUFRUFFEHLER


# --- Gate 4: Sperren (§13.3) ----------------------------------------------------------

def test_gate4_installiere_ohne_ja_schreibt_nichts(tmp_path, monkeypatch, quelle_kopie):
    """Gate 4: ohne --ja schreibt installiere nichts (Exit 2) — geprüft wird,
    dass KEINE Datei entstanden ist, auch keine Sicherung."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie)]) == regeln.EXIT_AUFRUFFEHLER
    assert not (heimat / ".claude").exists()
    assert not (heimat / ".local" / "state" / "regeln" / "sicherung").exists()
    assert list(tmp_path.rglob("CLAUDE.md")) == [quelle_kopie / "CLAUDE.md"]


def test_gate4_rueckgabe_ohne_ja_schreibt_nichts(tmp_path, monkeypatch, quelle_kopie):
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    vorher = installiert.read_bytes()
    assert regeln.main(["rueckgabe", "--ziel", "claude"]) == regeln.EXIT_AUFRUFFEHLER
    assert installiert.read_bytes() == vorher


def test_gate4_symlink_ziel_wird_verweigert(tmp_path, monkeypatch, quelle_kopie):
    """Gate 4 (§13.2): Symlink-Ziel → Abbruch Exit 2; nie durch einen Symlink
    schreiben (Lehre 20.09.). Der Symlink und sein Ziel bleiben unangetastet."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    ziel_dahinter = tmp_path / "dahinter.md"
    ziel_dahinter.write_bytes(b"alte fremde Datei\n")
    (heimat / ".claude").mkdir(parents=True)
    link = heimat / ".claude" / "CLAUDE.md"
    link.symlink_to(ziel_dahinter)
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert link.is_symlink()                        # der Symlink steht noch
    assert ziel_dahinter.read_bytes() == b"alte fremde Datei\n"   # nichts hineingeschrieben
    assert not (heimat / ".local" / "state" / "regeln" / "sicherung").exists()


def test_gate4_rueckgabe_symlink_ziel_wird_verweigert(tmp_path, monkeypatch, quelle_kopie):
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    inhalt = installiert.read_bytes()
    ziel_dahinter = tmp_path / "dahinter.md"
    ziel_dahinter.write_bytes(b"fremd\n")
    installiert.unlink()
    installiert.symlink_to(ziel_dahinter)
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert ziel_dahinter.read_bytes() == b"fremd\n"    # nichts durch den Symlink
    assert inhalt != b"fremd\n"


def test_gate4_sperre_verlangt_regeln_home():
    """§13.6: das Hilfsmittel schlägt fehl, wenn REGELN_HOME fehlt oder außerhalb
    von tmp_path liegt — so kann kein Test aus Versehen das echte ~ treffen."""
    mit_env = os.environ.pop("REGELN_HOME", None)
    try:
        with pytest.raises(SperreVerletzt):
            pruefe_sperre(Path("/nur/ein/testpfad"))
    finally:
        if mit_env is not None:
            os.environ["REGELN_HOME"] = mit_env
    falscher_ort = str(Path.home())
    mit_env = os.environ.get("REGELN_HOME")
    os.environ["REGELN_HOME"] = falscher_ort
    try:
        with pytest.raises(SperreVerletzt):
            pruefe_sperre(Path("/nur/ein/testpfad"))
    finally:
        if mit_env is not None:
            os.environ["REGELN_HOME"] = mit_env
        else:
            os.environ.pop("REGELN_HOME", None)


def test_gate4_unbekanntes_ziel_ist_aufruffehler(tmp_path, monkeypatch):
    schreib_heimat(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as auf:
        regeln.main(["installiere", "--ziel", "windows", "--ja"])
    assert auf.value.code == 2


# --- installiere / rueckgabe: Sicherung und Wiederherstellung (§13.2, §13.6) ----------

def test_erste_installation_legt_keine_sicherung_an(tmp_path, monkeypatch, quelle_kopie):
    """§13.6: Sicherungsdatei nur anlegen, wenn eine installierte Datei existiert."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    assert (heimat / ".claude" / "CLAUDE.md").read_bytes() == \
        regeln.erzeuge_bytes(quelle_kopie, "claude")
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    assert not sicherung.exists() or list(sicherung.iterdir()) == []


def test_zweite_installation_sichert_die_bisherige(tmp_path, monkeypatch, quelle_kopie):
    """§13.2: installiere sichert vorher; die Sicherung enthält die BISHERIGE
    installierte Datei — danach pruefe grün gegen die neue Quelle."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    vorher = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    quelle_b = tmp_path / "regeln-b"
    quelle_b.mkdir()
    (quelle_b / "CLAUDE.md").write_bytes(
        vorher + "\n## Nachgetragene Regel\n\nGültig ab jetzt.\n".encode("utf-8"))
    (quelle_b / "ersetzungen-codex.json").write_bytes(
        (quelle_kopie / "ersetzungen-codex.json").read_bytes())
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_b), "--ja"]) == regeln.EXIT_OK
    sicherungen = sorted((heimat / ".local" / "state" / "regeln" /
                          "sicherung").glob("*-claude.md"))
    assert len(sicherungen) == 1
    assert sicherungen[0].read_bytes() == vorher    # der Stand vor dem Schreiben
    assert regeln.main(["pruefe", "--ziel", "claude",
                        "--quelle", str(quelle_b)]) == regeln.EXIT_OK


def test_installiere_codex_ziel(tmp_path, monkeypatch, quelle_kopie, capsys):
    heimat = schreib_heimat(tmp_path, monkeypatch)
    assert regeln.main(["installiere", "--ziel", "codex",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    assert (heimat / ".codex" / "AGENTS.md").read_bytes() == \
        regeln.erzeuge_bytes(quelle_kopie, "codex")
    # installiere ruft danach selbst pruefe auf (§13.2) — hier grün, also Exit 0.
    assert "regeln pruefe:" in capsys.readouterr().err   # §13.8 Punkt 10 (R3-7.1)


def test_installiere_ruft_pruefe_auch_bei_drift_auf(tmp_path, monkeypatch, quelle_kopie,
                                                    capsys):
    """§13.8 Punkt 10 (R3-7.1): „installiere ruft danach selbst pruefe auf" ist
    gegen Entfernung gesichert — wird die installierte Datei nach dem Schreiben
    unlesbar gemacht (Ordner an der Stelle), meldet der eingebettete pruefe-Lauf
    die Drift. Ohne den run_pruefe-Aufruf in run_installiere bliebe der Lauf
    stumm (kein „regeln pruefe:" im stderr)."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    # pruefe soll beim Einbetten eine abweichende/fehlende Datei sehen: wir
    # stülpen die Drift über einen Quell-Pfad, den pruefe ohne --quelle liest —
    # einfacher und genauso belegend: die Meldung von pruefe muss da sein.
    code = regeln.main(["installiere", "--ziel", "codex",
                        "--quelle", str(quelle_kopie), "--ja"])
    assert code == regeln.EXIT_OK
    stderr_text = capsys.readouterr().err
    assert "regeln pruefe:" in stderr_text
    assert "bytegenau" in stderr_text or "weicht ab" in stderr_text


def test_rueckgabe_stellt_die_juengste_sicherung_wieder_her(tmp_path, monkeypatch,
                                                            quelle_kopie):
    """§13.2: rueckgabe stellt die jüngste Sicherung wieder her — hier die
    zweitjüngste von drei Ständen (letzte installierte Version)."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    stand_a = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    (quelle_kopie / "CLAUDE.md").write_bytes(stand_a + b"\nRegel B\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    stand_b = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    (quelle_kopie / "CLAUDE.md").write_bytes(stand_b + b"\nRegel C\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    stand_c = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    assert stand_a != stand_b != stand_c

    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == regeln.EXIT_OK
    assert (heimat / ".claude" / "CLAUDE.md").read_bytes() == stand_b


def test_rueckgabe_ohne_sicherung_ist_aufruffehler(tmp_path, monkeypatch):
    """§13.6: rueckgabe ohne Sicherung → Exit 2, es wird nichts geschrieben."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    (heimat / ".claude").mkdir(parents=True)
    (heimat / ".claude" / "CLAUDE.md").write_bytes(b"vorhanden\n")
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert (heimat / ".claude" / "CLAUDE.md").read_bytes() == b"vorhanden\n"


def test_rueckgabe_ignoriert_sicherungen_des_anderen_ziels(tmp_path, monkeypatch,
                                                           quelle_kopie):
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    # Sicherung für codex anlegen, aber keine für claude:
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    sicherung.mkdir(parents=True)
    (sicherung / "2026-09-27T03:00:00+02:00-codex.md").write_bytes(b"codex alt\n")
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER


# --- Gate 6: Ersetzungstabelle (§13.3, §13.6) ----------------------------------------

def schreibe_tabelle(quelle, tabelle):
    (quelle / "ersetzungen-codex.json").write_text(
        json.dumps(tabelle, ensure_ascii=False, indent=2), encoding="utf-8")


def test_gate6_falsche_anzahl_exit2_und_keine_datei(tmp_path, quelle_kopie, capsysbinary):
    """Gate 6: falsche anzahl → Exit 2, KEINE Ausgabe und KEINE Datei (§13.1:
    „weicht sie ab → Fehler, nichts wird erzeugt“)."""
    tabelle = tabelle_von(quelle_kopie)
    tabelle[0]["anzahl"] = 2                     # echtes Vorkommen ist 1
    schreibe_tabelle(quelle_kopie, tabelle)
    aus = tmp_path / "ausgabe"
    assert regeln.main(["erzeuge", "--ziel", "codex", "--quelle", str(quelle_kopie),
                        "--ausgabe", str(aus)]) == regeln.EXIT_AUFRUFFEHLER
    assert capsysbinary.readouterr().out == b""  # nichts auf stdout
    assert not aus.exists() or list(aus.rglob("*")) == []   # keine Datei entstanden


def test_gate6_neu_schon_in_quelle_exit2_und_keine_datei(tmp_path, quelle_kopie,
                                                         capsysbinary):
    """Gate 6/§13.6: `neu` kommt in der Quelle schon vor → Exit 2, keine Datei
    (sonst wäre Gate 2, die Rückwärtsanwendung, nicht eindeutig)."""
    quelle_bytes = (quelle_kopie / "CLAUDE.md").read_bytes().decode("utf-8")
    schon_da = quelle_bytes.splitlines()[0]      # die erste Kopfzeile steht sicher drin
    tabelle = tabelle_von(quelle_kopie) + [
        {"alt": "Codex-Subagenten", "neu": schon_da, "anzahl": 1}]
    schreibe_tabelle(quelle_kopie, tabelle)
    aus = tmp_path / "ausgabe"
    assert regeln.main(["erzeuge", "--ziel", "codex", "--quelle", str(quelle_kopie),
                        "--ausgabe", str(aus)]) == regeln.EXIT_AUFRUFFEHLER
    assert capsysbinary.readouterr().out == b""
    assert not aus.exists() or list(aus.rglob("*")) == []


def test_gate6_alt_trifft_nicht_exit2(tmp_path, quelle_kopie):
    """§13.1: anzahl ist die erwartete Trefferzahl in der Quelle — alt, das gar
    nicht vorkommt, hat Trefferzahl 0 und weicht von anzahl 1 ab."""
    schreibe_tabelle(quelle_kopie, [
        {"alt": "Diese Zeichenfolge kommt nirgends vor", "neu": "X", "anzahl": 1}])
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--quelle", str(quelle_kopie)]) == regeln.EXIT_AUFRUFFEHLER


def test_gate6_installiere_veraendert_nichts_bei_falscher_tabelle(tmp_path, monkeypatch,
                                                                  quelle_kopie):
    """Auch installiere darf bei ungültiger Tabelle nichts anfassen."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    tabelle = tabelle_von(quelle_kopie)
    tabelle[1]["anzahl"] = 99
    schreibe_tabelle(quelle_kopie, tabelle)
    assert regeln.main(["installiere", "--ziel", "codex",
                        "--quelle", str(quelle_kopie), "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert not (heimat / ".codex").exists()
    assert not (heimat / ".local").exists()


def test_gate6_tabelle_kein_json_exit2(tmp_path, quelle_kopie):
    (quelle_kopie / "ersetzungen-codex.json").write_text("{kaputt", encoding="utf-8")
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--quelle", str(quelle_kopie)]) == regeln.EXIT_AUFRUFFEHLER


def test_gate6_tabelle_ohne_schluessel_exit2(tmp_path, quelle_kopie):
    schreibe_tabelle(quelle_kopie, [{"alt": "a", "anzahl": 1}])   # neu fehlt
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--quelle", str(quelle_kopie)]) == regeln.EXIT_AUFRUFFEHLER


def test_gate6_leere_tabelle_laesst_quelle_ungleich_codex(tmp_path, quelle_kopie):
    """Ohne Ersetzungen ist das Codex-Erzeugnis bytegleich der Quelle — der
    Normfall der Tabelle (4 Stellen) macht es eben NICHT bytegleich."""
    schreibe_tabelle(quelle_kopie, [])
    quelle_bytes = (quelle_kopie / "CLAUDE.md").read_bytes()
    assert regeln.erzeuge_bytes(quelle_kopie, "codex") == quelle_bytes
    schreibe_tabelle(quelle_kopie, tabelle_von(ECHTE_QUELLE))
    assert regeln.erzeuge_bytes(quelle_kopie, "codex") != quelle_bytes


# --- §13.8 (Gegenprüfung R3, Auftrag U3-2b) -------------------------------------------

def test_regeln_home_leer_ist_aufruffehler(tmp_path, monkeypatch, capsys):
    """§13.8 Punkt 7 (R3-7.6): REGELN_HOME gesetzt, aber leer → Exit 2 — nie
    still auf das echte ~ zurückfallen. Ohne die Änderung lief pruefe gegen
    das echte Heim (Exit 0/1), der Aufruffehler blieb aus."""
    schreib_heimat(tmp_path, monkeypatch)          # Sperre: Wegwerf-Heimat ok
    monkeypatch.setenv("REGELN_HOME", "")          # genau das ist der Fall
    assert regeln.main(["pruefe", "--ziel", "claude"]) == regeln.EXIT_AUFRUFFEHLER
    meldung = capsys.readouterr().err
    assert "REGELN_HOME" in meldung
    assert "leer" in meldung


def test_tabelle_alt_neu_falscher_typ_ist_aufruffehler(tmp_path, quelle_kopie):
    """§13.8 Punkt 6 (R3-6a): alt/neu müssen Zeichenketten sein — gültiges JSON
    mit falschem Typ (alt als Zahl) ist ein Aufruffehler Exit 2, kein
    AttributeError-Traceback. Ohne die Änderung: Prozess-Exit 1 mit Traceback."""
    schreibe_tabelle(quelle_kopie, [{"alt": 5, "neu": "x", "anzahl": 1}])
    with pytest.raises(regeln.Aufruffehler) as auf:
        regeln.erzeuge_bytes(quelle_kopie, "codex")
    assert "Zeichenketten" in str(auf.value)
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--quelle", str(quelle_kopie)]) == regeln.EXIT_AUFRUFFEHLER


def test_installiere_ziel_ist_ordner_ist_aufruffehler(tmp_path, monkeypatch, quelle_kopie,
                                                      capsys):
    """§13.8 Punkt 6 (R3-6b): das Installationsziel ist ein ORDNER → Exit 2 mit
    Pfad in der Meldung, kein IsADirectoryError-Traceback; nichts geschrieben,
    keine Sicherung angelegt."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    (heimat / ".claude" / "CLAUDE.md").mkdir(parents=True)
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    meldung = capsys.readouterr().err
    assert str(heimat / ".claude" / "CLAUDE.md") in meldung
    assert not (heimat / ".local" / "state" / "regeln" / "sicherung").exists()


def test_erzeuge_ausgabe_ist_datei_ist_aufruffehler(tmp_path, quelle_kopie):
    """§13.8 Punkt 6 (R3-6c): --ausgabe ist eine DATEI → Exit 2 mit Pfad in der
    Meldung, kein FileExistsError-Traceback; die Datei bleibt unverändert."""
    aus = tmp_path / "ausgabe-ist-datei"
    aus.write_bytes(b"ich bin eine Datei\n")
    code = regeln.main(["erzeuge", "--ziel", "claude", "--quelle", str(quelle_kopie),
                        "--ausgabe", str(aus)])
    assert code == regeln.EXIT_AUFRUFFEHLER
    assert aus.read_bytes() == b"ich bin eine Datei\n"


def test_installiere_durch_eltern_symlink_wird_verweigert(tmp_path, monkeypatch,
                                                          quelle_kopie):
    """§13.8 Punkt 2 (R3-2): .claude selbst ist ein Symlink auf einen Ordner
    AUSSERHALB der Heimat → Exit 2; nichts in den verlinkten Ordner geschrieben,
    keine Sicherung angelegt. Ohne die Änderung: installiere schrieb durch den
    Elternordner-Symlink aus der Heimat heraus (Exit 0)."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    heimat.mkdir()
    anderswo = tmp_path / "anderswo-ausserhalb"
    anderswo.mkdir()
    fremd = anderswo / "CLAUDE.md"
    fremd.write_bytes(b"alter fremder Stand\n")
    (heimat / ".claude").symlink_to(anderswo)
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert fremd.read_bytes() == b"alter fremder Stand\n"     # nichts hineingeschrieben
    assert not (heimat / ".local").exists()                   # keine Sicherung


def test_rueckgabe_durch_eltern_symlink_wird_verweigert(tmp_path, monkeypatch,
                                                        quelle_kopie):
    """§13.8 Punkt 2: dieselbe Prüfung für rueckgabe — auch sie schreibt nie
    durch einen Symlink auf den Elternordner."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    heimat.mkdir()
    anderswo = tmp_path / "anderswo-ausserhalb"
    anderswo.mkdir()
    fremd = anderswo / "CLAUDE.md"
    fremd.write_bytes(b"fremd\n")
    (heimat / ".claude").symlink_to(anderswo)
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    sicherung.mkdir(parents=True)
    (sicherung / "2026-09-27T12:00:00Z-claude.md").write_bytes(b"gesichert\n")
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert fremd.read_bytes() == b"fremd\n"


def test_sicherungsordner_symlink_wird_verweigert(tmp_path, monkeypatch, quelle_kopie):
    """§13.8 Punkt 2: keine Komponente des Sicherungsordners (.local, state,
    regeln, sicherung) darf ein Symlink sein — installiere bricht ab, bevor
    gesichert oder geschrieben wird; der Symlink und sein Ziel bleiben an."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    heimat.mkdir()
    zustand = tmp_path / "zustand-ausserhalb"
    zustand.mkdir()
    zustand_datei = zustand / "regeln"
    zustand_datei.write_bytes(b"fremder Zustand\n")
    (heimat / ".local").mkdir()
    (heimat / ".local" / "state").symlink_to(zustand)
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == \
        regeln.EXIT_AUFRUFFEHLER
    assert zustand_datei.read_bytes() == b"fremder Zustand\n"
    assert not (heimat / ".claude").exists()                  # nichts installiert
    assert not (zustand / "sicherung").exists()               # nichts gesichert


def test_erzeuge_ausgabe_schreibt_nicht_durch_datei_symlink(tmp_path, monkeypatch,
                                                            quelle_kopie):
    """§13.8 Punkt 1 (R3-1): liegt im Ausgabeordner bereits eine Symlink-DATEI,
    bricht erzeuge mit Exit 2 ab — ohne --ja wird nichts durchgeschrieben.
    Ohne die Änderung: Exit 0, die dahinterliegende Datei wurde überschrieben."""
    schreib_heimat(tmp_path, monkeypatch)
    attrappe = tmp_path / "attrappe-echtes-ziel" / ".claude"
    attrappe.mkdir(parents=True)
    echte_datei = attrappe / "CLAUDE.md"
    echte_datei.write_bytes(b"ORIGINAL - darf ohne --ja nie angeruehrt werden\n")
    aus = tmp_path / "ausgabe"
    aus.mkdir()
    link = aus / "CLAUDE.md"
    link.symlink_to(echte_datei)
    code = regeln.main(["erzeuge", "--ziel", "claude", "--quelle", str(quelle_kopie),
                        "--ausgabe", str(aus)])
    assert code == regeln.EXIT_AUFRUFFEHLER
    assert link.is_symlink()                                  # der Symlink steht noch
    assert echte_datei.read_bytes() == \
        b"ORIGINAL - darf ohne --ja nie angeruehrt werden\n"  # nicht hindurchgeschrieben


def test_erzeuge_ausgabe_ersetzt_normale_datei_atomar(tmp_path, monkeypatch, quelle_kopie):
    """§13.8 Punkt 1: der Schreibweg für --ausgabe ist schreibe_atomar — eine
    bereits vorhandene NORMALE Datei wird vollständig ersetzt (os.replace)."""
    schreib_heimat(tmp_path, monkeypatch)
    aus = tmp_path / "ausgabe"
    aus.mkdir()
    (aus / "CLAUDE.md").write_bytes(b"alter Stand, viel laenger als das neue\n" * 10)
    assert regeln.main(["erzeuge", "--ziel", "claude", "--quelle", str(quelle_kopie),
                        "--ausgabe", str(aus)]) == regeln.EXIT_OK
    assert (aus / "CLAUDE.md").read_bytes() == \
        regeln.erzeuge_bytes(quelle_kopie, "claude")
    assert not list(aus.glob("*.tmp"))                        # keine Temp-Reste


def test_ausgabe_verbot_gilt_auch_fuer_echtes_home(tmp_path, monkeypatch):
    """§13.8 Punkt 9 (O-2): das --ausgabe-Verbot gilt immer auch für das echte
    Path.home()/.claude und .codex, nicht nur für die Ordner unter REGELN_HOME.
    Ohne die Änderung wirft pruefe_ausgabe_ordner hier KEINE Ausnahme — der
    Lauf würde ins echte Heim schreiben. Geprüft wird die Funktion selbst, damit
    der Test nie wirklich dorthin schreibt."""
    schreib_heimat(tmp_path, monkeypatch)          # REGELN_HOME weit weg vom echten ~
    with pytest.raises(regeln.Aufruffehler):
        regeln.pruefe_ausgabe_ordner(Path.home() / ".claude")
    with pytest.raises(regeln.Aufruffehler):
        regeln.pruefe_ausgabe_ordner(Path.home() / ".codex")
    regeln.pruefe_ausgabe_ordner(tmp_path / "normaler-ordner")   # Negativkontrolle


def test_rueckgabe_sichert_die_installierte_datei_vorher(tmp_path, monkeypatch,
                                                         quelle_kopie):
    """§13.8 Punkt 8 (O-1): rueckgabe verliert nichts — eine von Hand geänderte
    installierte Datei wird VOR dem Zurückschreiben gesichert und ist danach in
    $REGELN_HOME/.local/state/regeln/sicherung erhalten. Ohne die Änderung
    würde sie still überschrieben und wäre weg."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    stand_a = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    (quelle_kopie / "CLAUDE.md").write_bytes(stand_a + b"\nRegel B\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    stand_b = (heimat / ".claude" / "CLAUDE.md").read_bytes()
    (quelle_kopie / "CLAUDE.md").write_bytes(stand_b + b"\nRegel C\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    installiert = heimat / ".claude" / "CLAUDE.md"
    installiert.write_bytes(stand_b + b"\nRegel C\nVon-Hand-Ergaenzung\n")  # nur lokal
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == regeln.EXIT_OK
    assert installiert.read_bytes() == stand_b            # Rückstand B wiederhergestellt
    sicherungen = (heimat / ".local" / "state" / "regeln" / "sicherung").glob("*-claude.md")
    inhalte = {p.read_bytes() for p in sicherungen}
    assert stand_b + b"\nRegel C\nVon-Hand-Ergaenzung\n" in inhalte   # Handstand erhalten


class _Gefroren:
    """Ersetzt regeln.datetime (Repro r3_05): now() liefert immer dieselbe Sekunde;
    alles andere (fromisoformat) delegiert an die echte datetime-Klasse."""
    from datetime import datetime as _echt

    @classmethod
    def now(cls, tz=None):
        from datetime import timezone as tz_modul
        return cls._echt(2026, 9, 27, 12, 0, 0, tzinfo=tz_modul.utc)

    fromisoformat = staticmethod(_echt.fromisoformat)


def test_sicherungsnamen_sind_utc_mit_z_suffix(tmp_path, monkeypatch, quelle_kopie):
    """§13.8 Punkt 5 (R3-5): Sicherungsnamen tragen UTC-Zeit in der Form
    YYYY-MM-DDTHH:MM:SSZ-<ziel>.md — lexikographisch = chronologisch, auch über
    Zeitzonenumstellungen hinweg. Ohne die Änderung: lokale Zeit mit Offset
    (+02:00) im Namen."""
    monkeypatch.setattr(regeln, "datetime", _Gefroren)
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    (quelle_kopie / "CLAUDE.md").write_bytes(b"Stand A\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    namen = [p.name for p in sicherung.glob("*-claude.md")]
    assert namen == ["2026-09-27T12:00:00Z-claude.md"]
    assert all("+" not in n and n.endswith("Z-claude.md") for n in namen)


def test_kollision_in_derselben_sekunde_mit_eingefrorener_zeit(tmp_path, monkeypatch,
                                                               quelle_kopie):
    """§13.8 Punkt 10 / R3-5 Teil 1: zwei Installationen in derselben (eingefrorenen)
    Sekunde → Zählersuffix …Z.01-…, keine Überschreibung; rueckgabe stellt den
    jüngsten gesicherten Stand wieder her."""
    monkeypatch.setattr(regeln, "datetime", _Gefroren)
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    stand_0 = installiert.read_bytes()          # die Original-Quelle, installiert
    (quelle_kopie / "CLAUDE.md").write_bytes(b"Stand A\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    (quelle_kopie / "CLAUDE.md").write_bytes(b"Stand B\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    (quelle_kopie / "CLAUDE.md").write_bytes(b"Stand C\n")
    assert regeln.main(["installiere", "--ziel", "claude",
                        "--quelle", str(quelle_kopie), "--ja"]) == regeln.EXIT_OK
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    namen = sorted(p.name for p in sicherung.glob("*-claude.md"))
    assert namen == ["2026-09-27T12:00:00Z-claude.md",        # sichert stand_0
                     "2026-09-27T12:00:00Z.01-claude.md",     # sichert Stand A
                     "2026-09-27T12:00:00Z.02-claude.md"]     # sichert Stand B
    inhalte = {p.name: p.read_bytes() for p in sicherung.glob("*-claude.md")}
    assert inhalte["2026-09-27T12:00:00Z-claude.md"] == stand_0
    assert inhalte["2026-09-27T12:00:00Z.01-claude.md"] == b"Stand A\n"
    assert inhalte["2026-09-27T12:00:00Z.02-claude.md"] == b"Stand B\n"
    assert regeln.main(["rueckgabe", "--ziel", "claude", "--ja"]) == regeln.EXIT_OK
    assert installiert.read_bytes() == b"Stand B\n"       # jüngste Sicherung


def test_sommerzeit_altbestand_wird_chronologisch_sortiert(tmp_path, monkeypatch):
    """§13.8 Punkt 5 (R3-5 Teil 3): Offset-Namen aus der Zeit VOR der Umstellung
    (Altbestand) sortiert juengste_sicherung trotzdem chronologisch — in der
    Sommerzeit-Stunde liegt 02:59+02:00 (=00:59 UTC) VOR 02:01+01:00 (=01:01
    UTC). Ohne die Änderung wurde die ÄLTERE Sicherung gewählt."""
    heimat = schreib_heimat(tmp_path, monkeypatch)
    sicherung = heimat / ".local" / "state" / "regeln" / "sicherung"
    sicherung.mkdir(parents=True)
    (sicherung / "2026-10-25T02:59:00+02:00-claude.md").write_bytes(b"AELTER (00:59 UTC)\n")
    (sicherung / "2026-10-25T02:01:00+01:00-claude.md").write_bytes(b"JUENGER (01:01 UTC)\n")
    gewaehlt = regeln.juengste_sicherung("claude")
    assert gewaehlt.name == "2026-10-25T02:01:00+01:00-claude.md"
    assert gewaehlt.read_bytes() == b"JUENGER (01:01 UTC)\n"


def test_ueberlappende_ersetzungen_sind_aufruffehler(tmp_path, capsys):
    """§13.8 Punkt 3 (R3-3): überlappende Tabelleneinträge sind ein Aufruffehler
    Exit 2, es wird nichts erzeugt (Repro r3_03, Szenen A und B — dort lief der
    Lauf Exit 0 mit still übergegangener bzw. falsch platzierter Ersetzung,
    während alle Prüfungen und Gate 2 grün blieben)."""
    szenen = [
        # Szene A: alt_2 ("AA") steckt in alt_1 ("AAA") → Ersetzung 2 still übergangen
        (b"Regel: AAA ENDE\n",
         [{"alt": "AAA", "neu": "NNN", "anzahl": 1},
          {"alt": "AA", "neu": "MM", "anzahl": 1}]),
        # Szene B: neu_1 ("BBB CCC") enthält alt_2 ("CCC") → Ersetzung an falscher Stelle
        (b"x AAA y CCC z\n",
         [{"alt": "AAA", "neu": "BBB CCC", "anzahl": 1},
          {"alt": "CCC", "neu": "DDD", "anzahl": 1}]),
    ]
    for nr, (text, tabelle) in enumerate(szenen, 1):
        quelle = tmp_path / f"szene-{nr}"
        quelle.mkdir()
        (quelle / "CLAUDE.md").write_bytes(text)
        schreibe_tabelle(quelle, tabelle)
        aus = tmp_path / f"szene-{nr}-ausgabe"
        code = regeln.main(["erzeuge", "--ziel", "codex", "--quelle", str(quelle),
                            "--ausgabe", str(aus)])
        assert code == regeln.EXIT_AUFRUFFEHLER, f"Szene {nr}: kein Aufruffehler"
        meldung = capsys.readouterr()
        assert "Wechselwirkung Eintrag" in meldung.err
        assert meldung.out == ""                       # nichts auf stdout
        assert not aus.exists() or list(aus.rglob("*")) == []   # keine Datei erzeugt


def test_leeres_alt_ist_aufruffehler(tmp_path, capsys):
    """§13.8 Punkt 3: `alt` leer → Exit 2 MIT klarer Meldung — ein leerer
    Suchtext trifft alles und macht jede Ersetzung nicht eindeutig. Ohne die
    Änderung fiel der Fall zufällig durch die anzahl-Prüfung auf Exit 2, aber
    mit irreführender Meldung („trifft 10-mal auf die Quelle") — auch die wird
    hier festgenagelt."""
    quelle = tmp_path / "leeres-alt"
    quelle.mkdir()
    (quelle / "CLAUDE.md").write_bytes(b"# Regeln\n")
    schreibe_tabelle(quelle, [{"alt": "", "neu": "X", "anzahl": 1}])
    assert regeln.main(["erzeuge", "--ziel", "codex",
                        "--quelle", str(quelle)]) == regeln.EXIT_AUFRUFFEHLER
    meldung = capsys.readouterr().err
    assert "alt darf nicht leer sein" in meldung
    assert "auf die Quelle" not in meldung   # nicht die irreführende anzahl-Meldung


def test_drift_nur_zeilenenden_wird_diagnostiziert(tmp_path, monkeypatch, quelle_kopie,
                                                   capsys):
    """§13.8 Punkt 4 (R3-4): Drift, die NUR aus Zeilenenden besteht (\n→\r\n),
    bekommt eine Zeilennummer und einen Hinweis mit der Byte-Position — nicht
    mehr „Zeile: None" mit leerem diff. Exit 1 war auch vorher korrekt."""
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle_kopie)
    installiert = heimat / ".claude" / "CLAUDE.md"
    original = installiert.read_bytes()
    installiert.write_bytes(original.replace(b"\n", b"\r\n"))
    assert regeln.main(["pruefe", "--ziel", "claude"]) == regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err
    assert "erste abweichende Zeile: 1" in meldung     # keepends-Zeilen, nicht None
    assert "Zeile: None" not in meldung
    assert "Zeilenenden/Steuerzeichen" in meldung      # der Ergänzungshinweis
    assert f"erste Abweichung ab Byte {original.index(b'\n') + 1}" in meldung


def test_drift_steuerzeichen_wird_diagnostiziert(tmp_path, monkeypatch, capsys):
    """§13.8 Punkt 4: ein \n, das durch \v (vertikalen Tab) ersetzt wurde —
    splitlines trennt an beiden, die Zeileninhalte bleiben gleich; die Meldung
    nennt trotzdem Zeile und Byte-Position (Repro r3_04 Fall 4)."""
    quelle = tmp_path / "regeln-v"
    quelle.mkdir()
    (quelle / "CLAUDE.md").write_bytes(b"Zeile 1\nZeile 2\nZeile 3\nletzte\n")
    (quelle / "ersetzungen-codex.json").write_text("[]", encoding="utf-8")
    heimat = installiere_wegwerf(tmp_path, monkeypatch, quelle)
    installiert = heimat / ".claude" / "CLAUDE.md"
    installiert.write_bytes(b"Zeile 1\x0bZeile 2\nZeile 3\nletzte\n")
    assert regeln.main(["pruefe", "--ziel", "claude", "--quelle", str(quelle)]) == \
        regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err
    assert "erste abweichende Zeile: 1" in meldung
    assert "Zeilenenden/Steuerzeichen" in meldung
    assert "ab Byte 8" in meldung                      # b"Zeile 1\n" — das \n ist Byte 8


def test_drift_ohne_zeilenunterschied_byte_fallback(tmp_path, monkeypatch, capsys):
    """§13.8 Punkt 4: unterscheiden sich die Bytes nur an Stellen, die decode
    auf dasselbe Replacement-Zeichen abbildet (hier \xff gegen \xfe), sind die
    Zeilen gleich — dann trägt die Meldung den Fallback-Wortlaut mit der
    Byte-Position, statt „Zeile: None" mit leerem diff."""
    quelle = tmp_path / "regeln-b"
    quelle.mkdir()
    (quelle / "ersetzungen-codex.json").write_text("[]", encoding="utf-8")
    heimat = schreib_heimat(tmp_path, monkeypatch)
    ziel = heimat / ".claude" / "CLAUDE.md"
    erzeugt = b"erste Zeile\n\nInhalt abc\xffghi\n"
    quelle_ordner_bytes = erzeugt.replace(b"\xff", b"\xfe")
    (quelle / "CLAUDE.md").write_bytes(quelle_ordner_bytes)
    ziel.parent.mkdir(parents=True)
    ziel.write_bytes(erzeugt)
    assert regeln.main(["pruefe", "--ziel", "claude", "--quelle", str(quelle)]) == \
        regeln.EXIT_DRIFT
    meldung = capsys.readouterr().err
    assert "Zeile: None" not in meldung
    assert "unterscheiden sich nur in Zeilenenden/Steuerzeichen" in meldung
    assert "erste Abweichung ab Byte 24" in meldung    # b"\xff" an 24. Stelle

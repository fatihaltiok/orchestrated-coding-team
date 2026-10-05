"""Tests zum Literatur-Werkzeug (Auftrag E1, Entscheidung E-029).

Kein Test berührt das Netz: die conftest-Sperre auf socket.connect und
urllib.request.urlopen gilt auch hier. Alle „Live"-Läufe gehen über ein
Attrappen-Modul ``edison_client`` in sys.modules; die echte Schlüsseldatei
wird nie gelesen — jede Schlüsseldatei liegt in tmp_path.
"""

import importlib.util
import json
import sys
import threading
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "edison", WERKZEUGE / "edison.py")
edison = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edison)

GEHEIM = "edison-test-schluessel-ABCDEF1234567890"


@pytest.fixture(autouse=True)
def _saubere_umgebung(monkeypatch):
    """Kein echter Schlüssel und kein echtes Client-Modul in den Tests."""
    monkeypatch.delenv("EDISON_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "edison_client", raising=False)


def _attrappe(antwort=None, fehler=None, beim_aufruf=None):
    """Ein falsches edison_client-Modul; „aufzeichnung" sammelt Aufrufe."""
    modul = types.ModuleType("edison_client")
    aufzeichnung = {"api_key": None, "aufrufe": []}

    class JobNames:
        LITERATURE = "job-futurehouse-paperqa3"
        LITERATURE_HIGH = "job-futurehouse-paperqa3-high"
        PRECEDENT = "job-futurehouse-paperqa3-precedent"
        DUMMY = "job-futurehouse-dummy-env"

    class EdisonClient:
        def __init__(self, api_key=None):
            aufzeichnung["api_key"] = api_key

        def run_tasks_until_done(self, task_data, timeout=None, **_kw):
            satz = dict(task_data)
            satz["timeout"] = timeout
            aufzeichnung["aufrufe"].append(satz)
            if beim_aufruf is not None:
                beim_aufruf()
            if fehler is not None:
                raise fehler
            return (antwort,)

    modul.JobNames = JobNames
    modul.EdisonClient = EdisonClient
    modul.aufzeichnung = aufzeichnung
    return modul


def _antwort(**felder):
    grund = {"status": "success", "query": "f", "created_at": "2026-10-01",
             "job_name": "job-x", "task_id": "t-1",
             "answer": "Die Antwort.",
             "formatted_answer": "Die Antwort. [Quelle: doi:10.x]",
             "answer_reasoning": "Weil die Quellen das hergeben.",
             "has_successful_answer": True, "total_cost": 0.42,
             "total_queries": 7}
    grund.update(felder)
    return types.SimpleNamespace(**grund)


def _schluessel_anlegen(tmp_path, inhalt=None):
    pfad = tmp_path / "schluessel.env"
    if inhalt is None:
        inhalt = f"  {edison.SCHLUESSEL_VAR} = {GEHEIM}  \n"
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


def _argv(tmp_path, *extra, agent="literatur", frage="Was ist X?"):
    """frage-Aufruf mit allen Pfaden in tmp_path — nie die echten Dateien."""
    argv = ["frage", "--agent", agent, "--schluesseldatei",
            str(tmp_path / "schluessel.env"), "--zaehldatei",
            str(tmp_path / "zaehler.jsonl"), "--ordner",
            str(tmp_path / "ergebnisse")]
    if frage is not None:
        argv += ["--frage", frage]
    argv += list(extra)
    return argv


def _zeile(lauf, zeit, art="start"):
    return json.dumps({"zeit": zeit.isoformat(timespec="seconds"),
                       "lauf": lauf, "art": art, "agent": "literatur",
                       "fragenlaenge": 9})


# --- Offline-Standard ---------------------------------------------------

def test_offline_vorschau_kein_import_keine_dateien(tmp_path, capsys):
    assert edison.main(_argv(tmp_path)) == 0
    assert "edison_client" not in sys.modules
    assert not (tmp_path / "zaehler.jsonl").exists()
    assert not (tmp_path / "ergebnisse").exists()
    raus = capsys.readouterr().out
    assert "nichts gesendet" in raus
    assert "LITERATURE" in raus
    assert "Fragenlänge" in raus


def test_offline_agent_unbekannt_exit2(tmp_path, capsys):
    assert edison.main(_argv(tmp_path, agent="spinne")) == 2
    assert "unbekannt" in capsys.readouterr().err


def test_offline_frage_leer_exit2(tmp_path, capsys):
    assert edison.main(_argv(tmp_path, frage="   ")) == 2
    assert "leer" in capsys.readouterr().err


def test_offline_frage_zu_lang_exit2(tmp_path, capsys):
    assert edison.main(_argv(tmp_path, frage="x" * 8001)) == 2
    assert "zu lang" in capsys.readouterr().err


def test_offline_frage_datei_und_fehlende_datei(tmp_path, capsys):
    pfad = tmp_path / "frage.txt"
    pfad.write_text("Was weiß die Literatur über Z?\n", encoding="utf-8")
    argv = _argv(tmp_path, "--frage-datei", str(pfad), frage=None)
    assert edison.main(argv) == 0
    argv = _argv(tmp_path, "--frage-datei", str(tmp_path / "fehlt.txt"),
                 frage=None)
    assert edison.main(argv) == 2
    assert "nicht lesbar" in capsys.readouterr().err


# --- Schlüsselleser ------------------------------------------------------

@pytest.mark.parametrize("inhalt", [
    "EDISON_API_KEY=abc\n",
    "\n\n   EDISON_API_KEY = abc   \n",      # echte Form: Leerzeile+Leerzeichen
    "export EDISON_API_KEY=abc\n",
    'EDISON_API_KEY="abc"\n',
    "EDISON_API_KEY='abc'\n",
    "# Kommentar\nANDERE_VAR=1\n EDISON_API_KEY=abc\n",
])
def test_schluessel_leser_formen(tmp_path, inhalt):
    pfad = tmp_path / "k.env"
    pfad.write_text(inhalt, encoding="utf-8")
    assert edison._lies_schluessel_datei(str(pfad)) == "abc"


def test_schluessel_datei_fehlt_oder_leer(tmp_path, capsys, monkeypatch):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    # Datei existiert nicht:
    assert edison.main(_argv(tmp_path, "--live")) == 2
    err = capsys.readouterr().err
    assert "kein Schlüssel" in err and "schluessel.env" in err
    # Datei vorhanden, aber ohne brauchbaren Wert:
    _schluessel_anlegen(tmp_path, inhalt="EDISON_API_KEY=\nANDERE=x\n")
    assert edison.main(_argv(tmp_path, "--live")) == 2
    assert attrappe.aufzeichnung["aufrufe"] == []


def test_schluessel_umgebung_schlaegt_datei(monkeypatch, tmp_path):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path, inhalt="EDISON_API_KEY=aus-datei\n")
    monkeypatch.setenv("EDISON_API_KEY", "aus-umgebung")
    assert edison.main(_argv(tmp_path, "--live")) == 0
    assert attrappe.aufzeichnung["api_key"] == "aus-umgebung"


# --- Live über Attrappe --------------------------------------------------

@pytest.mark.parametrize("agent,wert", [
    ("literatur", "job-futurehouse-paperqa3"),
    ("literatur-hoch", "job-futurehouse-paperqa3-high"),
    ("vorlaeufer", "job-futurehouse-paperqa3-precedent"),
    ("probe", "job-futurehouse-dummy-env"),
])
def test_live_agent_mitglied(monkeypatch, tmp_path, capsys, agent, wert):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live", agent=agent)) == 0
    aufruf = attrappe.aufzeichnung["aufrufe"][0]
    assert aufruf["name"] == wert
    assert attrappe.aufzeichnung["api_key"] == GEHEIM


def test_live_zeitgrenze_durchgereicht(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live", "--zeitgrenze", "55")) == 0
    assert attrappe.aufzeichnung["aufrufe"][0]["timeout"] == 55.0


def test_live_dateien_korrekt_und_nie_ueberschrieben(monkeypatch, tmp_path,
                                                    capsys):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 0
    ordner = tmp_path / "ergebnisse"
    mds = sorted(ordner.glob("*.md"))
    jsons = sorted(ordner.glob("*.json"))
    assert len(mds) == len(jsons) == 1
    md = mds[0].read_text(encoding="utf-8")
    assert "Was ist X?" in md and "t-1" in md
    assert "Die Antwort. [Quelle: doi:10.x]" in md
    assert "selbst nachschlagen" in md
    daten = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert daten["agent"] == "literatur"
    assert daten["mitglied"] == "LITERATURE"
    assert daten["task_id"] == "t-1"
    assert daten["has_successful_answer"] is True
    assert daten["total_cost"] == 0.42
    # Zweiter Lauf gleicher Frage: neuer Name, alte Datei unverändert.
    erster = md
    assert edison.main(_argv(tmp_path, "--live")) == 0
    mds2 = sorted(ordner.glob("*.md"))
    assert len(mds2) == 2
    assert mds[0].read_text(encoding="utf-8") == erster


def test_live_pqa_felder_fehlen(monkeypatch, tmp_path, capsys):
    """DUMMY-Antwort ohne PQA-Felder: getattr(..., None) muss vertragen.

    Eine Antwort braucht seit E1n/B1 einen Antworttext für Exit 0 —
    hier steht er in ``answer``, die übrigen PQA-Felder fehlen."""
    antwort = types.SimpleNamespace(status="success", task_id="t-dummy",
                                    answer="Probe-Antwort")
    attrappe = _attrappe(antwort=antwort)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live", agent="probe")) == 0
    daten = json.loads(
        next((tmp_path / "ergebnisse").glob("*.json"))
        .read_text(encoding="utf-8"))
    assert daten["task_id"] == "t-dummy"
    assert daten["formatted_answer"] is None


def test_has_successful_false_exit3(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(antwort=_antwort(has_successful_answer=False))
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 3
    assert list((tmp_path / "ergebnisse").glob("*.md"))
    assert "has_successful_answer" in capsys.readouterr().err


def test_edison_client_fehlt_exit2(monkeypatch, tmp_path, capsys):
    # sys.modules-Eintrag None → Import schlägt immer fehl.
    monkeypatch.setitem(sys.modules, "edison_client", None)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 2
    err = capsys.readouterr().err
    assert "edison_client" in err and "Hülle" in err


# --- Schlüssel bleibt geheim ---------------------------------------------

def _alles_gelesen(tmp_path):
    """Gesamter Text aller Ausgabedateien — ohne die Schlüsseldatei."""
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and p.name != "schluessel.env")


def test_schluessel_nirgends_bei_erfolg(monkeypatch, tmp_path, capsys):
    antwort = _antwort(formatted_answer=f"Antwort mit {GEHEIM} drin")
    attrappe = _attrappe(antwort=antwort)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 0
    fertig = capsys.readouterr()
    assert GEHEIM not in fertig.out + fertig.err
    assert GEHEIM not in _alles_gelesen(tmp_path)


def test_schluessel_maskiert_bei_fehler(monkeypatch, tmp_path, capsys):
    fehler = RuntimeError(f"401 ungueltig: {GEHEIM} abgelehnt")
    attrappe = _attrappe(fehler=fehler)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 4
    fertig = capsys.readouterr()
    assert GEHEIM not in fertig.out + fertig.err
    assert "***" in fertig.err
    assert GEHEIM not in _alles_gelesen(tmp_path)
    # Nichts gespeichert außer der Zählzeile:
    assert not (tmp_path / "ergebnisse").exists()
    assert (tmp_path / "zaehler.jsonl").exists()


# --- Monatsgrenze ----------------------------------------------------------

def _fuellen(tmp_path, n, zeitpunkt, praefix="alt"):
    pfad = tmp_path / "zaehler.jsonl"
    pfad.write_text("".join(
        _zeile(f"{praefix}-{i}", zeitpunkt) + "\n" for i in range(n)),
        encoding="utf-8")


def test_monatsgrenze_blockiert(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    _fuellen(tmp_path, 10, datetime.now())
    assert edison.main(_argv(tmp_path, "--live")) == 5
    assert attrappe.aufzeichnung["aufrufe"] == []
    assert "Monatsgrenze" in capsys.readouterr().err


def test_knapp_unter_grenze_laeuft(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    _fuellen(tmp_path, 9, datetime.now())
    assert edison.main(_argv(tmp_path, "--live")) == 0
    assert len(attrappe.aufzeichnung["aufrufe"]) == 1


def test_vormonat_zaehlt_nicht(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    vormonat = datetime.now().replace(day=1) - timedelta(days=1)
    _fuellen(tmp_path, 10, vormonat)
    assert edison.main(_argv(tmp_path, "--live")) == 0
    assert len(attrappe.aufzeichnung["aufrufe"]) == 1


def test_zaehlzeile_steht_vor_dem_aufruf(monkeypatch, tmp_path, capsys):
    """Die Zählzeile wird VOR dem Netzaufruf geschrieben (Abbruch zählt)."""
    zaehldatei = tmp_path / "zaehler.jsonl"
    gesehen = []

    def beim_aufruf():
        gesehen.append(zaehldatei.read_text(encoding="utf-8"))

    attrappe = _attrappe(antwort=_antwort(), beim_aufruf=beim_aufruf)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 0
    assert gesehen and '"art": "start"' in gesehen[0]
    # Start- und Ergebniszeile eines Laufs zählen nur einmal:
    eintraege = edison._lade_zaehler(str(zaehldatei))
    arten = sorted(e["art"] for e in eintraege)
    assert arten == ["ende", "start"]
    assert edison._zaehle_monat(eintraege, datetime.now()) == 1


def test_fehler_schreibt_fehlerzeile(monkeypatch, tmp_path, capsys):
    attrappe = _attrappe(fehler=TimeoutError("Zeitgrenze"))
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 4
    eintraege = edison._lade_zaehler(str(tmp_path / "zaehler.jsonl"))
    assert sorted(e["art"] for e in eintraege) == ["fehler", "start"]
    assert edison._zaehle_monat(eintraege, datetime.now()) == 1


# --- E1n/B1: keine Antwort ist kein Erfolg ------------------------------

def test_keine_antwort_exit3_mit_status_und_task_id(monkeypatch, tmp_path,
                                                  capsys):
    """status=pending, answer=None, hsa=None: Zwischenstand wird
    gespeichert (mit task_id), Exit 3, Meldung nennt Status + task_id."""
    antwort = _antwort(status="pending", answer=None,
                       formatted_answer=None, answer_reasoning=None,
                       has_successful_answer=None, task_id="t-pend")
    attrappe = _attrappe(antwort=antwort)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 3
    err = capsys.readouterr().err
    assert "pending" in err and "t-pend" in err
    mds = list((tmp_path / "ergebnisse").glob("*.md"))
    assert len(mds) == 1
    md = mds[0].read_text(encoding="utf-8")
    assert "t-pend" in md and "Was ist X?" in md


def test_nur_answer_text_reicht_fuer_exit0(monkeypatch, tmp_path, capsys):
    """Antworttext nur in ``answer`` (formatted_answer leer) → Exit 0."""
    antwort = _antwort(formatted_answer=None)
    attrappe = _attrappe(antwort=antwort)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 0


# --- E1n/B2: kaputte Zählzeilen zählen sicher ----------------------------

def test_kaputte_zaehlzeilen_zaehlen_einzeln(tmp_path):
    """Abgeschnittene, Müll-, Nicht-Objekt- und zeitlose Zeilen zählen
    je einzeln als Aufruf im laufenden Monat (sicherer Fehler)."""
    jetzt = datetime.now()
    zaehldatei = tmp_path / "zaehler.jsonl"
    zaehldatei.write_text("\n".join([
        '{"zeit": "%s", "lauf": "ab' % jetzt.isoformat(timespec="seconds"),
        "müllzeile {{{",
        "[1, 2, 3]",
        json.dumps({"zeit": "kein-datum", "lauf": "x"}),
        json.dumps({"lauf": "y"}),
    ]) + "\n", encoding="utf-8")
    eintraege = edison._lade_zaehler(str(zaehldatei))
    assert len(eintraege) == 5
    assert edison._zaehle_monat(eintraege, jetzt) == 5


def test_lauf_null_zaehlt_je_einzeln(tmp_path):
    """Drei Zeilen mit lauf=null zählen als drei Aufrufe, nie als einer."""
    jetzt = datetime.now()
    zaehldatei = tmp_path / "zaehler.jsonl"
    zaehldatei.write_text("".join(
        json.dumps({"zeit": jetzt.isoformat(timespec="seconds"),
                    "lauf": None, "art": "start"}) + "\n"
        for _ in range(3)), encoding="utf-8")
    eintraege = edison._lade_zaehler(str(zaehldatei))
    assert edison._zaehle_monat(eintraege, jetzt) == 3


def test_monatsgrenze_blockiert_mit_kaputten_zeilen(monkeypatch, tmp_path,
                                                  capsys):
    """8 lesbare + 1 abgeschnittene + 1 Müllzeile = 10 → Grenze hält."""
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    _fuellen(tmp_path, 8, datetime.now())
    with open(tmp_path / "zaehler.jsonl", "a", encoding="utf-8") as d:
        d.write('{"zeit": "2026-10-01T10:00", "lauf": "ab\n')
        d.write("müllzeile\n")
    assert edison.main(_argv(tmp_path, "--live")) == 5
    assert attrappe.aufzeichnung["aufrufe"] == []


def test_zaehler_befehl(tmp_path, capsys):
    zaehldatei = tmp_path / "zaehler.jsonl"
    _fuellen(tmp_path, 3, datetime.now())
    assert edison.main(["zaehler", "--zaehldatei", str(zaehldatei)]) == 0
    daten = json.loads(capsys.readouterr().out)
    assert daten["aufrufe"] == 3 and daten["monatsgrenze"] == 10
    # Ohne Datei: 0 Aufrufe, kein Fehler.
    assert edison.main(["zaehler", "--zaehldatei",
                        str(tmp_path / "gibts-nicht.jsonl")]) == 0
    assert json.loads(capsys.readouterr().out)["aufrufe"] == 0


# --- Negativkontrollen ---------------------------------------------------

def test_negativ_maskierung_aus_und_schluessel_leckt(monkeypatch, tmp_path,
                                                     capsys):
    """Kaputter Schutz: Maskierung abgeschaltet → der Schlüssel MUSS
    lecken. Zeigt, dass test_schluessel_maskiert_bei_fehler den Schutz
    wirklich trifft und nicht leer läuft."""
    monkeypatch.setattr(edison, "_maskiere", lambda _s, t: t)
    fehler = RuntimeError(f"401 ungueltig: {GEHEIM} abgelehnt")
    attrappe = _attrappe(fehler=fehler)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 4
    fertig = capsys.readouterr()
    assert GEHEIM in fertig.err  # Leck nachweisbar → Schutz ist echt


def test_negativ_grenze_um_eins_versetzt_laesst_durch(monkeypatch, tmp_path,
                                                    capsys):
    """Kaputter Schutz: Zählung um eins zu niedrig → ein Aufruf zu viel
    geht durch. Zeigt, dass test_monatsgrenze_blockiert die Grenze
    wirklich trifft."""
    echt = edison._zaehle_monat
    monkeypatch.setattr(edison, "_zaehle_monat",
                        lambda e, j: max(0, echt(e, j) - 1))
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    _fuellen(tmp_path, 10, datetime.now())
    ausgang = edison.main(_argv(tmp_path, "--live"))
    # Mit kaputter Grenze geht der 11. Aufruf durch — der echte Schutz
    # hätte mit Exit 5 blockiert (siehe test_monatsgrenze_blockiert).
    assert ausgang == 0
    assert len(attrappe.aufzeichnung["aufrufe"]) == 1


# --- E1n/B3 + B7: Gleichzeitigkeit --------------------------------------

def test_gleichzeitige_aufrufe_grenze1_nur_einer(monkeypatch, tmp_path):
    """Zwei gleichzeitige Live-Läufe bei Grenze 1: die flock-Sperre lässt
    genau einen durch, der zweite sieht die Startzeile (E1n/B3)."""
    schranke = threading.Barrier(2)
    attrappe = _attrappe(antwort=_antwort(),
                         beim_aufruf=lambda: time.sleep(0.05))
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    ausgaenge = []

    def arbeit():
        schranke.wait()
        ausgaenge.append(edison.main(
            _argv(tmp_path, "--live", "--monatsgrenze", "1")))

    faeden = [threading.Thread(target=arbeit) for _ in range(2)]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()
    assert sorted(ausgaenge) == [0, 5]
    assert len(attrappe.aufzeichnung["aufrufe"]) == 1


def test_ergebnis_zwei_threads_vier_dateien(tmp_path):
    """Zwei gleichzeitige _schreibe_ergebnis-Läufe: exklusives Anlegen
    ergibt vier verschiedene Dateien, nichts wird überschrieben (B7)."""
    ordner = tmp_path / "o"
    zeit = datetime.now()
    daten = {"status": "success", "task_id": "t", "job_name": "j",
             "answer": "A", "formatted_answer": "FA",
             "has_successful_answer": True}
    schranke = threading.Barrier(2)
    ergebnisse = []

    def arbeit():
        schranke.wait()
        ergebnisse.append(edison._schreibe_ergebnis(
            ordner, "literatur", "LITERATURE", "Frage?", dict(daten), zeit))

    faeden = [threading.Thread(target=arbeit) for _ in range(2)]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()
    assert len(ergebnisse) == 2
    assert len({p.name for paar in ergebnisse for p in paar}) == 4
    assert len(list(ordner.glob("*.md"))) == 2
    assert len(list(ordner.glob("*.json"))) == 2


# --- E1n/B4: Abbruch und SystemExit --------------------------------------

def test_keyboardinterrupt_exit130_fehlerzeile(monkeypatch, tmp_path,
                                               capsys):
    """Strg+C im Netzteil: Fehlerzeile in der Zähldatei, kurze Meldung
    ohne Traceback, Exit 130."""
    attrappe = _attrappe(fehler=KeyboardInterrupt())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 130
    fertig = capsys.readouterr()
    assert "Traceback" not in fertig.err
    assert "abgebrochen" in fertig.err
    eintraege = edison._lade_zaehler(str(tmp_path / "zaehler.jsonl"))
    assert sorted(e["art"] for e in eintraege) == ["fehler", "start"]
    assert edison._zaehle_monat(eintraege, datetime.now()) == 1


def test_systemexit_maskiert_exit4(monkeypatch, tmp_path, capsys):
    """SystemExit der Bibliothek mit Schlüssel im Text → maskiert auf
    stderr und in der Zähldatei, Exit 4 (E1n/B4)."""
    attrappe = _attrappe(fehler=SystemExit(f"auth {GEHEIM} kaputt"))
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 4
    fertig = capsys.readouterr()
    assert GEHEIM not in fertig.out + fertig.err
    assert GEHEIM not in _alles_gelesen(tmp_path)


# --- E1n/B5: ungültiges UTF-8 --------------------------------------------

def test_schluesseldatei_ungueltiges_utf8_exit2(monkeypatch, tmp_path,
                                              capsys):
    """Kaputte Bytes in der Schlüsseldatei: wie „kein Schlüssel",
    Exit 2, kein Traceback."""
    (tmp_path / "schluessel.env").write_bytes(
        b"EDISON_API_KEY=\xff\xfe kaputt\n")
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    assert edison.main(_argv(tmp_path, "--live")) == 2
    assert "kein Schlüssel" in capsys.readouterr().err


def test_zaehldatei_ungueltiges_utf8_zaehlt_sicher(monkeypatch, tmp_path,
                                                 capsys):
    """Kaputte Bytes in der Zähldatei zählen als Aufruf (B2) — kein
    Traceback: 9 lesbare + 1 kaputte Zeile = 10 → Grenze hält."""
    attrappe = _attrappe(antwort=_antwort())
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    _fuellen(tmp_path, 9, datetime.now())
    with open(tmp_path / "zaehler.jsonl", "ab") as d:
        d.write(b"\xff\xfe kaputte bytes\n")
    assert edison.main(_argv(tmp_path, "--live")) == 5
    assert attrappe.aufzeichnung["aufrufe"] == []


# --- E1n/M1: Maskierung von Sitzungstoken und JWTs -----------------------

JWT_TEST = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


def test_maskierung_sitzungstoken_und_jwt(monkeypatch, tmp_path, capsys):
    """Fehlermeldung mit Bearer-Wert, api_key=, token=, authorization:
    und JWT: keiner dieser Werte darf auf stderr oder in Dateien stehen."""
    fehler = RuntimeError(
        f"401: Bearer tok-sitzung-123 api_key=key-xyz TOKEN=tok-abc "
        f"authorization: auth-geheim {JWT_TEST} ref={GEHEIM}")
    attrappe = _attrappe(fehler=fehler)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 4
    fertig = capsys.readouterr()
    alles = _alles_gelesen(tmp_path)
    for geheimnis in ("tok-sitzung-123", "key-xyz", "tok-abc",
                      "auth-geheim", JWT_TEST, GEHEIM):
        assert geheimnis not in fertig.out + fertig.err
        assert geheimnis not in alles
    assert "***" in fertig.err


def test_maskierung_gilt_in_ergebnisdateien(monkeypatch, tmp_path, capsys):
    """Token-förmige Stellen in der Antwort landen maskiert in .md/.json."""
    antwort = _antwort(formatted_answer=(
        f"Antwort Bearer sitz-777 api_key=k-9 {JWT_TEST}"))
    attrappe = _attrappe(antwort=antwort)
    monkeypatch.setitem(sys.modules, "edison_client", attrappe)
    _schluessel_anlegen(tmp_path)
    assert edison.main(_argv(tmp_path, "--live")) == 0
    alles = _alles_gelesen(tmp_path)
    for geheimnis in ("sitz-777", "k-9", JWT_TEST):
        assert geheimnis not in alles
    assert "***" in _alles_gelesen(tmp_path)


# --- E1n/M2: „export" nur als eigenes Wort -------------------------------

def test_schluessel_export_nur_eigenes_wort(tmp_path):
    """``export`` ohne folgendes Leerzeichen ist Teil des Variablennamens
    und darf den Namen nicht verstümmeln (E1n/M2)."""
    pfad = tmp_path / "k.env"
    pfad.write_text("exportEDISON_API_" "KEY=falscher-name\n"
                    "exporter=auch-fremd\n"
                    " EDISON_API_KEY=abc\n", encoding="utf-8")
    assert edison._lies_schluessel_datei(str(pfad)) == "abc"
    # Und weiterhin: „export " mit Leerraum wird entfernt.
    pfad.write_text("export EDISON_API_KEY=abc\n", encoding="utf-8")
    assert edison._lies_schluessel_datei(str(pfad)) == "abc"

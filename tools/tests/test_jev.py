"""Tests zum Jev-Baustein (Vertrag §12.6, Teil von G9).

Kein Test berührt das Netz: socket.socket.connect und
urllib.request.urlopen liegen für die ganze Datei auf einer Ausnahme.
Alle Jev-Antworten kommen aus eingeschleusten falschen Transporten.
Unterprozesse (CLI) starten über _cli_lauf mit derselben Sperre im
Kindprozess (§12.9 Punkt 4).
"""

import importlib.util
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
JEV_PFAD = WERKZEUGE / "jev.py"
KONTEXTPAKET_PFAD = WERKZEUGE / "kontextpaket.py"
WURZEL = WERKZEUGE.parent
_spec = importlib.util.spec_from_file_location("jev", JEV_PFAD)
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
Antwort = jev.Antwort
Eintrag = jev.Eintrag
Ordnung = jev.Ordnung
frage = jev.frage
ordne_kandidaten = jev.ordne_kandidaten
schaetze_kosten = jev.schaetze_kosten
FALSCH_SCHLUESSEL = "test-schluessel-123"


@pytest.fixture(autouse=True)
def _ohne_netz(monkeypatch):
    """Netz ist in diesen Tests verboten — jeder Versuch knallt."""
    def _verboten(*args, **kwargs):
        raise AssertionError("Netzaufruf im Jev-Test")
    monkeypatch.setattr("socket.socket.connect", _verboten)
    monkeypatch.setattr("urllib.request.urlopen", _verboten)


# --- §12.9 Punkt 4: Netzsperre auch im Kindprozess -----------------------------
# Startcode für CLI-Unterprozesse: sperrt socket.connect sofort und
# urllib.request.urlopen per Importhaken (lazy, damit der Importtest
# „urllib.request nicht in sys.modules" gültig bleibt). Jeder Versuch
# scheitert mit AssertionError „Netzaufruf im Kindprozess".
_KIND_SPERRE = "\n".join([
    "import socket as _sock, sys as _sys",
    "import importlib.abc as _abc, importlib.machinery as _mach",
    "def _netz_verboten(*_a, **_k):",
    "    raise AssertionError('Netzaufruf im Kindprozess')",
    "_sock.socket.connect = _netz_verboten",
    "class _UrlSperre(_abc.MetaPathFinder):",
    "    def find_spec(self, _n, _p=None, _t=None):",
    "        if _n != 'urllib.request':",
    "            return None",
    "        _spec = _mach.PathFinder.find_spec(_n, _p)",
    "        if _spec is None or _spec.loader is None:",
    "            return None",
    "        if getattr(_spec.loader, 'exec_module', None) is None:",
    "            return None",
    "        _orig = _spec.loader.exec_module",
    "        def _belegt(_mod, _o=_orig):",
    "            _o(_mod)",
    "            _mod.urlopen = _netz_verboten",
    "        _spec.loader.exec_module = _belegt",
    "        return _spec",
    "_sys.meta_path.insert(0, _UrlSperre())",
])


def _cli_lauf(*cli_args, env=None, cwd=None):
    """jev.py als Unterprozess — mit Netzsperre im Kindprozess (§12.9 Punkt 4)."""
    argv = ["jev.py", *cli_args]
    code = ("import sys as _cli_sys; "
            f"_cli_sys.argv = {json.dumps(argv)};\n"
            + _KIND_SPERRE + "\n"
            f"import runpy as _cli_r; _cli_r.run_path({str(JEV_PFAD)!r}, "
            "run_name='__main__')")
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=60,
                          env=env, cwd=cwd)


def _kandidat(i, text="Abschnitt über Regeln und Entscheidungen."):
    return {"id": f"k{i}", "datei": f"/tmp/d{i}.md",
            "ueberschrift": f"Abschnitt {i}", "text": text}


def _rumpf(antworten, modell="jev-1.13.0", token=11):
    return json.dumps({"model": modell, "answers": antworten,
                       "usage": {"input_tokens": token,
                                 "output_tokens": 2}}).encode("utf-8")


def _noul(wert):
    return {"wichtig": {"type": "noul", "noul": wert}}


def _transport_aus_folge(folge):
    """Falscher Transport: liefert die Folge ab, merkt sich Aufrufe.

    Jeder Aufruf verbraucht einen Eintrag (Wiederholungen also mehrfach
    einplanen); ist die Folge leer, gilt der letzte Eintrag weiter.
    """
    from collections import deque
    rest = deque(folge)
    letzter = folge[-1]
    aufrufe = []

    def transport(url, kopf, rumpf, timeout):
        aufrufe.append({"url": url, "kopf": kopf, "rumpf": rumpf,
                        "timeout": timeout})
        naechste = rest.popleft() if rest else letzter
        if isinstance(naechste, Exception):
            raise naechste
        return naechste
    transport.aufrufe = aufrufe
    return transport


def _ok_transport(noul_wert=0.9, **mehr):
    return _transport_aus_folge(
        [(200, {}, _rumpf(_noul(noul_wert), **mehr))])


# --- §12.6 Punkt 1: Gate 4 — kein Netz ohne Live ----------------------------

def test_126_1_kein_netz_ohne_live(monkeypatch):
    """ordne + CLI ohne --live, mit gesetztem Schlüssel: alles offline."""
    monkeypatch.setenv("TYPESAFE_API_KEY", FALSCH_SCHLUESSEL)
    zaehler = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    ordnung = ordne_kandidaten("Auftrag: Regeln prüfen",
                               [_kandidat(1), _kandidat(2)],
                               transport=zaehler,
                               schluessel=FALSCH_SCHLUESSEL)
    assert zaehler.aufrufe == []
    assert ordnung.status_gesamt == "nicht_bewertet"
    assert [e.id for e in ordnung.eintraege] == ["k1", "k2"]
    assert all(e.status == "nicht_bewertet" and e.grund == "offline"
               for e in ordnung.eintraege)


def test_126_1b_import_ohne_urllib():
    """Nach import jev ist urllib.request nicht in sys.modules."""
    code = (_KIND_SPERRE + "\nimport sys, importlib.util; "
            "s = importlib.util.spec_from_file_location('jev', "
            f"{str(JEV_PFAD)!r}); m = importlib.util.module_from_spec(s); "
            "s.loader.exec_module(m); "
            "assert 'urllib.request' not in sys.modules, sorted(sys.modules); "
            "print('OK')")
    prozess = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=60)
    assert prozess.returncode == 0, prozess.stderr
    assert "OK" in prozess.stdout


def test_14_schluessel_treffer_gibt_nur_bezeichnungen(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "erfundener-geheimer-wert")
    geheimes_muster = "sk-" + "abcdefghijklmnopQRST"
    treffer = jev.schluessel_treffer([
        ("auftrag", "Text mit " + geheimes_muster),
        ("kandidat", "api_key = " + "abcdefghijklmnopqrstuvwxyz"),
        ("bearer", "Bearer abcdefghijklmnop"),
        ("umgebungswert", "erfundener-geheimer-wert"),
        ("sicher", "kein Geheimnis hier"),
    ])
    assert treffer == ["auftrag", "bearer", "kandidat", "umgebungswert"]
    assert all(geheim not in " ".join(treffer)
               for geheim in (geheimes_muster, "erfundener-geheimer-wert"))


def test_14_11_schluessel_treffer_platzhalter_treffen_nicht(monkeypatch):
    """§14.11 Punkt 1: nur Schlüsselzeichen treffen; Platzhalter nicht."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "echter-umgebungs-wert-99")
    treffer = jev.schluessel_treffer([
        ("p_bearer_spitze", "Authorization: Bearer <Schlüssel>"),
        ("p_bearer_env", "Bearer $TYPESAFE_API_KEY"),
        ("p_token_env", "TOKEN=$TYPESAFE_API_KEY"),
        ("p_apikey_ellipsis", "api_key = \"…\""),
        ("p_secret_sterne", "secret: ***"),
        ("bearer_echt", "Bearer abcdefghijklmnop1234"),
        ("apikey_echt", "API_KEY=" + "abcdefghijkl1234"),
        ("sk_echt", "sk-" + "abcdefghijklmnopqrstuv"),
        ("umgebungswert", "hier steht echter-umgebungs-wert-99 drin"),
    ])
    assert treffer == ["apikey_echt", "bearer_echt", "sk_echt", "umgebungswert"]
    assert "echter-umgebungs-wert-99" not in " ".join(treffer)


def test_14_12_schluessel_treffer_neue_formen(monkeypatch):
    """§14.12 Punkt 1 (B2): realistische Fremdschlüssel-Formen treffen —
    AWS, GitHub, Slack, Google, nacktes JWT, Stripe, Private Keys, bearer
    ohne Groß-/Kleinschreibung und Labels mit Namensende/Sonderzeichen."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "umgebungs-wert-0815")
    faelle = [
        ("aws_nackt", "AKIAIOSFODNN7EXAMPLE"),
        ("aws_benannt", "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"),
        ("aws_secret",
         "aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"),
        ("ghp", "ghp_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ("gho", "gho_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ("ghs", "ghs_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ("ghr", "ghr_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ("ghu", "ghu_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        ("github_pat", "github" + "_pat_11ABCDEFG0AbCdEfGhIj_KlMnOpQrStUvWxYz"),
        ("slack", "xox" + "b-1234567890123-abcdefghijklmn"),
        ("google", "AIza" + "SyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7"),
        ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
               ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
        ("sk_live", "sk_" + "live_4eC39HqLyjWDarjtT1z"),
        ("sk_test", "sk_" + "test_4eC39HqLyjWDarjtT1z"),
        ("privkey", "-----BEGIN RSA " + "PRIVATE KEY-----"),
        ("privkey_openssh", "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
        ("bearer_klein", "bearer abcdefghijklmnop1234"),
        ("bearer_gross", "BEARER abcdefghijklmnop1234"),
        ("password", "PASSWORD=Sup3r!Geheim%Passwort"),
        ("mysql_pwd", "MYSQL_PWD=ab!cd%ef:gh12"),
        ("apikey_sonder", "api_key=ab!cdefghijklmnopq"),
    ]
    treffer = jev.schluessel_treffer(faelle)
    assert treffer == sorted(name for name, _probe in faelle)


def test_14_12_schluessel_treffer_platzhalter_bleiben_frei(monkeypatch):
    """§14.12 (6f): die Platzhalter aus §14.11 Punkt 4a und zusätzlich
    PASSWORD=$DB_PASS sowie Bearer <token> treffen weiterhin nicht."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "umgebungs-wert-0815")
    platzhalter = [
        ("p_bearer_spitze", "Authorization: Bearer <Schlüssel>"),
        ("p_bearer_env", "Bearer $TYPESAFE_API_KEY"),
        ("p_token_env", "TOKEN=$TYPESAFE_API_KEY"),
        ("p_apikey_ellipsis", "api_key = \"…\""),
        ("p_secret_sterne", "secret: ***"),
        ("p_password_env", "PASSWORD=$DB_PASS"),
        ("p_bearer_token", "Bearer <token>"),
    ]
    assert jev.schluessel_treffer(platzhalter) == []


def test_14_ordne_behaelt_jeden_kandidaten_in_30_faellen():
    """Fester Zufallsseed prüft Erhalt aller Eingaben auch jenseits von max=30."""
    rng = random.Random(20260927)
    for _ in range(30):
        anzahl = rng.randint(1, 45)
        kandidaten = [_kandidat(i, f"marker-{i:03d}") for i in range(anzahl)]
        rng.shuffle(kandidaten)
        punkte = {k["id"]: rng.random() for k in kandidaten}

        def transport(_url, _kopf, rumpf, _timeout):
            state = json.loads(rumpf.decode("utf-8"))["state"]["kandidat"]
            if state.get("quelle") == "kontrolle":
                wert = 0.1
            else:
                marker = re.search(r"marker-(\d+)", state["text"])
                wert = punkte[f"k{int(marker.group(1))}"]
            return 200, {}, _rumpf(_noul(wert))

        ordnung = ordne_kandidaten(
            "Jev Kontext Regeln", kandidaten, max_kandidaten=30,
            kontrolle=True, live=True, transport=transport,
            schluessel=FALSCH_SCHLUESSEL)
        eingang = [k["id"] for k in kandidaten]
        ausgang = [e.id for e in ordnung.eintraege]
        assert len(ausgang) == len(eingang)
        assert set(ausgang) == set(eingang)


def test_126_1c_cli_offline_reihenfolge(tmp_path, monkeypatch):
    """CLI ohne --live gibt die lokale Reihenfolge aus, alles offline."""
    monkeypatch.setenv("TYPESAFE_API_KEY", FALSCH_SCHLUESSEL)
    datei = tmp_path / "kandidaten.json"
    datei.write_text(json.dumps(
        {"format": 1, "auftrag": "Auftrag: Regeln prüfen",
         "kandidaten": [_kandidat(1), _kandidat(2), _kandidat(3)]}),
        encoding="utf-8")
    prozess = _cli_lauf(
        "ordne", str(datei), cwd=str(WURZEL),
        env={"PATH": "/usr/bin:/bin", "TYPESAFE_API_KEY": FALSCH_SCHLUESSEL,
             "PYTHONDONTWRITEBYTECODE": "1"})
    assert prozess.returncode == 0, prozess.stderr
    daten = json.loads(prozess.stdout)
    assert [e["id"] for e in daten["eintraege"]] == ["k1", "k2", "k3"]
    assert all(e["grund"] == "offline" for e in daten["eintraege"])


# --- §13.7 Punkt 2: ordne „-“ liest von stdin (echte Pipe) ---------------------

def _kind_code(*cli_args):
    """Startcode für einen jev-Kindprozess mit Netzsperre (wie _cli_lauf)."""
    argv = ["jev.py", *cli_args]
    return ("import sys as _cli_sys; "
            f"_cli_sys.argv = {json.dumps(argv)};\n"
            + _KIND_SPERRE + "\n"
            f"import runpy as _cli_r; _cli_r.run_path({str(JEV_PFAD)!r}, "
            "run_name='__main__')")


def _wegwerfprojekt_mit_kandidaten(tmp_path, zahl=3):
    """Kleines Projekt unter tmp_path; liefert (ordner, auftrag)."""
    ordner = tmp_path / "projekt"
    (ordner / "docs").mkdir(parents=True)
    (ordner / "KONTEXT.json").write_text(json.dumps(
        {"format": 1, "projekt": "wegwerf", "protokoll": "PROGRESS.md",
         "pflicht_zusatz": [], "zusatz": ["docs/*.md"]},
        ensure_ascii=False), encoding="utf-8")
    woerter = "Auftragsbär Werkzeugfelsen Kontextbeeren"
    for nr in range(1, zahl + 1):
        (ordner / "docs" / f"abschnitt{nr}.md").write_text(
            f"## Abschnitt {nr}\n\n{woerter} im Abschnitt {nr}.\n",
            encoding="utf-8")
    return ordner, f"{woerter} im Projekt wegwerf"


_UMGEBUNG = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def test_137_pipe_kontextpaket_jev_ordne_stdin(tmp_path):
    """Echte Pipe „kontextpaket kandidaten … | jev ordne -“ (§13.7 Punkt 2).

    Zwei Unterprozesse, stdout der Quelle speist stdin von jev; ohne
    --live, kein Netz im Kind (§12.9 Punkt 4). Wegwerfprojekt mit drei
    Zusatz-Kandidaten. Plan-Gate 3: offline ändert die Pipe die
    Reihenfolge nicht, und keine Kandidaten-ID verschwindet.
    """
    ordner, auftrag = _wegwerfprojekt_mit_kandidaten(tmp_path)
    quell_befehl = [sys.executable, str(KONTEXTPAKET_PFAD), "kandidaten",
                    str(ordner), "--auftrag", auftrag]
    # Messender Vorlauf derselben (deterministischen) Quelle: so sehen die
    # Eingabe-IDs aus, die die Pipe im zweiten Lauf tatsächlich liefert.
    vorlage = subprocess.run(quell_befehl, capture_output=True, timeout=60,
                             env=_UMGEBUNG, cwd=str(WURZEL))
    assert vorlage.returncode == 0, vorlage.stderr.decode("utf-8")
    eingang = [k["id"] for k in
               json.loads(vorlage.stdout.decode("utf-8"))["kandidaten"]]
    # Die echte Pipe: stdout der Quelle ist stdin von jev (kein Zwischenfile).
    quelle = subprocess.Popen(quell_befehl, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=_UMGEBUNG,
                              cwd=str(WURZEL))
    senke = subprocess.Popen(
        [sys.executable, "-c", _kind_code("ordne", "-")],
        stdin=quelle.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_UMGEBUNG, cwd=str(WURZEL))
    # Elternkopie der Quell-stdout schließen, damit jev ein EOF bekommt,
    # sobald die Quelle endet.
    quelle.stdout.close()
    quell_fehler = quelle.stderr.read().decode("utf-8")
    quelle.wait()
    ausgabe, senke_fehler = senke.communicate(timeout=60)
    assert quelle.returncode == 0, quell_fehler
    assert senke.returncode == 0, senke_fehler.decode("utf-8")
    daten = json.loads(ausgabe.decode("utf-8"))
    ids = [e["id"] for e in daten["eintraege"]]
    assert len(eingang) >= 3                # mindestens drei Zusatz-Kandidaten
    assert ids == eingang                   # Reihenfolge unverändert, nichts weg
    assert all(e["grund"] == "offline" for e in daten["eintraege"])


def test_137_ordne_stdin_ungueltiges_json_abbruch():
    """„ordne -“ mit kaputtem JSON auf stdin: Exit 2, kein Stumm-Crash."""
    prozess = subprocess.run(
        [sys.executable, "-c", _kind_code("ordne", "-")],
        input=b"{kaputt", capture_output=True, timeout=60,
        env=_UMGEBUNG, cwd=str(WURZEL))
    assert prozess.returncode == 2
    assert "kein gültiges JSON" in prozess.stderr.decode("utf-8")


def test_137_schaetze_stdin_strich_abbruch():
    """„schaetze -“ bleibt Datei-only: „-“ wird abgewiesen (Exit 2, §13.7)."""
    prozess = subprocess.run(
        [sys.executable, "-c", _kind_code("schaetze", "-")],
        input=b"{}", capture_output=True, timeout=60,
        env=_UMGEBUNG, cwd=str(WURZEL))
    assert prozess.returncode == 2
    assert "nur bei „ordne“" in prozess.stderr.decode("utf-8")


# --- §12.6 Punkt 2: Gate 3 — Rückfall sichtbar -------------------------------

def test_126_2_rueckfall_sichtbar():
    """Acht fehlerhafte Antworten → acht saubere Gründe, alle IDs da."""
    schlafe_aufrufe = []
    folge = [
        (200, {}, _rumpf({"fremd": {"type": "noul", "noul": 0.5}})),
        (200, {}, _rumpf({"wichtig": {"type": "choice",
                                      "choice": "a",
                                      "probabilities": {"a": 1.0}}})),
        (200, {}, _rumpf(_noul(1.7))),
        (200, {}, _rumpf({})),
        (200, {}, b"das ist kein json"),
        (500, {}, b"kaputt"),
        (500, {}, b"kaputt"),
        (500, {}, b"kaputt"),
        (422, {}, b"anfrage ungueltig"),
        OSError("netz weg"),
    ]
    transport = _transport_aus_folge(folge)
    kandidaten = [_kandidat(i) for i in range(1, 9)]
    ordnung = ordne_kandidaten(
        "Auftrag: Regeln prüfen", kandidaten, kontrolle=False,
        live=True, transport=transport, schluessel=FALSCH_SCHLUESSEL,
        schlafe=schlafe_aufrufe.append)
    nach_id = {e.id: e for e in ordnung.eintraege}
    assert set(nach_id) == {f"k{i}" for i in range(1, 9)}
    assert len(ordnung.eintraege) == 8
    assert nach_id["k1"].grund.startswith("antwort_ungueltig")
    assert nach_id["k2"].grund.startswith("antwort_ungueltig")
    assert nach_id["k3"].grund.startswith("antwort_ungueltig")
    assert nach_id["k4"].grund.startswith("antwort_ungueltig")
    assert nach_id["k5"].grund == "antwort_kein_json"
    assert nach_id["k6"].grund == "http_500"
    assert nach_id["k7"].grund.startswith("http_422")
    assert nach_id["k8"].grund == "netz"
    assert all(e.status == "nicht_bewertet" for e in ordnung.eintraege)
    assert ordnung.status_gesamt == "nicht_bewertet"


def test_126_2b_ungueltig_einzeln():
    """Unbekannter Schlüssel und falscher Typ landen einzeln in ungueltig."""
    transport = _transport_aus_folge(
        [(200, {}, _rumpf({"fremd": {"type": "noul", "noul": 0.5}}))])
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=transport, schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "nicht_bewertet"
    assert antwort.ungueltig["wichtig"] == "fehlt"
    assert antwort.ungueltig["fremd"] == "nicht_gefragt"
    assert antwort.antworten == {}


def test_126_2c_live_ohne_schluessel_kein_aufruf(monkeypatch):
    """live=True ohne Schlüssel: kein Transportaufruf, Grund klar."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    zaehler = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=zaehler)
    assert antwort == Antwort(status="nicht_bewertet",
                              grund="kein_schluessel", modell=None)
    assert zaehler.aufrufe == []


# --- §12.6 Punkt 3: Wiederholung ---------------------------------------------

def test_126_3_wiederholung_mit_wartezeit():
    """429 mit retry-after 3, dann 200 → bewertet, 3 s gewartet."""
    gewartet = []
    transport = _transport_aus_folge([
        (429, {"retry-after": "3"}, b"zu viele"),
        (200, {}, _rumpf(_noul(0.8))),
    ])
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=transport, schluessel=FALSCH_SCHLUESSEL,
                    schlafe=gewartet.append)
    assert antwort.status == "bewertet"
    assert antwort.antworten == {"wichtig": 0.8}
    assert gewartet == [3]
    assert len(transport.aufrufe) == 2


def test_126_3b_rueckfall_ohne_kopf():
    """500 ohne retry-after: Rückfall 1 s, dann 2 s (§12.3 Punkt 5)."""
    gewartet = []
    transport = _transport_aus_folge([
        (500, {}, b"a"), (500, {}, b"b"), (200, {}, _rumpf(_noul(0.4))),
    ])
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=transport, schluessel=FALSCH_SCHLUESSEL,
                    wiederholungen=2, schlafe=gewartet.append)
    assert antwort.status == "bewertet"
    assert gewartet == [1.0, 2.0]


# --- §12.6 Punkt 4: Anfrageform -----------------------------------------------

def test_126_4_anfrageform():
    """URL, Köpfe, Rumpf mit state/model/questions, Version festgenagelt."""
    transport = _ok_transport()
    ordne_kandidaten("Auftrag: Regeln zum Mergen prüfen", [_kandidat(1)],
                     kontrolle=False, live=True, transport=transport,
                     schluessel=FALSCH_SCHLUESSEL)
    assert len(transport.aufrufe) == 1
    aufruf = transport.aufrufe[0]
    assert aufruf["url"] == "https://api.typesafe.ai/v1/systemone"
    assert aufruf["kopf"]["Authorization"] == f"Bearer {FALSCH_SCHLUESSEL}"
    assert aufruf["kopf"]["Content-Type"] == "application/json"
    rumpf = json.loads(aufruf["rumpf"].decode("utf-8"))
    assert rumpf["model"] == "jev-1.13.0"
    assert rumpf["state"]["auftrag"] == "Auftrag: Regeln zum Mergen prüfen"
    innen = rumpf["state"]["kandidat"]
    # §12.8 Punkt 4: nur quelle bzw. Dateiname, nie der absolute Pfad.
    assert "datei" not in innen
    assert innen["quelle"] == "d1.md"
    assert innen["ueberschrift"] == "Abschnitt 1"
    assert "Regeln und Entscheidungen" in innen["text"]
    wichtig = rumpf["questions"]["wichtig"]
    assert wichtig["type"] == "noul"
    assert "Muss jemand" in wichtig["instructions"]
    assert "true" in wichtig["criteria"] and "false" in wichtig["criteria"]


def test_126_4b_frage_pruefung_vor_senden():
    """Falsche Fragen → ValueError, kein Transportaufruf."""
    zaehler = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    with pytest.raises(ValueError):
        frage({"a": 1}, {"x": {"type": "raten"}}, live=True,
              transport=zaehler, schluessel=FALSCH_SCHLUESSEL)
    with pytest.raises(ValueError):
        frage({"a": 1}, {"x": {"type": "noul"}}, live=True,
              transport=zaehler, schluessel=FALSCH_SCHLUESSEL)
    with pytest.raises(ValueError):
        frage({"a": 1},
              {"x": {"type": "choice", "instructions": "i", "criteria": {}}},
              live=True, transport=zaehler, schluessel=FALSCH_SCHLUESSEL)
    with pytest.raises(ValueError):
        frage({"a": 1},
              {"x": {"type": "score", "instructions": "i",
                     "criteria": ["nur eine"]}},
              live=True, transport=zaehler, schluessel=FALSCH_SCHLUESSEL)
    assert zaehler.aufrufe == []


# --- §12.6 Punkt 5: Negativkontrolle ------------------------------------------

def _kontroll_transport(kontroll_noul, kandidaten_noul=0.9):
    def transport(url, kopf, rumpf, timeout):
        rumpf_obj = json.loads(rumpf.decode("utf-8"))
        if rumpf_obj["state"]["kandidat"].get("quelle") == "kontrolle":
            wert = kontroll_noul
        else:
            wert = kandidaten_noul
        return 200, {}, _rumpf(_noul(wert))
    return transport


def test_126_5_kontrolle_bestanden():
    """Kontrolle 0,05 → nicht unzuverlaessig, Kontrollkandidat fehlt."""
    ordnung = ordne_kandidaten(
        "Auftrag: Regeln prüfen", [_kandidat(1), _kandidat(2)], live=True,
        transport=_kontroll_transport(0.05), schluessel=FALSCH_SCHLUESSEL)
    assert ordnung.status_gesamt == "bewertet"
    assert ordnung.kontrolle == {"noul": 0.05, "bestanden": True}
    assert [e.id for e in ordnung.eintraege] == ["k1", "k2"]


def test_126_5b_kontrolle_verfehlt():
    """Kontrolle 0,6 → unzuverlaessig, Werte bleiben sichtbar."""
    ordnung = ordne_kandidaten(
        "Auftrag: Regeln prüfen", [_kandidat(1)], live=True,
        transport=_kontroll_transport(0.6), schluessel=FALSCH_SCHLUESSEL)
    assert ordnung.status_gesamt == "unzuverlaessig"
    assert ordnung.kontrolle == {"noul": 0.6, "bestanden": False}
    assert len(ordnung.eintraege) == 1
    assert ordnung.eintraege[0].noul == 0.9


# --- §12.6 Punkt 6: Vollständigkeit als Eigenschaft ---------------------------

def test_126_6_vollstaendigkeit_eigenschaft():
    """≥ 50 Zufallsfälle (fester Seed): IDs vollständig, keine Dublette."""
    zufall = random.Random(20260926)
    antwortarten = [
        (200, {}, _rumpf(_noul(0.9))),
        (200, {}, _rumpf(_noul(0.1))),
        (200, {}, _rumpf(_noul(2.5))),
        (200, {}, _rumpf({})),
        (200, {}, b"kaputt"),
        (500, {}, b"kaputt"),
        (422, {}, b"nein"),
        OSError("weg"),
    ]
    for fall in range(60):
        anzahl = zufall.randint(1, 8)
        kandidaten = [_kandidat(i) for i in range(anzahl)]
        folge = [zufall.choice(antwortarten) for _ in range(anzahl)]
        ordnung = ordne_kandidaten(
            "Auftrag: etwas prüfen", kandidaten, kontrolle=False, live=True,
            transport=_transport_aus_folge(folge),
            schluessel=FALSCH_SCHLUESSEL, schlafe=lambda s: None)
        ids_rein = [k["id"] for k in kandidaten]
        ids_raus = [e.id for e in ordnung.eintraege]
        assert len(ids_raus) == len(ids_rein), fall
        assert set(ids_raus) == set(ids_rein), fall
        assert len(set(ids_raus)) == len(ids_raus), fall


# --- §12.6 Punkt 7: Schlüssel geheim ------------------------------------------

def test_126_7_schluessel_geheim():
    """Server spiegelt den Schlüssel im 422-Rumpf: überall ***."""
    rumpf = (f"ungültig, Schlüssel {FALSCH_SCHLUESSEL} falsch".encode("utf-8"))
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_transport_aus_folge([(422, {}, rumpf)]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert FALSCH_SCHLUESSEL not in (antwort.grund or "")
    assert "***" in (antwort.grund or "")
    assert FALSCH_SCHLUESSEL not in repr(antwort)


def test_126_7b_ordnung_ohne_schluessel():
    """Auch repr(Ordnung) enthält den Schlüssel nie."""
    rumpf = f"nein {FALSCH_SCHLUESSEL}".encode("utf-8")
    ordnung = ordne_kandidaten(
        "Auftrag: prüfen", [_kandidat(1)], kontrolle=False, live=True,
        transport=_transport_aus_folge([(422, {}, rumpf)]),
        schluessel=FALSCH_SCHLUESSEL)
    assert FALSCH_SCHLUESSEL not in repr(ordnung)
    assert "***" in repr(ordnung)


# --- §12.6 Punkt 8: Keine Freigabe aus Urteil ----------------------------------

_VERBOTEN = ("frei", "loesch", "bestanden", "schreib")


def test_126_8_keine_freigabe():
    """Nur Bezeichner und Optionsnamen, keine Hilfetexte (§12.8 Punkt 3)."""
    import re
    namen = [n.lower() for n in dir(jev)]
    for name in namen:
        for wort in _VERBOTEN:
            assert wort not in name, name
    for hilfe in (["--help"], ["ordne", "--help"], ["schaetze", "--help"]):
        prozess = _cli_lauf(*hilfe)
        assert prozess.returncode == 0
        optionen = re.findall(r"--[\w-]+", prozess.stdout.lower())
        assert optionen, (hilfe, prozess.stdout)
        for opt in optionen:
            for wort in _VERBOTEN:
                assert wort not in opt, (hilfe, opt)


# --- §12.6 Punkt 9: Größe -----------------------------------------------------

def test_126_9_zu_gross():
    """Kandidat mit 100 000 Zeichen: zu_gross, keine Anfrage, bleibt drin."""
    zaehler = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    gross = {"id": "riesig", "text": "x" * 100_000}
    klein = _kandidat(1)
    ordnung = ordne_kandidaten("Auftrag: prüfen", [gross, klein],
                               kontrolle=False, live=True, transport=zaehler,
                               schluessel=FALSCH_SCHLUESSEL)
    assert len(zaehler.aufrufe) == 1  # nur der kleine Kandidat
    gesendet = json.loads(zaehler.aufrufe[0]["rumpf"].decode("utf-8"))
    assert gesendet["state"]["kandidat"]["text"] == klein["text"]
    nach_id = {e.id: e for e in ordnung.eintraege}
    assert set(nach_id) == {"riesig", "k1"}
    assert nach_id["riesig"].grund == "zu_gross"
    assert nach_id["riesig"].noul is None


# --- Ergänzende Tests: Sortierung, Grenzen, Kosten, CLI ------------------------

def test_sortierung_noul_dann_lokal():
    """Bewertete nach noul absteigend, Gleichstand nach lokalem Rang."""
    kandidaten = [{"id": "a", "text": "a"}, {"id": "b", "text": "b"},
                  {"id": "c", "text": "c"}, {"id": "d", "text": "d"}]
    folge = [(200, {}, _rumpf(_noul(0.3))), (200, {}, _rumpf(_noul(0.9))),
             (200, {}, _rumpf(_noul(0.3))), (200, {}, _rumpf(_noul(9.9)))]
    ordnung = ordne_kandidaten(
        "Auftrag: prüfen", kandidaten, kontrolle=False, live=True,
        transport=_transport_aus_folge(folge), schluessel=FALSCH_SCHLUESSEL)
    assert [e.id for e in ordnung.eintraege] == ["b", "a", "c", "d"]
    assert ordnung.status_gesamt == "teilweise"


def test_doppelte_id_ist_fehler():
    """Doppelte id → ValueError (§12.4 Punkt 1)."""
    with pytest.raises(ValueError):
        ordne_kandidaten("Auftrag", [_kandidat(1), _kandidat(1)])
    with pytest.raises(ValueError):
        ordne_kandidaten("Auftrag", [{"text": "ohne id"}])


def test_ueber_max_bleibt_drin():
    """Jenseits von max_kandidaten: ueber_max, keine Anfrage dafür."""
    transport = _ok_transport()
    ordnung = ordne_kandidaten("Auftrag", [_kandidat(1), _kandidat(2)],
                               max_kandidaten=1, kontrolle=False, live=True,
                               transport=transport,
                               schluessel=FALSCH_SCHLUESSEL)
    assert len(transport.aufrufe) == 1
    assert ordnung.eintraege[1].grund == "ueber_max"
    assert ordnung.status_gesamt == "teilweise"


def test_modell_abweichend_als_hinweis():
    """Anderes Modell als angefragt: Hinweis, Antworten gelten trotzdem."""
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_ok_transport(0.7, modell="jev-1.14.0"),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "bewertet"
    assert antwort.antworten == {"wichtig": 0.7}
    assert antwort.hinweise == ("modell_abweichend: jev-1.14.0",)
    assert antwort.modell == "jev-1.14.0"


def test_choice_und_score_pruefung():
    """Choice- und Score-Antworten werden gegen die Frage geprüft."""
    fragen = {
        "wahl": {"type": "choice", "instructions": "wähle",
                 "criteria": {"a": "erstens", "b": "zweitens"}},
        "punkte": {"type": "score", "instructions": "werte",
                   "criteria": ["schlecht", "mittel", "gut"]},
    }
    gut = {"wahl": {"type": "choice", "choice": "a",
                    "probabilities": {"a": 0.7, "b": 0.3}},
           "punkte": {"type": "score", "score": 1.5}}
    antwort = frage({"s": 1}, fragen, live=True,
                    transport=_transport_aus_folge(
                        [(200, {}, _rumpf(gut))]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "bewertet"
    assert antwort.antworten == {"wahl": "a", "punkte": 1.5}
    schlecht = {"wahl": {"type": "choice", "choice": "c",
                         "probabilities": {"a": 0.5, "b": 0.5}},
                "punkte": {"type": "score", "score": 7}}
    antwort = frage({"s": 1}, fragen, live=True,
                    transport=_transport_aus_folge(
                        [(200, {}, _rumpf(schlecht))]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "nicht_bewertet"
    assert antwort.ungueltig == {"wahl": "choice_unbekannt",
                                 "punkte": "score_ausserhalb"}


def test_schaetze_rechnet_ohne_netz():
    """schaetze: Anfragen, Zeichen, Token, Dollar — ohne einen Aufruf."""
    kosten = schaetze_kosten("Auftrag: Regeln prüfen",
                             [_kandidat(1), _kandidat(2)])
    assert kosten["anfragen"] == 3  # zwei Kandidaten plus Kontrolle
    assert kosten["zeichen"] > 0
    assert kosten["token_geschaetzt"] == (kosten["zeichen"] + 2) // 3
    assert kosten["usd_geschaetzt"] == pytest.approx(
        kosten["token_geschaetzt"] * 0.042 / 1_000_000)
    kosten_ohne = schaetze_kosten("Auftrag", [_kandidat(1)], kontrolle=False)
    assert kosten_ohne["anfragen"] == 1


def test_schaetze_cli(tmp_path):
    """schaetze-CLI gibt die Kostenschätzung als JSON aus."""
    datei = tmp_path / "kandidaten.json"
    datei.write_text(json.dumps(
        {"format": 1, "auftrag": "Auftrag", "kandidaten": [_kandidat(1)]}),
        encoding="utf-8")
    prozess = _cli_lauf("schaetze", str(datei))
    assert prozess.returncode == 0, prozess.stderr
    daten = json.loads(prozess.stdout)
    assert daten["anfragen"] == 2
    assert daten["usd_geschaetzt"] >= 0


def test_cli_live_ohne_schluessel_abbruch(tmp_path, monkeypatch):
    """--live ohne Schlüssel: Exit 2 mit Klartext, keine Ausgabe."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    datei = tmp_path / "kandidaten.json"
    datei.write_text(json.dumps(
        {"format": 1, "auftrag": "Auftrag", "kandidaten": [_kandidat(1)]}),
        encoding="utf-8")
    import os
    umgebung = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                "HOME": os.environ.get("HOME", "/tmp")}
    prozess = _cli_lauf("ordne", str(datei), "--live", env=umgebung)
    assert prozess.returncode == 2
    assert "TYPESAFE_API_KEY" in prozess.stderr
    assert prozess.stdout == ""


def test_cli_falsche_datei(tmp_path):
    """Unlesbare oder falsche Kandidatendatei: Exit 2."""
    for name, inhalt in (("kein.json", None), ("falsch.json", "{kein"),
                         ("leer.json", "[]")):
        pfad = tmp_path / name
        if inhalt is not None:
            pfad.write_text(inhalt, encoding="utf-8")
        prozess = _cli_lauf("ordne", str(pfad))
        assert prozess.returncode == 2, name


# --- §12.7 Nachbesserung ------------------------------------------------------

def test_127_1_kein_transport_ohne_live_trotz_transports():
    """§12.7 Punkt 1: ohne live=True kein Transportaufruf, auch hereingereicht nicht."""
    transport = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=False,
                    transport=transport, schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "nicht_bewertet"
    assert antwort.grund == "offline"
    assert transport.aufrufe == []
    assert "kein" in jev.frage.__doc__.lower() or "live" in jev.frage.__doc__


def test_127_2_maskieren_vor_kuerzen():
    """§12.7 Punkt 2: Schlüssel ab Zeichen 290 → kein Präfix (≥ 6) im grund."""
    rumpf = ("x" * 290 + FALSCH_SCHLUESSEL + " Rest").encode("utf-8")
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_transport_aus_folge([(422, {}, rumpf)]),
                    schluessel=FALSCH_SCHLUESSEL)
    grund = antwort.grund or ""
    assert FALSCH_SCHLUESSEL not in grund
    for laenge in range(6, len(FALSCH_SCHLUESSEL) + 1):
        for beginn in range(len(FALSCH_SCHLUESSEL) - laenge + 1):
            assert FALSCH_SCHLUESSEL[beginn:beginn + laenge] not in grund, \
                FALSCH_SCHLUESSEL[beginn:beginn + laenge]
    assert "***" in grund


def test_127_3_kontrolle_fehlt_wird_unzuverlaessig():
    """§12.7 Punkt 3: Kandidat ok, Kontrolle 500×3 → unzuverlaessig mit grund."""
    folge = [(200, {}, _rumpf(_noul(0.9))),
             (500, {}, b"kaputt"), (500, {}, b"kaputt"), (500, {}, b"kaputt")]
    ordnung = ordne_kandidaten(
        "Auftrag: prüfen", [_kandidat(1)], live=True,
        transport=_transport_aus_folge(folge), schluessel=FALSCH_SCHLUESSEL,
        schlafe=lambda s: None)
    assert ordnung.status_gesamt == "unzuverlaessig"
    assert ordnung.kontrolle["noul"] is None
    assert ordnung.kontrolle["bestanden"] is None
    assert ordnung.kontrolle["grund"] == "http_500"
    assert [e.id for e in ordnung.eintraege] == ["k1"]


def test_127_3b_offline_bleibt_nicht_bewertet():
    """§12.7 Punkt 3: kein Kandidat bewertet (offline) → nicht_bewertet."""
    transport = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    ordnung = ordne_kandidaten("Auftrag: prüfen", [_kandidat(1)],
                               transport=transport,
                               schluessel=FALSCH_SCHLUESSEL)
    assert transport.aufrufe == []
    assert ordnung.status_gesamt == "nicht_bewertet"


def test_127_4_probabilities_bereich():
    """§12.7 Punkt 4: Summe 1 reicht nicht — Einzelwerte müssen in [0, 1] liegen."""
    fragen = {"wahl": {"type": "choice", "instructions": "wähle",
                       "criteria": {"a": "erstens", "b": "zweitens"}}}
    schlecht = {"wahl": {"type": "choice", "choice": "a",
                         "probabilities": {"a": -0.5, "b": 1.5}}}
    antwort = frage({"s": 1}, fragen, live=True,
                    transport=_transport_aus_folge(
                        [(200, {}, _rumpf(schlecht))]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "nicht_bewertet"
    assert antwort.ungueltig == {"wahl": "probabilities_falsch"}
    punkte_fragen = {"punkte": {"type": "score", "instructions": "werte",
                                "criteria": ["schlecht", "mittel", "gut"]}}
    schlecht_score = {"punkte": {"type": "score", "score": 1,
                                 "probabilities": {"0": -0.2, "1": 0.6,
                                                   "2": 0.6}}}
    antwort = frage({"s": 1}, punkte_fragen, live=True,
                    transport=_transport_aus_folge(
                        [(200, {}, _rumpf(schlecht_score))]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "nicht_bewertet"
    assert antwort.ungueltig == {"punkte": "probabilities_falsch"}


# --- §12.8 Nachtrag V1 (U2c) --------------------------------------------------
# (test_128_3 stand hier; ersetzt durch test_128_3_echte_bezeichner_und_optionen
# unten unter §12.9 Punkt 5.)


def test_128_4_keine_maschinenpfade():
    """§12.8 Punkt 4: keine privaten absoluten Pfade im Rumpf; quelle statt datei."""
    kandidaten = [
        {"id": "rel", "quelle": "docs/x.md",
         "datei": "/" + "home/example/projekt/docs/x.md",
         "ueberschrift": "Relativ", "text": "Regeln zum Auftrag."},
        {"id": "abs1", "datei": "/" + "home/example/projekt/docs/y.md",
         "ueberschrift": "Absolut", "text": "Entscheidung zum Auftrag."},
        {"id": "abs2", "datei": "/" + "media/daten/projekt/docs/z.md",
         "text": "Erfahrung zum Auftrag."},
    ]
    transport = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))] * 4)
    ordne_kandidaten("Auftrag: Pfade prüfen", kandidaten, kontrolle=True,
                     live=True, transport=transport,
                     schluessel=FALSCH_SCHLUESSEL)
    assert len(transport.aufrufe) == 4  # drei Kandidaten plus Kontrolle
    for aufruf in transport.aufrufe:
        roh = aufruf["rumpf"].decode("utf-8")
        assert "/" + "home/" not in roh
        assert "/" + "media/" not in roh
        assert json.loads(roh)["state"]["kandidat"].get("datei") is None
    gesendet = [json.loads(a["rumpf"].decode("utf-8"))["state"]["kandidat"]
                for a in transport.aufrufe[:3]]
    assert gesendet[0]["quelle"] == "docs/x.md"  # quelle gewinnt vor datei
    assert gesendet[1]["quelle"] == "y.md"  # ohne quelle: nur Dateiname
    assert gesendet[2]["quelle"] == "z.md"
    kontrolle = json.loads(
        transport.aufrufe[3]["rumpf"].decode("utf-8"))["state"]["kandidat"]
    assert kontrolle["quelle"] == "kontrolle"


def test_128_5_modell_nur_versioniert():
    """§12.8 Punkt 5: Hinweis nur bei angefragtem ^jev-\\d+\\.\\d+\\.\\d+$."""
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_ok_transport(0.7, modell="jev-1.14.0"),
                    schluessel=FALSCH_SCHLUESSEL, modell="jev-1.13.0")
    assert antwort.hinweise == ("modell_abweichend: jev-1.14.0",)
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_ok_transport(0.7, modell="jev-1.13.0"),
                    schluessel=FALSCH_SCHLUESSEL, modell="jev-latest")
    assert antwort.status == "bewertet"
    assert antwort.hinweise == ()
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_ok_transport(0.7, modell="jev-1.13.0"),
                    schluessel=FALSCH_SCHLUESSEL, modell="mein-modell")
    assert antwort.status == "bewertet"
    assert antwort.hinweise == ()
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_ok_transport(0.7, modell="jev-1.13.0"),
                    schluessel=FALSCH_SCHLUESSEL, modell="jev-1.13.0")
    assert antwort.hinweise == ()


def test_128_8_schaetze_wie_frage():
    """§12.8 Punkt 8: schaetze zählt dieselben Rümpfe, die frage sendet."""
    kandidaten = [
        {"id": "a", "quelle": "docs/a.md",
         "datei": "/" + "home/example/projekt/docs/a.md",
         "ueberschrift": "A", "text": "Regeln zum Auftrag."},
        {"id": "b", "datei": "/" + "media/daten/projekt/docs/b.md",
         "text": "Entscheidung zum Auftrag."},
    ]
    auftrag = "Auftrag: Kosten prüfen"
    transport = _transport_aus_folge([
        (200, {}, _rumpf(_noul(0.9))),
        (200, {}, _rumpf(_noul(0.8))),
        (200, {}, _rumpf(_noul(0.05))),
    ])
    ordnung = ordne_kandidaten(auftrag, kandidaten, kontrolle=True, live=True,
                               transport=transport,
                               schluessel=FALSCH_SCHLUESSEL)
    assert ordnung.status_gesamt == "bewertet"
    gesendet = [a["rumpf"].decode("utf-8") for a in transport.aufrufe]
    assert len(gesendet) == 3
    for roh in gesendet:
        assert "/" + "home/" not in roh
        assert "/" + "media/" not in roh
    kosten = schaetze_kosten(auftrag, kandidaten, kontrolle=True)
    assert kosten["anfragen"] == 3
    assert kosten["zeichen"] == sum(len(r) for r in gesendet)
    # Absoluter Pfad kostet dasselbe wie der Dateiname allein.
    nur_namen = [
        {"id": "a", "quelle": "docs/a.md", "ueberschrift": "A",
         "text": "Regeln zum Auftrag."},
        {"id": "b", "quelle": "b.md", "text": "Entscheidung zum Auftrag."},
    ]
    assert (schaetze_kosten(auftrag, nur_namen)["zeichen"]
            == kosten["zeichen"])


# --- §12.9 Nachtrag R2 (U2e) ----------------------------------------------------

def test_129_1_schluessel_in_modell_und_antwortschluessel_geheim(tmp_path):
    """§12.9 Punkt 1: Schlüssel in `model` und als Antwortschlüssel → überall ***."""
    fremd = {FALSCH_SCHLUESSEL: {"type": "noul", "noul": 0.5}}
    spiegel = _rumpf({**_noul(0.8), **fremd}, modell=FALSCH_SCHLUESSEL)
    antwort = frage({"a": 1}, {"wichtig": jev.WICHTIG_FRAGE}, live=True,
                    transport=_transport_aus_folge([(200, {}, spiegel)]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.modell != FALSCH_SCHLUESSEL
    assert "***" in (antwort.modell or "")
    assert FALSCH_SCHLUESSEL not in (antwort.grund or "")
    assert FALSCH_SCHLUESSEL not in repr(antwort)
    # Ordnungs-Ebene plus CLI-JSON-Serialisierung (_ordnung_json).
    transport = _transport_aus_folge([(200, {}, spiegel)])
    ordnung = ordne_kandidaten("Auftrag: prüfen", [_kandidat(1)], kontrolle=False,
                               live=True, transport=transport,
                               schluessel=FALSCH_SCHLUESSEL)
    assert FALSCH_SCHLUESSEL not in repr(ordnung)
    cli_json = json.dumps(jev._ordnung_json(ordnung), ensure_ascii=False)
    assert FALSCH_SCHLUESSEL not in cli_json
    # Echte CLI (Kindprozess mit Sperre, offline): Schlüssel aus der Umgebung
    # steht nie in der JSON-Ausgabe.
    datei = tmp_path / "kandidaten.json"
    datei.write_text(json.dumps(
        {"format": 1, "auftrag": "Auftrag", "kandidaten": [_kandidat(1)]}),
        encoding="utf-8")
    prozess = _cli_lauf(
        "ordne", str(datei),
        env={"PATH": "/usr/bin:/bin", "TYPESAFE_API_KEY": FALSCH_SCHLUESSEL,
             "PYTHONDONTWRITEBYTECODE": "1"})
    assert prozess.returncode == 0, prozess.stderr
    assert FALSCH_SCHLUESSEL not in prozess.stdout


@pytest.mark.parametrize("quelle", ["/" + "home/x/a.md", "~/a.md", "../../a.md",
                                    "C:\\x\\a.md"])
def test_129_2_absolute_quelle_wird_dateiname(quelle):
    """§12.9 Punkt 2: absolute/ausbrechende quelle → nur der Dateiname reist."""
    kandidat = {"id": "q", "quelle": quelle, "text": "Regeln zum Auftrag."}
    transport = _transport_aus_folge([(200, {}, _rumpf(_noul(0.9)))])
    ordne_kandidaten("Auftrag: Pfade prüfen", [kandidat], kontrolle=False,
                     live=True, transport=transport,
                     schluessel=FALSCH_SCHLUESSEL)
    assert len(transport.aufrufe) == 1
    roh = transport.aufrufe[0]["rumpf"].decode("utf-8")
    assert json.loads(roh)["state"]["kandidat"]["quelle"] == "a.md"
    assert quelle not in roh
    # schaetze_kosten zählt denselben Rumpf (dieselbe Normalisierung).
    kosten = schaetze_kosten("Auftrag: Pfade prüfen", [kandidat],
                             kontrolle=False)
    assert kosten["anfragen"] == 1
    assert kosten["zeichen"] == len(roh)


def test_129_3_score_probabilities_schluessel():
    """§12.9 Punkt 3: fremde/fehlende Stufenschlüssel → verworfen."""
    fragen = {"punkte": {"type": "score", "instructions": "werte",
                         "criteria": ["schlecht", "mittel", "gut"]}}
    faelle = ({"fremd": 1.0},
              {"0": 0.5, "1": 0.5},
              {"0": 0.4, "1": 0.3, "2": 0.3, "3": 0.0},
              {"0": 1.0, "1": 0.0, "2": 0.0, "fremd": 0.0})
    for schlecht in faelle:
        roh = {"punkte": {"type": "score", "score": 1,
                          "probabilities": schlecht}}
        antwort = frage({"s": 1}, fragen, live=True,
                        transport=_transport_aus_folge(
                            [(200, {}, _rumpf(roh))]),
                        schluessel=FALSCH_SCHLUESSEL)
        assert antwort.status == "nicht_bewertet", schlecht
        assert antwort.ungueltig == {"punkte": "probabilities_falsch"}, schlecht
    # Kontrolle: exakte Schlüssel {"0", "1", "2"} werden bewertet.
    gut = {"punkte": {"type": "score", "score": 1,
                      "probabilities": {"0": 0.2, "1": 0.5, "2": 0.3}}}
    antwort = frage({"s": 1}, fragen, live=True,
                    transport=_transport_aus_folge([(200, {}, _rumpf(gut))]),
                    schluessel=FALSCH_SCHLUESSEL)
    assert antwort.status == "bewertet"
    assert antwort.antworten == {"punkte": 1.0}


def test_129_4_sperre_greift_im_kindprozess():
    """§12.9 Punkt 4: die Kindprozess-Sperre blockt connect und urlopen."""
    for versuch in ("import socket; socket.socket().connect(('127.0.0.1', 9))",
                    "import urllib.request; "
                    "urllib.request.urlopen('http://127.0.0.1:9/')"):
        code = _KIND_SPERRE + "\n" + versuch
        prozess = subprocess.run([sys.executable, "-c", code],
                                 capture_output=True, text=True, timeout=60)
        assert prozess.returncode != 0, versuch
        assert "Netzaufruf im Kindprozess" in prozess.stderr, versuch


def test_128_3_echte_bezeichner_und_optionen():
    """§12.9 Punkt 5 (ersetzt den tautologischen Test): echte Bezeichner aus
    dir(jev), echte Optionsnamen aus der --help-Ausgabe des CLI-Parsers."""
    import re
    namen = [n.lower() for n in dir(jev)]
    assert namen, "dir(jev) ist leer"
    for name in namen:
        for wort in _VERBOTEN:
            assert wort not in name, name
    gefunden = set()
    for hilfe in (["--help"], ["ordne", "--help"], ["schaetze", "--help"]):
        prozess = _cli_lauf(*hilfe)
        assert prozess.returncode == 0, (hilfe, prozess.stderr)
        optionen = re.findall(r"--[\w-]+", prozess.stdout.lower())
        assert optionen, (hilfe, prozess.stdout)
        gefunden.update(optionen)
        for opt in optionen:
            for wort in _VERBOTEN:
                assert wort not in opt, (hilfe, opt)
    for erwartet in ("--live", "--max", "--ohne-kontrolle", "--modell"):
        assert erwartet in gefunden, sorted(gefunden)


# --- §15.2: Kleinteile nach der Gegenprüfung R4 -------------------------------

def test_15_2_schluessel_treffer_deutsche_labels(monkeypatch):
    """§15.2 Punkt 1: die deutschen Label „passwort“ und „kennwort“ treffen —
    mit Namensende, ohne Groß-/Kleinschreibung, gleiche Wertregel."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "umgebungs-wert-0815")
    faelle = [
        ("passwort_gross", "PASSWORT=" + "Xk9mQ2vL7pR4tN8w"),
        ("db_passwort", "DB_PASSWORT: \"Xk9mQ2vL7pR4tN8w\""),
        ("kennwort", "Kennwort=Xk9mQ2vL7pR4tN8w"),
        ("kennwort_namensende", "DB_KENNWORT=Xk9mQ2vL7pR4tN8w"),
    ]
    treffer = jev.schluessel_treffer(faelle)
    assert treffer == sorted(name for name, _probe in faelle)


def test_15_2_schluessel_treffer_deutsche_platzhalter_frei(monkeypatch):
    """§15.2 Punkt 1: die Platzhalterregel gilt auch für die neuen Label —
    $-Wert, <platzhalter> und zu kurze Werte treffen weiterhin nicht."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "umgebungs-wert-0815")
    platzhalter = [
        ("p_passwort_env", "PASSWORT=$DB_PASS"),
        ("p_kennwort_spitze", "Kennwort: <geheim>"),
        ("p_passwort_kurz", "Passwort=kurz"),
    ]
    assert jev.schluessel_treffer(platzhalter) == []


def test_15_2_ordne_stdin_utf8_bom():
    """§15.2 Punkt 5: „ordne -“ nimmt UTF-8 mit führendem BOM an (R4-3)."""
    eingabe = b"\xef\xbb\xbf" + json.dumps(
        {"format": 1, "auftrag": "Auftragsbär Werkzeugfelsen",
         "kandidaten": [_kandidat(1), _kandidat(2)]}).encode("utf-8")
    prozess = subprocess.run(
        [sys.executable, "-c", _kind_code("ordne", "-")],
        input=eingabe, capture_output=True, timeout=60,
        env=_UMGEBUNG, cwd=str(WURZEL))
    assert prozess.returncode == 0, prozess.stderr.decode("utf-8", "replace")
    ids = [e["id"] for e in
           json.loads(prozess.stdout.decode("utf-8"))["eintraege"]]
    assert ids == ["k1", "k2"]


def test_15_2_ordne_und_schaetze_datei_utf8_bom(tmp_path):
    """§15.2 Punkt 5: auch eine Kandidatendatei mit BOM wird gelesen —
    bei „ordne“ und bei „schaetze“ (beide über _lies_kandidaten)."""
    datei = tmp_path / "kandidaten.json"
    datei.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"format": 1, "auftrag": "Auftragsbär",
         "kandidaten": [_kandidat(1)]}).encode("utf-8"))
    prozess = _cli_lauf("ordne", str(datei))
    assert prozess.returncode == 0, prozess.stderr
    ids = [e["id"] for e in json.loads(prozess.stdout)["eintraege"]]
    assert ids == ["k1"]
    prozess = _cli_lauf("schaetze", str(datei))
    assert prozess.returncode == 0, prozess.stderr
    assert json.loads(prozess.stdout)["anfragen"] == 2


def test_15_2_ordne_stdin_bom_mit_kaputtem_json_bleibt_exit2():
    """§15.2 Punkt 5: das BOM wird nur am Anfang entfernt — kaputtes JSON
    dahinter bleibt Exit 2 wie heute."""
    prozess = subprocess.run(
        [sys.executable, "-c", _kind_code("ordne", "-")],
        input=b"\xef\xbb\xbf{kaputt", capture_output=True, timeout=60,
        env=_UMGEBUNG, cwd=str(WURZEL))
    assert prozess.returncode == 2
    assert "kein gültiges JSON" in prozess.stderr.decode("utf-8")

"""Offline tests for the U4 Jev measurement tool (scripts/measurements/).

Cleaned copy of the project's test suite for the measurement tool, adapted to
this repository layout. Key-shaped test strings are assembled at runtime so the
publication guard never sees a key-shaped literal."""
import importlib.util
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "measurements" / "u4_jev_messung.py"
FAKE_KEY = "sk-" + "a" * 26
spec = importlib.util.spec_from_file_location("u4_jev_messung", TOOL)
u4 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = u4
spec.loader.exec_module(u4)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access forbidden in U4 tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def test_secret_scan_uses_shared_jev_implementation(monkeypatch):
    gesehen = []

    def shared(texts):
        gesehen.extend(texts)
        return ["label"]

    monkeypatch.setattr(u4.jev, "schluessel_treffer", shared)
    texts = [("label", "synthetic text")]
    assert u4._key_hits(texts) == ["label"]
    assert gesehen == texts


def test_measurements_hand_checked_top_overlap_counts_and_border_values():
    refs = {f"c{i}": {"relevant": i in {0, 2, 4}} for i in range(6)}
    local = [{"id": f"c{i}"} for i in range(6)]
    order = {"eintraege": [
        {"id": "c2", "status": "bewertet", "noul": .5},
        {"id": "c1", "status": "bewertet", "noul": .6},
        {"id": "c0", "status": "bewertet", "noul": .4},
        {"id": "c3", "status": "bewertet", "noul": .6},
        {"id": "c4", "status": "nicht_bewertet", "noul": None},
        {"id": "c5", "status": "bewertet", "noul": .5},
    ]}
    got = u4._metrics(refs, local, order, k=(2, 4))
    assert got == {"lokal_top5": 1, "lokal_top10": 2, "jev_top5": 1,
                   "jev_top10": 2, "uebersehen": 1, "fehlalarm": 3,
                   "grenzfaelle": 5, "nicht_bewertet": 1, "gegenprobe": None}
    assert u4._stability(
        [{"id":"a", "status":"bewertet", "noul":.4}, {"id":"b", "status":"bewertet", "noul":.8}],
        [{"id":"a", "status":"bewertet", "noul":.6}, {"id":"b", "status":"bewertet", "noul":.7}]
    ) == {"paarzahl": 2, "gleiche_seite": .5, "mittlere_delta": pytest.approx(.15)}


def test_messen_without_live_returns_2_without_transport(tmp_path):
    calls = []
    assert u4.messe(tmp_path, transport=lambda *a: calls.append(a), live=False) == 2
    assert not calls


def test_cli_messen_without_live_is_exit_2_under_child_network_block(tmp_path):
    code = "\n".join([
        "import socket, urllib.request, sys, runpy",
        "def blocked(*a, **k): raise AssertionError('network access forbidden')",
        "socket.socket.connect = blocked",
        "urllib.request.urlopen = blocked",
        f"sys.argv = ['u4_jev_messung.py', 'messen', '--ordner', {str(tmp_path)!r}]",
        f"runpy.run_path({str(TOOL)!r}, run_name='__main__')",
    ])
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert child.returncode == 2
    assert "Traceback" not in child.stderr
    assert "nur mit --live" in child.stderr


def test_prepare_sheet_is_blind_and_seeded(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    dump(project / "KONTEXT.json", {"format":1, "projekt":"p", "protokoll":"PROGRESS.md",
         "stand":None, "pflicht_zusatz":[], "zusatz":["docs/*.md"]})
    (project / "docs").mkdir()
    (project / "docs" / "source.md").write_text(
        "# Alpha\nAuftrag Regeln und Index-Check.\n\n# Beta\nAuftrag Regeln und Index-Check.\n\n# Gamma\nAuftrag Regeln und Index-Check.\n\n# Delta\nAuftrag Regeln und Index-Check.",
        encoding="utf-8")
    folder = tmp_path / "u4"
    dump(folder / "auftraege.json", {"format":1,"auftraege":[{"id":"A1","text":"Auftrag Regeln Index-Check"}]})
    u4.vorbereiten(project, ordner=folder)
    first = (folder / "boegen/bogen-A1.md").read_text(encoding="utf-8")
    u4.vorbereiten(project, ordner=folder, ueberschreiben=True)
    assert first == (folder / "boegen/bogen-A1.md").read_text(encoding="utf-8")
    assert "Muss jemand, der den Auftrag ausführt, diesen Abschnitt kennen?" in first
    assert not any(x in first.lower() for x in ("punkte:", "lokal", "z-", "/home/", "/media/"))
    sheet_order = [first.index(f"### {name}") for name in ("Alpha", "Beta", "Gamma", "Delta")]
    assert sorted(sheet_order) != sheet_order
    with pytest.raises(FileExistsError):
        u4.vorbereiten(project, ordner=folder)


def _reference_fixture(folder):
    dump(folder / "auftraege.json", {"format":1, "auftraege":[{"id":"A1", "text":"test"}]})
    dump(folder / "boegen/schluessel-A1.json", {"B01":"c1", "B02":"c2"})
    dump(folder / "referenz/beurteiler-1.json", {"A1":{"B01":{"relevant":True,"grund":"g1"},"B02":{"relevant":True,"grund":"g2"}}})
    dump(folder / "referenz/beurteiler-2.json", {"A1":{"B01":{"relevant":False,"grund":"h1"},"B02":{"relevant":True,"grund":"h2"}}})


def test_reference_strife_needs_decision_and_reports_agreement(tmp_path, capsys):
    _reference_fixture(tmp_path)
    assert u4.referenz(tmp_path) == 1
    assert not (tmp_path / "referenz.json").exists()
    assert "B01" in capsys.readouterr().out
    dump(tmp_path / "referenz/entscheid.json", {"c1":{"relevant":True,"grund":"nachgelesen"}})
    assert u4.referenz(tmp_path) == 0
    result = json.loads((tmp_path / "referenz.json").read_text())
    assert result["auftraege"]["A1"]["c1"]["strittig"] is True
    assert result["uebereinstimmung"] == {"A1": .5, "gesamt": .5}
    dump(tmp_path / "referenz/beurteiler-2.json", {"A1":{"B02":{"relevant":True,"grund":"ok"}}})
    with pytest.raises(ValueError, match="fehlendes"):
        u4.referenz(tmp_path)


def _tracked_ready(folder, *, text="safe context", with_probe=False):
    job = {"id":"A1", "text":"sample contract"}
    dump(folder / "auftraege.json", {"format":1,"auftraege":[job]})
    candidate = {"id":"c1", "datei":"source.md", "quelle":"source.md", "ueberschrift":"Header", "text":text}
    dump(folder / "kandidaten-A1.json", {"format":1,"auftrag":job["text"],"kandidaten":[candidate]})
    dump(folder / "referenz.json", {"format":1,"auftraege":{"A1":{"c1":{"relevant":True}}}})
    dump(folder / "gegenprobe.json", {"A1":None})
    (folder / "source.md").write_text("# Source\nA safe paragraph with no matching words.", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    subprocess.run(["git", "add", "."], cwd=folder, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@e", "commit", "-qm", "fixture"], cwd=folder, check=True)
    return candidate


def test_uncommitted_frozen_data_blocks_before_transport(tmp_path):
    _tracked_ready(tmp_path)
    with (tmp_path / "referenz.json").open("a") as f: f.write(" ")
    calls = []
    assert u4.messe(tmp_path, live=True, transport=lambda *a: calls.append(a)) == 1
    assert not calls


def test_secret_scan_redacts_match_and_blocks(tmp_path, capsys):
    _tracked_ready(tmp_path, text="sensitive " + FAKE_KEY)
    calls = []
    assert u4.messe(tmp_path, live=True, transport=lambda *a: calls.append(a)) == 1
    message = capsys.readouterr().err
    assert "Schlüsselmuster" in message
    assert FAKE_KEY not in message
    assert not calls


def _reply(noul=.8, tokens=1):
    body = {"model":"jev-1.13.0", "answers":{"wichtig":{"type":"noul", "noul":noul}},
            "usage":{"input_tokens":tokens,"output_tokens":1}}
    return 200, {}, json.dumps(body).encode()


def test_cost_ceiling_keeps_first_raw_and_stops(tmp_path):
    _tracked_ready(tmp_path)
    calls = []
    def expensive(*args):
        calls.append(args)
        return _reply(tokens=10_000_000)
    assert u4.messe(tmp_path, live=True, obergrenze_usd=.10, transport=expensive, schluessel="test-key-for-cost") == 1
    assert len(calls) == 1
    assert (tmp_path / "roh/L1-A1.json").exists()


def test_counterprobe_matching_searchword_blocks(tmp_path):
    _tracked_ready(tmp_path)
    dump(tmp_path / "gegenprobe.json", {"A1":{"datei":str(tmp_path / "source.md"),"zeile":1,"bis":2}})
    (tmp_path / "source.md").write_text("# sample\ncontract evidence", encoding="utf-8")
    subprocess.run(["git", "add", "source.md", "gegenprobe.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@e", "commit", "-qm", "probe"], cwd=tmp_path, check=True)
    calls=[]
    assert u4.messe(tmp_path, live=True, transport=lambda *a: calls.append(a)) == 1
    assert not calls


def test_counterprobe_keyword_punktzahl_is_not_a_special_search_word(tmp_path):
    dump(tmp_path / "auftraege.json", {"format": 1, "auftraege": [{"id": "A1", "text": "sample contract"}]})
    source = tmp_path / "source.md"
    source.write_text("# Not related\nPunktzahl wird hier nur erwähnt.", encoding="utf-8")
    dump(tmp_path / "gegenprobe.json", {"A1": {"datei": str(source), "zeile": 1, "bis": 2}})
    probe = u4._gegenprobe(tmp_path, [{"id": "A1", "text": "sample contract"}])["A1"]
    assert probe["text"] == "# Not related\nPunktzahl wird hier nur erwähnt."


def test_counterprobe_secret_is_scanned_and_redacted_before_transport(tmp_path, capsys):
    _tracked_ready(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# harmless\nprivate " + FAKE_KEY, encoding="utf-8")
    dump(tmp_path / "gegenprobe.json", {"A1": {"datei": str(source), "zeile": 1, "bis": 2}})
    subprocess.run(["git", "add", "source.md", "gegenprobe.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@e", "commit", "-qm", "probe"], cwd=tmp_path, check=True)
    calls = []
    assert u4.messe(tmp_path, live=True, schluessel="test-live-key", transport=lambda *a: calls.append(a)) == 1
    message = capsys.readouterr().err
    assert "Schlüsselmuster" in message
    assert FAKE_KEY not in message
    assert not calls


def test_missing_live_key_fails_without_transport_or_raw_files(tmp_path, monkeypatch, capsys):
    _tracked_ready(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []
    assert u4.messe(tmp_path, live=True, transport=lambda *a: calls.append(a)) == 1
    assert "TYPESAFE_API_KEY fehlt" in capsys.readouterr().err
    assert not calls
    assert not (tmp_path / "roh").exists()


def test_reference_candidate_id_mismatch_blocks_before_transport(tmp_path, capsys):
    _tracked_ready(tmp_path)
    dump(tmp_path / "referenz.json", {"format": 1, "auftraege": {"A1": {"other": {"relevant": True}}}})
    subprocess.run(["git", "add", "referenz.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@e", "commit", "-qm", "bad reference"], cwd=tmp_path, check=True)
    calls = []
    assert u4.messe(tmp_path, live=True, schluessel="test-live-key", transport=lambda *a: calls.append(a)) == 1
    assert "Referenz-IDs" in capsys.readouterr().err
    assert not calls


def test_counterprobe_rank_and_top_counts_exclude_counterprobe(tmp_path):
    dump(tmp_path / "auftraege.json", {"format": 1, "auftraege": [{"id": "A1", "text": "sample"}]})
    local = [{"id": f"c{i}"} for i in range(6)]
    dump(tmp_path / "kandidaten-A1.json", {"kandidaten": local})
    dump(tmp_path / "referenz.json", {"auftraege": {"A1": {f"c{i}": {"relevant": i in {0, 2, 4}} for i in range(6)}}})
    source = tmp_path / "probe.md"
    source.write_text("# Probe\nAn unrelated counterprobe paragraph.", encoding="utf-8")
    dump(tmp_path / "gegenprobe.json", {"A1": {"datei": str(source), "zeile": 1, "bis": 2}})
    entries = [{"id": "c0", "status": "bewertet", "noul": .9},
               {"id": "c1", "status": "bewertet", "noul": .8},
               {"id": "probe", "status": "bewertet", "noul": .7}]
    entries += [{"id": f"c{i}", "status": "bewertet", "noul": .6 - i / 100} for i in range(2, 6)]
    dump(tmp_path / "roh/L1-A1.json", {"ordnung": {"eintraege": entries, "kontrolle": {"bestanden": True}, "eingabe_token": 1}, "laufzeit_s": 1.0, "gegenprobe_id": "probe"})
    result = u4.auswerten(tmp_path, k=(2, 4))
    metrics = result["auftraege"]["A1"]["L1"]
    assert metrics["gegenprobe"] == {"noul": .7, "rang": 3}
    assert metrics["jev_top5"] == 1
    assert metrics["jev_top10"] == 2


def test_l3_acceptance_and_each_individual_failure(tmp_path):
    dump(tmp_path / "auftraege.json", {"format": 1, "auftraege": [{"id": "A1", "text": "sample"}]})
    local = [{"id": f"c{i}"} for i in range(3)]
    dump(tmp_path / "kandidaten-A1.json", {"kandidaten": local})
    dump(tmp_path / "referenz.json", {"auftraege": {"A1": {row["id"]: {"relevant": False} for row in local}}})
    valid = [{"id": f"c{i}", "status": "nicht_bewertet", "grund": "http_401 unauthorized"} for i in range(3)]

    def evaluate(entries):
        dump(tmp_path / "roh/L3-A1.json", {"ordnung": {"eintraege": entries}})
        return u4.auswerten(tmp_path)["l3"]["wie_erwartet"]

    assert evaluate(valid) is True
    variants = []
    changed = [dict(row) for row in valid]; changed[0]["status"] = "bewertet"; variants.append(changed)
    changed = [dict(row) for row in valid]; changed[0]["grund"] = "netzfehler"; variants.append(changed)
    variants.append([valid[1], valid[0], valid[2]])
    variants.append(valid[:-1])
    for entries in variants:
        assert evaluate(entries) is False


def test_each_decision_rule_is_independently_checked(tmp_path):
    jobs = [{"id": "A1", "text": "first"}, {"id": "A2", "text": "second"}]
    dump(tmp_path / "auftraege.json", {"format": 1, "auftraege": jobs})
    candidates = {job["id"]: [{"id": f"{job['id']}-c{i}"} for i in range(13)] for job in jobs}
    for ident, rows in candidates.items():
        dump(tmp_path / f"kandidaten-{ident}.json", {"kandidaten": rows})
    relevant = {"A1": {"A1-c0", "A1-c10", "A1-c11", "A1-c12"}, "A2": {"A2-c0", "A2-c1", "A2-c2"}}
    dump(tmp_path / "referenz.json", {"auftraege": {ident: {row["id"]: {"relevant": row["id"] in relevant[ident]} for row in rows} for ident, rows in candidates.items()}})

    def run(*, increase=True, rule2=False, control=False, unstable=False, bad_l3=False):
        for job in jobs:
            ident = job["id"]
            local_ids = [row["id"] for row in candidates[ident]]
            if ident == "A1":
                order_ids = ["A1-c0", "A1-c10", "A1-c11", "A1-c12"] + [i for i in local_ids if i not in {"A1-c0", "A1-c10", "A1-c11", "A1-c12"}]
                if not increase:
                    order_ids = local_ids
            else:
                order_ids = local_ids
                if rule2:
                    order_ids = [f"A2-c{i}" for i in range(3, 12)] + ["A2-c0", "A2-c1", "A2-c2"]
            for run_name in ("L1", "L2"):
                entries = [{"id": ident_id, "status": "bewertet", "noul": .8} for ident_id in order_ids]
                if unstable and ident == "A2" and run_name == "L2":
                    entries[0]["noul"] = .2
                    entries[1]["noul"] = .2
                    entries[2]["noul"] = .2
                passed = not (control and ident == "A2" and run_name == "L2")
                dump(tmp_path / f"roh/{run_name}-{ident}.json", {"ordnung": {"eintraege": entries, "kontrolle": {"bestanden": passed}, "eingabe_token": 1}, "laufzeit_s": 1.0})
        l3_entries = [{"id": row["id"], "status": "nicht_bewertet", "grund": "http_401 unauthorized"} for row in candidates["A1"]]
        if bad_l3:
            l3_entries[0]["grund"] = "http_500"
        dump(tmp_path / "roh/L3-A1.json", {"ordnung": {"eintraege": l3_entries}})
        return u4.auswerten(tmp_path)["entscheidungsregel"]

    good = run()
    assert good["alle_erfuellt"] is True
    assert all(good[f"{i}_{name}"]["erfuellt"] for i, name in ((1, "jev_summe_top10_hoeher"), (2, "je_auftrag_maximal_einer_weniger"), (3, "kontrolle_alle_laeufe_bestanden"), (4, "stabilitaet_mindestens_0_90"), (5, "l3_wie_erwartet")))
    for kwargs, failed in [({"increase": False}, "1_jev_summe_top10_hoeher"),
                           ({"rule2": True}, "2_je_auftrag_maximal_einer_weniger"),
                           ({"control": True}, "3_kontrolle_alle_laeufe_bestanden"),
                           ({"unstable": True}, "4_stabilitaet_mindestens_0_90"),
                           ({"bad_l3": True}, "5_l3_wie_erwartet")]:
        result = run(**kwargs)
        assert result[failed]["erfuellt"] is False
        assert result["alle_erfuellt"] is False
        assert sum(not result[name]["erfuellt"] for name in result if name != "alle_erfuellt") == 1


def test_raw_files_never_contain_key_even_when_transport_echoes_it(tmp_path):
    _tracked_ready(tmp_path)
    key = "secret-value-for-test-123456"
    calls = []
    def echo(*args):
        calls.append(args)
        sent_key = args[1]["Authorization"].removeprefix("Bearer ")
        # Echo the credential used for this exact attempt, including L3's bad key.
        return 401, {}, ("echo " + sent_key).encode()
    assert u4.messe(tmp_path, live=True, transport=echo, schluessel=key) == 0
    raw = (tmp_path / "roh/L1-A1.json").read_text() + (tmp_path / "roh/L2-A1.json").read_text() + (tmp_path / "roh/L3-A1.json").read_text()
    assert key not in raw
    assert len(calls) >= 3


def test_evaluation_builds_json_and_report_including_missing_raw(tmp_path):
    dump(tmp_path / "auftraege.json", {"format":1,"auftraege":[{"id":"A1","text":"test"}]})
    dump(tmp_path / "kandidaten-A1.json", {"kandidaten":[{"id":f"c{i}"} for i in range(6)]})
    dump(tmp_path / "referenz.json", {"auftraege":{"A1":{f"c{i}":{"relevant":i in (0,2,4)} for i in range(6)}}})
    (tmp_path / "roh").mkdir()
    def raw(run, entries, control):
        dump(tmp_path / f"roh/{run}-A1.json", {"ordnung":{"eintraege":entries,"kontrolle":control,"status_gesamt":"bewertet","modell":"jev-1.13.0","eingabe_token":100},"laufzeit_s":1.2,"gegenprobe_id":None})
    l1=[{"id":"c0","status":"bewertet","noul":.4},{"id":"c1","status":"bewertet","noul":.6},{"id":"c2","status":"bewertet","noul":.5},{"id":"c3","status":"bewertet","noul":.6},{"id":"c4","status":"bewertet","noul":.6},{"id":"c5","status":"bewertet","noul":.5}]
    l2=[dict(e, noul=.5 if e['id']=='c0' else e['noul']) for e in l1]
    control={"bestanden":True,"noul":.1}
    raw("L1",l1,control); raw("L2",l2,control)
    result=u4.auswerten(tmp_path, k=(2,4))
    assert result["auftraege"]["A1"]["L1"]["jev_top5"] == 1
    assert result["auftraege"]["A1"]["stabilitaet"]["paarzahl"] == 6
    assert (tmp_path/"BERICHT.md").exists()
    assert "Summe L1" in (tmp_path/"BERICHT.md").read_text(encoding="utf-8")
    assert result["entscheidungsregel"]["alle_erfuellt"] is False

"""Offline preparation, controlled Jev measurement and reporting for U4.

Cleaned copy of the project's measurement tool (private build document §12.10),
adapted to this repository layout: the helper tools are loaded from ``tools/``,
the measurement data lives in ``data/u4-jev/``. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "data" / "u4-jev"
def _load(name):
    for folder in (ROOT / "tools", Path(__file__).resolve().parent):
        path = folder / f"{name}.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(f"helper module not found: {name}.py (looked in tools/ and scripts/measurements/)")


jev = _load("jev")
kp = _load("kontextpaket")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _auftraege(ordner):
    data = _read(Path(ordner) / "auftraege.json")
    if data.get("format") != 1 or not isinstance(data.get("auftraege"), list):
        raise ValueError("auftraege.json muss format 1 und eine Auftragsliste enthalten")
    return data["auftraege"]


def vorbereiten(projekt=ROOT, merkzettel=None, ordner=DEFAULT_DIR, *, ueberschreiben=False):
    ordner, projekt = Path(ordner), Path(projekt)
    jobs = _auftraege(ordner)
    outputs = []
    befunde = []
    dekl = kp.lade_deklaration(projekt, befunde)
    abschnitte = kp.zusatz_abschnitte(projekt, dekl, Path(merkzettel) if merkzettel else None)
    all_outputs = [path for job in jobs for path in (
        ordner / f"kandidaten-{job['id']}.json",
        ordner / "boegen" / f"bogen-{job['id']}.md",
        ordner / "boegen" / f"schluessel-{job['id']}.json")]
    if not ueberschreiben:
        existing = [p for p in all_outputs if p.exists()]
        if existing:
            raise FileExistsError("vorhandene Dateien werden nicht ersetzt: " +
                                  ", ".join(str(p) for p in existing) +
                                  " (nutze --ueberschreiben)")
    for job in jobs:
        ident, text = job["id"], job["text"]
        candidates = kp.kandidaten_liste(abschnitte, kp.suchwoerter(text))[:30]
        rows = [{"id": a.id, "datei": str(a.pfad_abs), "quelle": a.anzeige,
                 "zeile": a.zeile, "bis": a.bis, "ueberschrift": a.ueberschrift,
                 "sha": a.sha, "punkte": points, "text": a.text}
                for a, points in candidates]
        cand_path = ordner / f"kandidaten-{ident}.json"
        sheet_path = ordner / "boegen" / f"bogen-{ident}.md"
        key_path = ordner / "boegen" / f"schluessel-{ident}.json"
        outputs.extend((cand_path, sheet_path, key_path))
        _write(cand_path, {"format": 1, "auftrag": text, "kandidaten": rows})
        indexed = list(enumerate(rows))
        import random
        random.Random("u4-" + ident).shuffle(indexed)
        key = {f"B{i:02d}": row["id"] for i, (_n, row) in enumerate(indexed, 1)}
        lines = [f"# Beurteilungsbogen {ident}", "", "## Auftrag", text, "",
                 "## Frage", jev.WICHTIG_FRAGE["instructions"], "",
                 f"Ja: {jev.WICHTIG_FRAGE['criteria']['true']}",
                 f"Nein: {jev.WICHTIG_FRAGE['criteria']['false']}", "",
                 "## Antwortformat", 'JSON: {"A1": {"B01": {"relevant": true, "grund": "ein Satz"}}}; je Kandidat ja/nein und ein Satz Begründung.', ""]
        for number, (_original, row) in enumerate(indexed, 1):
            source = row.get("quelle") or jev._norm_quelle(row.get("datei", ""))
            lines += [f"## B{number:02d}", f"Quelle: {source}", f"### {row.get('ueberschrift') or '(ohne Überschrift)'}", row["text"], ""]
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_path.write_text("\n".join(lines), encoding="utf-8")
        _write(key_path, key)
    return outputs


def referenz(ordner=DEFAULT_DIR):
    ordner = Path(ordner)
    jobs = _auftraege(ordner)
    refs = {}
    disagreements = []
    total = agrees = 0
    for job in jobs:
        ident = job["id"]
        keys = _read(ordner / "boegen" / f"schluessel-{ident}.json")
        b1 = _read(ordner / "referenz" / "beurteiler-1.json").get(ident, {})
        b2 = _read(ordner / "referenz" / "beurteiler-2.json").get(ident, {})
        decision_data = _read(ordner / "referenz" / "entscheid.json") if (ordner / "referenz" / "entscheid.json").exists() else {}
        mapping = {}
        for label in sorted(keys):
            a, b = b1.get(label), b2.get(label)
            if not isinstance(a, dict) or type(a.get("relevant")) is not bool or not isinstance(b, dict) or type(b.get("relevant")) is not bool:
                raise ValueError(f"fehlendes oder ungültiges Urteil: {ident}/{label}")
            total += 1
            same = a["relevant"] == b["relevant"]
            agrees += int(same)
            chosen = a["relevant"] if same else None
            grund = None
            if not same:
                candidate_id = keys[label]
                decision = decision_data.get(candidate_id)
                if not isinstance(decision, dict) or type(decision.get("relevant")) is not bool:
                    disagreements.append({"auftrag": ident, "bogen": label, "id": candidate_id,
                                          "grund_b1": a.get("grund", ""), "grund_b2": b.get("grund", "")})
                    continue
                chosen, grund = decision["relevant"], decision.get("grund")
            mapping[keys[label]] = {"relevant": chosen, "b1": a["relevant"], "b2": b["relevant"],
                                    "strittig": not same, "entscheid_grund": grund}
        if set(b1) != set(keys) or set(b2) != set(keys):
            missing = sorted(set(keys) - (set(b1) & set(b2)))
            raise ValueError(f"fehlende B-Nummern für {ident}: {', '.join(missing)}")
        refs[ident] = mapping
    if disagreements:
        for item in disagreements:
            print(json.dumps(item, ensure_ascii=False))
        return 1
    per = {}
    for job in jobs:
        ident = job["id"]
        k = len(_read(ordner / "boegen" / f"schluessel-{ident}.json"))
        agrees_job = sum(1 for r in refs[ident].values() if r["b1"] == r["b2"])
        per[ident] = agrees_job / k if k else 1.0
    per["gesamt"] = agrees / total if total else 1.0
    _write(ordner / "referenz.json", {"format": 1, "auftraege": refs, "uebereinstimmung": per})
    return 0


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def _key_hits(texts):
    return jev.schluessel_treffer(texts)


def _gather_send_texts(ordner, jobs):
    texts = []
    for job in jobs:
        texts.append((f"Auftrag {job['id']}", job["text"]))
        data = _read(Path(ordner) / f"kandidaten-{job['id']}.json")
        for c in data["kandidaten"]:
            for field in ("text", "ueberschrift", "quelle"):
                if c.get(field):
                    texts.append((f"{job['id']}:{field}", str(c[field])))
    return texts


def _gegenprobe_send_texts(jobs, probes):
    texts = []
    for job in jobs:
        probe = probes[job["id"]]
        if probe:
            for field in ("text", "ueberschrift", "quelle"):
                if probe.get(field):
                    texts.append((f"{job['id']}:gegenprobe:{field}", str(probe[field])))
    return texts


def _pruefe_referenz_kandidaten(ordner, jobs, paths):
    reference = _read(Path(ordner) / "referenz.json").get("auftraege")
    if not isinstance(reference, dict):
        raise ValueError("referenz.json muss eine Auftragszuordnung enthalten")
    for job, path in zip(jobs, paths):
        candidates = _read(path).get("kandidaten")
        if not isinstance(candidates, list):
            raise ValueError(f"Kandidatenliste fehlt: {path.relative_to(ordner)}")
        ids = [candidate.get("id") for candidate in candidates if isinstance(candidate, dict)]
        if (len(ids) != len(candidates) or any(not isinstance(ident, str) for ident in ids)
                or len(ids) != len(set(ids))):
            raise ValueError(f"Kandidaten-IDs ungültig oder doppelt: {job['id']}")
        job_reference = reference.get(job["id"])
        if not isinstance(job_reference, dict) or set(job_reference) != set(ids):
            raise ValueError(f"Referenz-IDs stimmen nicht genau mit Kandidaten überein: {job['id']}")


def _versioniert_unveraendert(ordner, paths):
    for path in paths:
        rel = Path(path).relative_to(ordner).as_posix()
        if _git("ls-files", "--error-unmatch", rel, cwd=ordner).returncode:
            raise ValueError(f"nicht versioniert: {rel}")
        if _git("status", "--porcelain", "--", rel, cwd=ordner).stdout.strip():
            raise ValueError(f"versionierte Datei geändert: {rel}")


def _gegenprobe(ordner, jobs):
    probes = _read(Path(ordner) / "gegenprobe.json")
    result = {}
    for job in jobs:
        item = probes.get(job["id"])
        if item is None:
            result[job["id"]] = None
            continue
        path = Path(item["datei"])
        if not path.is_absolute():
            path = ROOT / path
        lines = path.read_text(encoding="utf-8").splitlines()
        start, end = int(item["zeile"]), int(item["bis"])
        if start < 1 or end < start or end > len(lines):
            raise ValueError(f"ungültiger Gegenprobe-Zeilenbereich: {job['id']}")
        text = "\n".join(lines[start - 1:end])
        if kp.punktzahl(text.lower(), kp.suchwoerter(job["text"])):
            raise ValueError(f"Gegenprobe {job['id']} enthält Suchwörter")
        candidate = {"id": "Z-" + hashlib.sha256((str(path.resolve()) + "\n" + text).encode()).hexdigest()[:12],
                     "datei": str(path.resolve()), "quelle": path.name,
                     "ueberschrift": next((line.lstrip("# ") for line in lines[start-1:end] if line.startswith("#")), ""),
                     "text": text}
        result[job["id"]] = candidate
    return result


def messe(ordner=DEFAULT_DIR, *, live=False, obergrenze_usd=0.10, transport=None,
          schluessel=None, jetzt=None, uhr=None):
    if not live:
        print("u4 messen: nur mit --live", file=sys.stderr)
        return 2
    ordner = Path(ordner)
    jobs = _auftraege(ordner)
    candidates_paths = [ordner / f"kandidaten-{j['id']}.json" for j in jobs]
    required = candidates_paths + [ordner / "referenz.json", ordner / "gegenprobe.json"]
    try:
        for path in required:
            if not path.is_file():
                raise ValueError(f"Datei fehlt: {path.relative_to(ordner)}")
        _versioniert_unveraendert(ordner, required)
        _pruefe_referenz_kandidaten(ordner, jobs, candidates_paths)
        probes = _gegenprobe(ordner, jobs)
        hits = _key_hits(_gather_send_texts(ordner, jobs) + _gegenprobe_send_texts(jobs, probes))
        if hits:
            raise ValueError("Schlüsselmuster gefunden in: " + ", ".join(hits))
        if not (schluessel or os.environ.get("TYPESAFE_API_KEY")):
            raise ValueError("TYPESAFE_API_KEY fehlt")
        kand_by_job = {j["id"]: _read(ordner / f"kandidaten-{j['id']}.json")["kandidaten"] for j in jobs}
        estimate = sum(jev.schaetze_kosten(j["text"], kand_by_job[j["id"]] + ([probes[j["id"]]] if probes[j["id"]] else []), max_kandidaten=31, kontrolle=True)["usd_geschaetzt"] for j in jobs) * 2
        if estimate > obergrenze_usd / 2:
            raise ValueError(f"Kostenschätzung für beide Läufe je Hälfte ${estimate/2:.6f} über ${obergrenze_usd/2:.6f}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"u4 messen: {exc}", file=sys.stderr)
        return 1
    os.makedirs(ordner / "roh", exist_ok=True)
    total_cost = 0.0
    now = jetzt or (lambda: datetime.now(timezone.utc))
    clock = uhr or time.monotonic
    for run in ("L1", "L2"):
        for job in jobs:
            ident = job["id"]
            rows = list(kand_by_job[ident])
            probe = probes[ident]
            if probe:
                rows.append(probe)
            started = clock()
            call_tokens = 0
            over_budget = False

            def bounded_transport(url, headers, body, timeout):
                nonlocal call_tokens, over_budget, total_cost
                if over_budget:
                    raise RuntimeError("U4-Kostenobergrenze")
                sender = transport or jev._live_transport
                response = sender(url, headers, body, timeout)
                try:
                    payload = json.loads(response[2].decode("utf-8", errors="replace"))
                    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
                    amount = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                    if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
                        call_tokens += amount
                        total_cost += amount * jev.PREIS_JE_MIO_TOKEN / 1_000_000
                        over_budget = total_cost > obergrenze_usd
                except (ValueError, TypeError, AttributeError):
                    pass
                return response

            order = jev.ordne_kandidaten(job["text"], rows, max_kandidaten=31,
                                        kontrolle=True, live=True, transport=bounded_transport,
                                        schluessel=schluessel)
            elapsed = clock() - started
            if over_budget:
                print(f"u4 messen: Kostenobergrenze ${obergrenze_usd:.2f} überschritten; Abbruch nach {run}/{ident}", file=sys.stderr)
                payload = {"lauf": run, "auftrag": ident, "zeitpunkt": now().astimezone(timezone.utc).isoformat(),
                           "laufzeit_s": float(elapsed), "ordnung": jev._ordnung_json(order),
                           "gegenprobe_id": probe["id"] if probe else None}
                _write(ordner / "roh" / f"{run}-{ident}.json", payload)
                return 1
            payload = {"lauf": run, "auftrag": ident, "zeitpunkt": now().astimezone(timezone.utc).isoformat(),
                       "laufzeit_s": float(elapsed), "ordnung": jev._ordnung_json(order),
                       "gegenprobe_id": probe["id"] if probe else None}
            _write(ordner / "roh" / f"{run}-{ident}.json", payload)
    # L3: one run, first job, deliberately invalid key, no counterprobe.
    job = jobs[0]
    rows = kand_by_job[job["id"]]
    started = clock()
    order = jev.ordne_kandidaten(job["text"], rows, max_kandidaten=30, kontrolle=True,
                                live=True, transport=transport,
                                schluessel="u4-absichtlich-falsch-0000")
    payload = {"lauf": "L3", "auftrag": job["id"], "zeitpunkt": now().astimezone(timezone.utc).isoformat(),
               "laufzeit_s": float(clock() - started), "ordnung": jev._ordnung_json(order), "gegenprobe_id": None}
    _write(ordner / "roh" / f"L3-{job['id']}.json", payload)
    return 0


def _stability(a, b):
    by_a = {e["id"]: e for e in a if e.get("status") == "bewertet"}
    by_b = {e["id"]: e for e in b if e.get("status") == "bewertet"}
    pairs = [(by_a[i]["noul"], by_b[i]["noul"]) for i in by_a.keys() & by_b.keys()]
    return {"paarzahl": len(pairs), "gleiche_seite": sum((x >= .5) == (y >= .5) for x, y in pairs) / len(pairs) if pairs else None,
            "mittlere_delta": sum(abs(x-y) for x, y in pairs) / len(pairs) if pairs else None}


def _metrics(refmap, local, order, *, k=(5, 10)):
    relevant = {i for i, v in refmap.items() if v["relevant"]}
    local_ids = [x["id"] for x in local]
    jev_entries = [x for x in order["eintraege"] if x["id"] in set(local_ids)]
    jev_ids = [x["id"] for x in jev_entries]
    noul = {x["id"]: x["noul"] for x in jev_entries if x["status"] == "bewertet"}
    not_relevant = set(refmap) - relevant
    return {"lokal_top5": len(relevant & set(local_ids[:k[0]])), "lokal_top10": len(relevant & set(local_ids[:k[1]])),
            "jev_top5": len(relevant & set(jev_ids[:k[0]])), "jev_top10": len(relevant & set(jev_ids[:k[1]])),
            "uebersehen": sum(1 for i in relevant if i in noul and noul[i] < .5),
            "fehlalarm": sum(1 for i in not_relevant if i in noul and noul[i] >= .5),
            "grenzfaelle": sum(.4 <= x <= .6 for x in noul.values()),
            "nicht_bewertet": sum(e.get("noul") is None for e in jev_entries),
            "gegenprobe": None}


def auswerten(ordner=DEFAULT_DIR, *, k=(5, 10)):
    ordner = Path(ordner)
    jobs, reference = _auftraege(ordner), _read(ordner / "referenz.json")["auftraege"]
    per, run_summary, stability_all = {}, {}, []
    for job in jobs:
        ident = job["id"]
        local = _read(ordner / f"kandidaten-{ident}.json")["kandidaten"]
        one = {}
        for run in ("L1", "L2"):
            path = ordner / "roh" / f"{run}-{ident}.json"
            if not path.exists():
                one[run] = None
                continue
            raw = _read(path)
            metrics = _metrics(reference[ident], local, raw["ordnung"], k=k)
            probe_id = raw.get("gegenprobe_id")
            if probe_id:
                found = next((i for i, e in enumerate(raw["ordnung"]["eintraege"], 1) if e["id"] == probe_id), None)
                probe_entry = next((e for e in raw["ordnung"]["eintraege"] if e["id"] == probe_id), None)
                metrics["gegenprobe"] = {"noul": probe_entry["noul"], "rang": found} if probe_entry else None
            metrics.update({"kontrolle": raw["ordnung"].get("kontrolle"), "status_gesamt": raw["ordnung"].get("status_gesamt"),
                            "modell": raw["ordnung"].get("modell"), "laufzeit_s": raw.get("laufzeit_s"),
                            "kosten_usd": raw["ordnung"].get("eingabe_token", 0) * jev.PREIS_JE_MIO_TOKEN / 1e6,
                            "kosten_geschaetzt_usd": jev.schaetze_kosten(job["text"], local + ([
                                _gegenprobe(ordner, [job])[ident]] if raw.get("gegenprobe_id") else []),
                                                                             max_kandidaten=31, kontrolle=True)["usd_geschaetzt"]})
            one[run] = metrics
            run_summary[f"{run}/{ident}"] = metrics
        if one["L1"] and one["L2"]:
            stab = _stability(_read(ordner / "roh" / f"L1-{ident}.json")["ordnung"]["eintraege"],
                              _read(ordner / "roh" / f"L2-{ident}.json")["ordnung"]["eintraege"])
            one["stabilitaet"] = stab
            # carry pair values for pooled stability
            a = {e["id"]:e for e in _read(ordner / "roh" / f"L1-{ident}.json")["ordnung"]["eintraege"] if e["status"] == "bewertet"}
            b = {e["id"]:e for e in _read(ordner / "roh" / f"L2-{ident}.json")["ordnung"]["eintraege"] if e["status"] == "bewertet"}
            stability_all += [(a[i]["noul"], b[i]["noul"]) for i in a.keys() & b.keys()]
        per[ident] = one
    l3_expected = False
    l3 = None
    l3path = ordner / "roh" / f"L3-{jobs[0]['id']}.json"
    if l3path.exists():
        l3 = _read(l3path)
        local = _read(ordner / f"kandidaten-{jobs[0]['id']}.json")["kandidaten"]
        entries = l3["ordnung"]["eintraege"]
        l3_expected = (len(entries) == len(local) and [e["id"] for e in entries] == [c["id"] for c in local]
                       and all(e["status"] == "nicht_bewertet" and (e.get("grund") or "").startswith("http_401") for e in entries))
    paired = len(stability_all)
    pooled = {"paarzahl": paired, "gleiche_seite": sum((a >= .5)==(b >= .5) for a,b in stability_all)/paired if paired else None,
              "mittlere_delta": sum(abs(a-b) for a,b in stability_all)/paired if paired else None}
    # Totals count each job once from L1, per §12.10.
    totals = {key: sum((per[j["id"]].get("L1") or {}).get(key, 0) for j in jobs)
              for key in ("lokal_top10", "jev_top10", "lokal_top5", "jev_top5", "uebersehen", "fehlalarm", "grenzfaelle", "nicht_bewertet")}
    per_job_rule2 = {j["id"]: {"erfuellt": (per[j["id"]].get("L1") or {}).get("jev_top10", 0) >=
                                   (per[j["id"]].get("L1") or {}).get("lokal_top10", 0)-1,
                               "jev_top10": (per[j["id"]].get("L1") or {}).get("jev_top10"),
                               "lokal_top10": (per[j["id"]].get("L1") or {}).get("lokal_top10")}
                      for j in jobs}
    control_evidence = {f"{run}/{j['id']}": (per[j["id"]].get(run) or {}).get("kontrolle")
                        for j in jobs for run in ("L1", "L2")}
    rules = {
        "1_jev_summe_top10_hoeher": {"erfuellt": totals["jev_top10"] > totals["lokal_top10"], "jev": totals["jev_top10"], "lokal": totals["lokal_top10"]},
        "2_je_auftrag_maximal_einer_weniger": {"erfuellt": all(v["erfuellt"] for v in per_job_rule2.values()), "auftraege": per_job_rule2},
        "3_kontrolle_alle_laeufe_bestanden": {"erfuellt": all(isinstance(v, dict) and v.get("bestanden") is True for v in control_evidence.values()), "laeufe": control_evidence},
        "4_stabilitaet_mindestens_0_90": {"erfuellt": pooled["gleiche_seite"] is not None and pooled["gleiche_seite"] >= .90, "wert": pooled["gleiche_seite"]},
        "5_l3_wie_erwartet": {"erfuellt": l3_expected,
                              "anzahl": len(l3["ordnung"]["eintraege"]) if l3 else None,
                              "status_gesamt": l3["ordnung"].get("status_gesamt") if l3 else None,
                              "gruende_http_401": sum((e.get("grund") or "").startswith("http_401") for e in l3["ordnung"]["eintraege"]) if l3 else None}}
    rules["alle_erfuellt"] = all(v["erfuellt"] for key,v in rules.items() if key != "alle_erfuellt")
    result = {"format": 1, "auftraege": per, "summe": totals, "stabilitaet_gesamt": pooled,
              "l3": {"vorhanden": l3 is not None, "wie_erwartet": l3_expected}, "entscheidungsregel": rules,
              "entscheidung": "Fatih", "referenz_commit": _git("log", "-1", "--format=%H", "--", "referenz.json", cwd=ordner).stdout.strip() or None}
    _write(ordner / "ergebnis.json", result)
    lines = ["# U4-Messbericht", "", "Entscheidung: Fatih", "", "## Ergebnisse je Auftrag", "",
             "| Auftrag | lokal Top 5/10 | Jev Top 5/10 | übersehen | Fehlalarme | Grenzen | L1 Kontrolle | L2 Kontrolle |", "|---|---:|---:|---:|---:|---:|---|---|"]
    for j in jobs:
        m = per[j["id"]].get("L1")
        if not m:
            lines.append(f"| {j['id']} | fehlt | fehlt | fehlt | fehlt | fehlt | fehlt | fehlt |")
        else:
            ctl1 = m.get("kontrolle")
            ctl2 = (per[j["id"]].get("L2") or {}).get("kontrolle")
            lines.append(f"| {j['id']} | {m['lokal_top5']}/{m['lokal_top10']} | {m['jev_top5']}/{m['jev_top10']} | {m['uebersehen']} | {m['fehlalarm']} | {m['grenzfaelle']} | {ctl1} | {ctl2 or 'fehlt'} |")
    lines.append(f"| **Summe L1** | **{totals['lokal_top5']}/{totals['lokal_top10']}** | **{totals['jev_top5']}/{totals['jev_top10']}** | **{totals['uebersehen']}** | **{totals['fehlalarm']}** | **{totals['grenzfaelle']}** | — | — |")
    lines += ["", "## Summe und Messläufe", "", f"Stabilität gepoolt: {pooled['gleiche_seite']} gleiche Seite, mittlere Δ {pooled['mittlere_delta']}.",
              f"L1/L2 Kosten gesamt: ${sum((per[j['id']].get(r) or {}).get('kosten_usd', 0) for j in jobs for r in ('L1','L2')):.8f}; geschätzt: ${sum((per[j['id']].get(r) or {}).get('kosten_geschaetzt_usd', 0) for j in jobs for r in ('L1','L2')):.8f}.",
              f"L1/L2 Laufzeit gesamt: {sum((per[j['id']].get(r) or {}).get('laufzeit_s', 0) for j in jobs for r in ('L1','L2')):.3f} s.",
              f"L3 wie erwartet: {l3_expected}; Rohdatei: {'vorhanden' if l3 else 'fehlt'}.", f"Referenz-Commit: {result['referenz_commit'] or 'fehlt'}", "", "### Pro Lauf", ""]
    for j in jobs:
        for run in ("L1", "L2"):
            m = per[j["id"]].get(run)
            if m:
                lines.append(f"- {run}/{j['id']}: Status {m['status_gesamt']}; Modell {m['modell'] or 'fehlt'}; Laufzeit {m['laufzeit_s']:.3f} s; Kosten ${m['kosten_usd']:.8f} (geschätzt ${m['kosten_geschaetzt_usd']:.8f}); Gegenprobe {m['gegenprobe'] or 'keine'}; Kontrolle {m['kontrolle']}.")
            else:
                lines.append(f"- {run}/{j['id']}: Rohdaten fehlen.")
    lines += ["", "## Entscheidungsregel", ""]
    for name, value in rules.items():
        if name == "alle_erfuellt":
            lines.append(f"- Alle erfüllt: {'✔' if value else '✘'}")
        else:
            lines.append(f"- {name}: {'✔' if value['erfuellt'] else '✘'} — {json.dumps(value, ensure_ascii=False)}")
    (ordner / "BERICHT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("vorbereiten"); p.add_argument("--projekt", type=Path, default=ROOT); p.add_argument("--merkzettel", type=Path); p.add_argument("--ordner", type=Path, default=DEFAULT_DIR); p.add_argument("--ueberschreiben", action="store_true")
    p = sub.add_parser("referenz"); p.add_argument("--ordner", type=Path, default=DEFAULT_DIR)
    p = sub.add_parser("messen"); p.add_argument("--live", action="store_true"); p.add_argument("--ordner", type=Path, default=DEFAULT_DIR); p.add_argument("--obergrenze-usd", type=float, default=.10)
    p = sub.add_parser("auswerten"); p.add_argument("--ordner", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "vorbereiten":
            for path in vorbereiten(args.projekt, args.merkzettel, args.ordner, ueberschreiben=args.ueberschreiben): print(path)
            return 0
        if args.cmd == "referenz": return referenz(args.ordner)
        if args.cmd == "messen": return messe(args.ordner, live=args.live, obergrenze_usd=args.obergrenze_usd)
        auswerten(args.ordner); return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"u4: {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())

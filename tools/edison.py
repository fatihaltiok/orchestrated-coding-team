"""Literatur-Werkzeug: Fragen an Edison Scientific (E-029).

Stellt eine Frage an einen Literatur-Agenten von Edison Scientific
(früher FutureHouse) über das offizielle Paket ``edison-client`` und legt
die Antwort mit Quellen unter ``docs/recherche/edison/`` ab. Offline ist
Standard, und vor jedem Live-Aufruf steht eine Monatsgrenze.

Kommandozeile::

    python3 tools/edison.py frage --agent AGENT
        (--frage TEXT | --frage-datei PFAD) [--live] [--ordner DIR]
        [--zeitgrenze SEK] [--monatsgrenze N] [--schluesseldatei PFAD]
        [--zaehldatei PFAD]
    python3 tools/edison.py zaehler [--zaehldatei PFAD] [--monatsgrenze N]

Agenten (deutscher Name → Mitglied in ``edison_client.JobNames``):
``literatur`` → LITERATURE, ``literatur-hoch`` → LITERATURE_HIGH,
``vorlaeufer`` → PRECEDENT, ``probe`` → DUMMY.

Einrichtung: Für Live-Aufrufe ``edison-client`` in einer eigenen
Python-Umgebung installieren und das Werkzeug mit deren Interpreter starten::

    python3 tools/edison.py …

Ohne ``--live``: kein Netz, kein Import von ``edison_client``, kein
Schlüssel nötig, keine Zählung — nur eine Vorschau dessen, was gesendet
würde. Der Schlüssel (``EDISON_API_KEY``, sonst Schlüsseldatei) wird nie
ausgegeben — weder auf stdout/stderr noch in Ergebnis-, JSON- oder
Zähldatei; Fehlermeldungen der Gegenstelle werden maskiert, zusätzlich
Bearer-/Token-Werte und JWTs.

Exit-Codes: 0 = Antwort gespeichert (bzw. Offline-Vorschau),
2 = Aufruffehler (Agent unbekannt, Frage leer oder länger als 8000
Zeichen, Datei fehlt, Schlüssel fehlt, ``edison_client`` nicht
importierbar → Hülle ``edison`` benutzen), 3 = gespeichert, aber keine
Antwort im Ergebnis oder ``has_successful_answer`` ist False,
4 = Fehler der Gegenstelle oder Zeitgrenze (nichts gespeichert außer
der Zählzeile), 5 = Monatsgrenze erreicht (kein Aufruf),
130 = abgebrochen (Strg+C) — der Versuch zählt mit.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

# Deutsche Agentennamen → Mitgliedsname in edison_client.JobNames.
# Die Zuordnung läuft über den Mitgliedsnamen (getattr), nicht über hart
# codierte Jobstrings — die bleiben allein im Paket.
AGENTEN = {
    "literatur": "LITERATURE",
    "literatur-hoch": "LITERATURE_HIGH",
    "vorlaeufer": "PRECEDENT",
    "probe": "DUMMY",
}

SCHLUESSEL_VAR = "EDISON_API_KEY"
SCHLUESSEL_DATEI = "~/.config/orchestrated-team/edison.env"
ZAEHL_DATEI = "~/.local/state/orchestrated-team/edison-aufrufe.jsonl"
ORDNER_STANDARD = "docs/recherche/edison"
MONATSGRENZE = 10
ZEITGRENZE = 2400.0
FRAGE_MAX_ZEICHEN = 8000

# Repo-Wurzel des Werkzeugs: eine Ebene über tools/.
WURZEL = Path(__file__).resolve().parent.parent

HINWEIS_KI = ("Antwort eines KI-Agenten — Quellen vor Verwendung "
              "selbst nachschlagen.")


# Geheimnis-förmige Muster in Texten der Gegenstelle: die Bibliothek
# tauscht den Schlüssel gegen ein Sitzungstoken — Meldungen können das
# Token statt des Schlüssels enthalten. Deshalb neben dem exakten
# Schlüssel auch Bearer-Werte, Werte hinter api_key=/api-key:/token=/
# authorization: (Groß/Klein egal) und JWT-förmige Zeichenketten maskieren.
_MUSTER_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_MUSTER_FELD = re.compile(
    r"(?i)\b(api[_-]?key|token|authorization)\s*[:=]\s*\S+")
_MUSTER_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def _feld_ersatz(treffer):
    """In „name = wert“ den Wert durch *** ersetzen, Namen behalten."""
    kopf = re.match(r"(?i).*[:=]\s*", treffer.group(0)).group(0)
    return kopf + "***"


def _maskiere(schluessel, text):
    """Schlüssel und geheimnis-förmige Werte durch *** ersetzen."""
    if not text:
        return text
    text = str(text)
    if schluessel:
        text = text.replace(schluessel, "***")
    text = _MUSTER_BEARER.sub("Bearer ***", text)
    text = _MUSTER_FELD.sub(_feld_ersatz, text)
    text = _MUSTER_JWT.sub("***", text)
    return text


def _maskiere_daten(schluessel, daten):
    """_maskiere für jedes Textfeld einer Ergebnis-Map."""
    return {name: (_maskiere(schluessel, wert) if isinstance(wert, str)
                   else wert)
            for name, wert in daten.items()}


def _lies_schluessel_datei(pfad):
    """EDISON_API_KEY aus einer env-Datei lesen — tolerant gegen
    Leerzeilen, Leerzeichen, ``export``-Präfix, Anführungszeichen,
    ``#``-Kommentarzeilen und fremde Variablen. ``export`` wird nur als
    eigenes Wort mit folgendem Leerraum entfernt — Namen wie
    ``exporter`` oder ``exportEDISON_API_KEY`` bleiben unberührt.
    Nicht gefunden oder nicht lesbar (auch ungültiges UTF-8) → None
    (nie eine Ausnahme)."""
    try:
        with open(os.path.expanduser(pfad), encoding="utf-8") as datei:
            zeilen = datei.readlines()
    except (OSError, UnicodeError):
        return None
    for roh in zeilen:
        zeile = roh.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if re.match(r"export\s", zeile):
            zeile = zeile.split(None, 1)[-1]
        name, trenner, wert = zeile.partition("=")
        if not trenner or name.strip() != SCHLUESSEL_VAR:
            continue
        wert = wert.strip()
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1].strip()
        return wert or None
    return None


def _schluessel(schluesseldatei):
    """Umgebung zuerst, dann die Schlüsseldatei. None, wenn beides leer."""
    wert = os.environ.get(SCHLUESSEL_VAR, "").strip()
    if wert:
        return wert
    return _lies_schluessel_datei(schluesseldatei)


def _kurzname(frage, max_laenge=40):
    """Dateinamen-Kurzname aus der Frage: nur a-z0-9-, höchstens 40."""
    text = frage.lower().replace("ß", "ss")
    text = "".join(zeichen for zeichen in unicodedata.normalize("NFKD", text)
                   if unicodedata.category(zeichen) != "Mn")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_laenge].rstrip("-") or "frage"


def _lade_zaehler(pfad):
    """Alle Zähleinträge einer jsonl-Datei.

    Unlesbare Zeilen (kein JSON, kein Objekt, ungültige Bytes) bleiben
    als Platzhalter ``{"_unlesbar": True}`` erhalten — sie zählen in
    ``_zaehle_monat`` sicherheitshalber als je ein eigener Aufruf im
    laufenden Monat (sicherer Fehler, E1n/B2)."""
    eintraege = []
    try:
        with open(os.path.expanduser(pfad), encoding="utf-8",
                  errors="replace") as datei:
            for zeile in datei:
                zeile = zeile.strip()
                if not zeile:
                    continue
                try:
                    eintrag = json.loads(zeile)
                except ValueError:
                    eintrag = None
                eintraege.append(eintrag if isinstance(eintrag, dict)
                                 else {"_unlesbar": True})
    except (OSError, UnicodeError):
        pass
    return eintraege


def _zaehle_monat(eintraege, jetzt):
    """Zahl der Aufrufe im laufenden Kalendermonat.

    Start- und Ergebniszeile desselben Laufs teilen die Lauf-ID und
    zählen nur einmal. Zeilen ohne lesbaren Zeitstempel (``zeit`` fehlt
    oder ungültig, kein Objekt) zählen je einzeln als Aufruf im
    laufenden Monat — sicherer Fehler. Einträge mit ``lauf`` fehlend
    oder null zählen ebenfalls je einzeln, nie zusammengefasst.
    """
    laeufe = set()
    einzelne = 0
    for eintrag in eintraege:
        zeit = eintrag.get("zeit") if isinstance(eintrag, dict) else None
        try:
            stempel = datetime.fromisoformat(str(zeit))
        except (TypeError, ValueError):
            einzelne += 1
            continue
        if stempel.tzinfo is not None:
            stempel = stempel.astimezone().replace(tzinfo=None)
        if (stempel.year, stempel.month) != (jetzt.year, jetzt.month):
            continue
        lauf = eintrag.get("lauf")
        if lauf:
            laeufe.add(lauf)
        else:
            einzelne += 1
    return len(laeufe) + einzelne


def _haenge_an(pfad, eintrag):
    """Eine JSON-Zeile an die Zähldatei hängen; Ordner bei Bedarf anlegen."""
    ziel = Path(os.path.expanduser(pfad))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with open(ziel, "a", encoding="utf-8") as datei:
        datei.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def _gesperrt(zaehldatei):
    """Exklusive Dateisperre neben der Zähldatei (``<zaehldatei>.lock``).

    Grenze prüfen und Startzeile anhängen laufen unter dieser Sperre —
    zwei gleichzeitige Aufrufe können die Monatsgrenze so nicht gemeinsam
    unterlaufen (E1n/B3)."""
    ziel = Path(os.path.expanduser(zaehldatei))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with open(str(ziel) + ".lock", "a", encoding="utf-8") as sperr:
        fcntl.flock(sperr.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(sperr.fileno(), fcntl.LOCK_UN)


def _live_ausfuehren(agent_mitglied, frage, schluessel, zeitgrenze):
    """Der einzige Netzteil — ``edison_client`` wird erst hier importiert.

    Gibt die Antwortfelder als schlichte Map zurück (fehlende PQA-Felder,
    etwa bei DUMMY, bleiben None). Fremdtypen wie Zeitstempel werden als
    Text abgelegt, damit alles JSON-fähig ist.
    """
    from edison_client import EdisonClient, JobNames
    aufgabe = {"name": getattr(JobNames, agent_mitglied), "query": frage}
    client = EdisonClient(api_key=schluessel)
    (antwort,) = client.run_tasks_until_done(aufgabe, timeout=zeitgrenze)
    felder = ("status", "query", "created_at", "job_name", "task_id",
              "answer", "formatted_answer", "answer_reasoning",
              "has_successful_answer", "total_cost", "total_queries")
    daten = {}
    for feld in felder:
        wert = getattr(antwort, feld, None)
        if wert is not None and not isinstance(wert, (bool, int, float, str)):
            wert = str(wert)
        daten[feld] = wert
    return daten


def _schreibe_ergebnis(ordner, agent, mitglied, frage, daten, zeit):
    """Antwort als .md (lesbar) und .json (maschinenlesbar) ablegen.

    Beide Dateien werden exklusiv angelegt (``open(…, "x")``) — bei einer
    Kollision, auch durch einen gleichzeitigen Lauf, zählt der Zähler
    weiter; nie überschreiben (E1n/B7).
    Rückgabe: (md_pfad, json_pfad).
    """
    ziel = Path(ordner)
    ziel.mkdir(parents=True, exist_ok=True)
    basis = f"{zeit:%Y-%m-%d-%H%M}-{agent}-{_kurzname(frage)}"
    nr = 1
    while True:
        name = basis if nr == 1 else f"{basis}-{nr}"
        json_pfad = ziel / f"{name}.json"
        md_pfad = ziel / f"{name}.md"
        try:
            json_datei = open(json_pfad, "x", encoding="utf-8")
        except FileExistsError:
            nr += 1
            continue
        try:
            md_datei = open(md_pfad, "x", encoding="utf-8")
        except FileExistsError:
            json_datei.close()
            json_pfad.unlink()  # das eigene, eben angelegte Ticket lösen
            nr += 1
            continue
        break
    antwort_text = daten.get("formatted_answer") or daten.get("answer") or ""
    kopf = {
        "hinweis": HINWEIS_KI,
        "zeitpunkt": zeit.isoformat(timespec="seconds"),
        "agent": agent,
        "mitglied": mitglied,
        "frage": frage,
        "job_name": daten.get("job_name"),
        "task_id": daten.get("task_id"),
        "status": daten.get("status"),
        "has_successful_answer": daten.get("has_successful_answer"),
        "total_cost": daten.get("total_cost"),
        "total_queries": daten.get("total_queries"),
        "answer": daten.get("answer"),
        "formatted_answer": daten.get("formatted_answer"),
        "answer_reasoning": daten.get("answer_reasoning"),
    }
    with json_datei:
        json_datei.write(
            json.dumps(kopf, ensure_ascii=False, indent=2) + "\n")
    job_teil = f" = {daten['job_name']}" if daten.get("job_name") else ""
    zeilen = [
        f"# Edison-Recherche ({agent})",
        "",
        f"> {HINWEIS_KI}",
        "",
        f"- Zeitpunkt: {kopf['zeitpunkt']}",
        f"- Agent: {agent} (JobNames.{mitglied}{job_teil})",
        f"- task_id: {daten.get('task_id')}",
        f"- Status: {daten.get('status')}",
        f"- has_successful_answer: {daten.get('has_successful_answer')}",
        f"- total_cost: {daten.get('total_cost')}",
        "",
        "## Frage",
        "",
        frage,
        "",
        "## Antwort",
        "",
        antwort_text or "(keine Antwort im Ergebnis)",
        "",
    ]
    if daten.get("answer_reasoning"):
        zeilen += ["## Begründung", "", str(daten["answer_reasoning"]), ""]
    with md_datei:
        md_datei.write("\n".join(zeilen))
    return md_pfad, json_pfad


def _frage_befehl(args):
    """Der frage-Unterbefehl: Vorschau offline, Aufruf nur mit --live."""
    mitglied = AGENTEN.get(args.agent)
    if mitglied is None:
        print(f"edison: Agent unbekannt: {args.agent!r} "
              f"(bekannt: {', '.join(sorted(AGENTEN))})", file=sys.stderr)
        return 2
    if args.frage is not None:
        frage = args.frage
    else:
        try:
            frage = Path(args.frage_datei).read_text(encoding="utf-8")
        except OSError as fehler:
            print(f"edison: Fragedatei nicht lesbar: "
                  f"{args.frage_datei} ({fehler})", file=sys.stderr)
            return 2
    if not frage.strip():
        print("edison: Frage ist leer", file=sys.stderr)
        return 2
    if len(frage) > FRAGE_MAX_ZEICHEN:
        print(f"edison: Frage zu lang ({len(frage)} Zeichen, Grenze "
              f"{FRAGE_MAX_ZEICHEN})", file=sys.stderr)
        return 2
    ordner = Path(args.ordner) if args.ordner else WURZEL / ORDNER_STANDARD
    eintraege = _lade_zaehler(args.zaehldatei)
    jetzt = datetime.now()
    monatszahl = _zaehle_monat(eintraege, jetzt)

    if not args.live:
        print("edison: Vorschau — was gesendet würde:")
        print(f"  Agent:         {args.agent} (JobNames.{mitglied})")
        print(f"  Fragenlänge:   {len(frage)} Zeichen")
        print(f"  Zielordner:    {ordner}")
        print(f"  Zeitgrenze:    {args.zeitgrenze:g} s")
        print(f"  Monatszähler:  {monatszahl} von {args.monatsgrenze}")
        print("edison: Offline — es wurde nichts gesendet.")
        return 0

    schluessel = _schluessel(args.schluesseldatei)
    if not schluessel:
        print(f"edison: kein Schlüssel — {SCHLUESSEL_VAR} nicht gesetzt "
              f"und die Schlüsseldatei {args.schluesseldatei} liefert "
              f"keinen Wert", file=sys.stderr)
        return 2
    # Grenze prüfen und Startzeile anhängen unter exklusiver Sperre —
    # ein zweiter gleichzeitiger Aufruf wartet, bis die Startzeile steht.
    lauf = uuid.uuid4().hex[:12]
    with _gesperrt(args.zaehldatei):
        monatszahl = _zaehle_monat(_lade_zaehler(args.zaehldatei), jetzt)
        if monatszahl >= args.monatsgrenze:
            print(f"edison: Monatsgrenze erreicht — {monatszahl} Aufrufe "
                  f"im laufenden Monat, Grenze {args.monatsgrenze}. "
                  f"Kein Aufruf.", file=sys.stderr)
            return 5
        _haenge_an(args.zaehldatei, {
            "zeit": jetzt.isoformat(timespec="seconds"), "lauf": lauf,
            "art": "start", "agent": args.agent, "mitglied": mitglied,
            "fragenlaenge": len(frage)})
    try:
        daten = _live_ausfuehren(mitglied, frage, schluessel,
                                 args.zeitgrenze)
    except KeyboardInterrupt:
        _haenge_an(args.zaehldatei, {
            "zeit": datetime.now().isoformat(timespec="seconds"),
            "lauf": lauf, "art": "fehler", "grund": "abgebrochen"})
        print("edison: abgebrochen — der Versuch zählt mit",
              file=sys.stderr)
        return 130
    except ImportError:
        _haenge_an(args.zaehldatei, {
            "zeit": datetime.now().isoformat(timespec="seconds"),
            "lauf": lauf, "art": "fehler", "grund": "edison_client_import"})
        print("edison: Paket edison_client nicht importierbar — "
              "Hülle `edison` benutzen (eigene Umgebung, E-029)",
              file=sys.stderr)
        return 2
    except BaseException as fehler:  # noqa: BLE001 — auch SystemExit & Co.
        meldung = _maskiere(schluessel, f"{type(fehler).__name__}: {fehler}")
        _haenge_an(args.zaehldatei, {
            "zeit": datetime.now().isoformat(timespec="seconds"),
            "lauf": lauf, "art": "fehler", "grund": meldung[:300]})
        print(f"edison: Gegenstelle fehlgeschlagen — {meldung}",
              file=sys.stderr)
        return 4
    daten = _maskiere_daten(schluessel, daten)
    _haenge_an(args.zaehldatei, {
        "zeit": datetime.now().isoformat(timespec="seconds"), "lauf": lauf,
        "art": "ende", "task_id": daten.get("task_id"),
        "status": daten.get("status"),
        "has_successful_answer": daten.get("has_successful_answer"),
        "total_cost": daten.get("total_cost")})
    md_pfad, json_pfad = _schreibe_ergebnis(
        ordner, args.agent, mitglied, frage, daten, jetzt)
    if daten.get("has_successful_answer") is False:
        print(f"edison: gespeichert ({md_pfad}), aber "
              f"has_successful_answer ist False — Antwort kritisch lesen",
              file=sys.stderr)
        return 3
    if not (daten.get("formatted_answer") or daten.get("answer")):
        # Keine Antwort ist kein Erfolg: die Datei bleibt (mit task_id
        # und Frage, damit die Aufgabe wiederzufinden ist), Exit 3.
        print(f"edison: keine Antwort erhalten — Status "
              f"{daten.get('status')!r}, task_id {daten.get('task_id')}. "
              f"Zwischenstand gespeichert: {md_pfad}", file=sys.stderr)
        return 3
    print(f"edison: Antwort gespeichert: {md_pfad}")
    print(f"edison: maschinenlesbar:     {json_pfad}")
    return 0


def _zaehler_befehl(args):
    """Der zaehler-Unterbefehl: Monatsstand zeigen — fasst nie das Netz an."""
    eintraege = _lade_zaehler(args.zaehldatei)
    jetzt = datetime.now()
    zahl = _zaehle_monat(eintraege, jetzt)
    print(json.dumps({
        "monat": jetzt.strftime("%Y-%m"),
        "aufrufe": zahl,
        "monatsgrenze": args.monatsgrenze,
        "frei": max(0, args.monatsgrenze - zahl),
        "zaehldatei": os.path.expanduser(args.zaehldatei),
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="edison.py",
        description="Literaturfragen an Edison Scientific (E-029); ohne "
                    "--live bleibt alles offline.")
    unter = parser.add_subparsers(dest="befehl", required=True)

    frage = unter.add_parser(
        "frage", help="Eine Frage stellen (ohne --live nur Vorschau).")
    frage.add_argument("--agent", required=True,
                       help="literatur | literatur-hoch | vorlaeufer | probe")
    quelle = frage.add_mutually_exclusive_group(required=True)
    quelle.add_argument("--frage", help="Fragetext direkt")
    quelle.add_argument("--frage-datei", help="Datei mit dem Fragetext")
    frage.add_argument("--live", action="store_true",
                       help="wirklich senden (braucht Schlüssel, zählt)")
    frage.add_argument("--ordner", default=None,
                       help=f"Ergebnisordner (Vorgabe {ORDNER_STANDARD})")
    frage.add_argument("--zeitgrenze", type=float, default=ZEITGRENZE,
                       help=f"Zeitgrenze in Sekunden (Vorgabe {ZEITGRENZE:g})")
    frage.add_argument("--monatsgrenze", type=int, default=MONATSGRENZE,
                       help=f"Live-Aufrufe je Monat (Vorgabe {MONATSGRENZE})")
    frage.add_argument("--schluesseldatei", default=SCHLUESSEL_DATEI,
                       help=f"env-Datei mit {SCHLUESSEL_VAR}")
    frage.add_argument("--zaehldatei", default=ZAEHL_DATEI,
                       help="jsonl-Zähldatei der Live-Aufrufe")

    zaehler = unter.add_parser(
        "zaehler", help="Aufrufe im laufenden Monat zeigen (nie Netz).")
    zaehler.add_argument("--zaehldatei", default=ZAEHL_DATEI)
    zaehler.add_argument("--monatsgrenze", type=int, default=MONATSGRENZE)

    args = parser.parse_args(argv)
    if args.monatsgrenze < 0:
        print("edison: --monatsgrenze muss 0 oder größer sein",
              file=sys.stderr)
        return 2
    if args.befehl == "zaehler":
        return _zaehler_befehl(args)
    return _frage_befehl(args)


if __name__ == "__main__":
    sys.exit(main())

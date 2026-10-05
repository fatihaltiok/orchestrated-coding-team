"""Jev-Baustein: schmale Urteile, offline als Standard (Vertrag §12).

Ordnet Zusatz-Kandidaten aus ``kontextpaket.py kandidaten`` (§11.7) nach ihrer
Wichtigkeit für einen Auftrag. Jev ordnet nur — nichts wird entfernt, nichts
wird als erledigt markiert, nichts wird entschieden.

Ohne ``live=True`` bzw. ``--live`` findet kein Netzzugriff statt — auch dann
nicht, wenn ein Schlüssel in der Umgebung steht oder ein Transport
hereingereicht wurde. Fällt Jev aus, läuft alles mit der lokalen Reihenfolge
weiter, und „nicht_bewertet" steht sichtbar daneben.

Kommandozeile::

    python3 tools/jev.py ordne KANDIDATEN.json [--live] [--max N]
        [--ohne-kontrolle] [--modell M]
    python3 tools/jev.py schaetze KANDIDATEN.json [--max N]

Bei ``ordne`` steht „-“ für die Kandidaten auf stdin (UTF-8), damit
``kontextpaket kandidaten … | jev ordne -`` geht (§13.7).

``schaetze`` rechnet nur (Zeichen ÷ 3 = Token, × 0,042 $/Mio Eingabetoken) und
fasst nie das Netz an. Der Betrag wird vor jedem Live-Lauf genannt.

Exit-Codes der Kommandozeile: 0 = Ausgabe vollständig (auch bei
„nicht_bewertet"), 2 = Aufruffehler (Datei fehlt, JSON ungültig, ``--live``
ohne Schlüssel).
"""

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace

API_URL = "https://api.typesafe.ai/v1/systemone"
MODELL_STANDARD = "jev-1.13.0"
MODELL_ALIAS = "jev-latest"
SCHLUESSEL_VAR = "TYPESAFE_API_KEY"
PREIS_JE_MIO_TOKEN = 0.042

# §14 Punkt 4c: gemeinsame Schlüsselsuche für Kontextpaket und U4.
# §14.11 Punkt 1: der Wert muss aus Schlüsselzeichen bestehen — Platzhalter
# wie „<Schlüssel>", „$TYPESAFE_API_KEY", „…" oder „***" treffen nicht.
# §14.12 Punkt 1: weitere realistische Fremdschlüssel-Formen; Bearer jetzt
# ohne Groß-/Kleinschreibung; das erweiterte Label-Muster erlaubt Namensenden
# (z. B. „AWS_SECRET_ACCESS_KEY") und Sonderzeichen im Wert — nur die erste
# Wertstelle bleibt eng, damit Platzhalter weiter nicht treffen. Die zwei
# §14.11-Zeilen sind strikte Teilmengen der erweiterten Muster; sie bleiben
# als Sabotage-Anker der Negativkontrolle f6 (R5) stehen.
_SCHLUESSEL_MUSTER = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(?:api[_-]?key|token|secret)\s*[=:]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # §14.12 Punkt 1:
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:ghp|gho|ghs|ghr|ghu)_[A-Za-z0-9]{20,}"),
    re.compile(r"github" r"_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[bpoas]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # §15.2 Punkt 1: auch die deutschen Label „passwort" und „kennwort"
    # (gleiche Wertregel, gleiche Platzhalter-Freiheit).
    re.compile(r"(?i)(?:api[_-]?key|token|secret|passw(?:or)?d|pwd|access[_-]?key"
               r"|passwort|kennwort)"
               r"[A-Za-z0-9_]*\s*[=:]\s*[\"']?"
               r"[A-Za-z0-9._~+/=-][A-Za-z0-9._~+/=!%:@#$^&*-]{11,}"),
)


def schluessel_treffer(texte):
    """Gibt Bezeichnungen erkannter Schlüssel-Fundstellen zurück, nie Treffer."""
    muster = list(_SCHLUESSEL_MUSTER)
    schluessel = os.environ.get(SCHLUESSEL_VAR, "")
    if len(schluessel) >= 8:
        muster.append(re.compile(re.escape(schluessel)))
    treffer = []
    for bezeichnung, inhalt in texte:
        if any(p.search(str(inhalt)) for p in muster):
            treffer.append(str(bezeichnung))
    return sorted(set(treffer))

# §12.3 Punkt 3: grob mit ≈ 3 Zeichen je Token gegen 32k/64k gerechnet.
GRENZE_ZUSTAND_FRAGE = 90_000
GRENZE_RUMPF = 180_000

# §12.4 Punkt 2: Wortlaut als Konstante; eine Änderung ist eine Vertragsänderung.
WICHTIG_FRAGE = {
    "type": "noul",
    "instructions": "Muss jemand, der den Auftrag ausführt, diesen Abschnitt kennen?",
    "criteria": {
        "true": "Der Abschnitt enthält eine Regel, Bedingung, Entscheidung oder "
                "Erfahrung, die die Arbeit an genau diesem Auftrag verändert oder "
                "einen Fehler dabei verhindert.",
        "false": "Der Abschnitt behandelt nur ein ähnliches Thema oder ist für "
                 "diesen Auftrag ohne Folgen.",
    },
}

# §12.4 Punkt 3: feste, offensichtlich unpassende Kontrolle (Konstante im Code).
KONTROLL_TEXT = (
    "Kontrollrezept, gehört zu keinem Auftrag: 250 g Mehl, 150 g Zucker, "
    "3 Eier und 100 g Butter verrühren, bei 180 Grad 30 Minuten backen und "
    "mit Puderzucker bestäuben."
)
KONTROLL_GRENZE = 0.2

ERLAUBTE_TYPEN = ("noul", "choice", "score")

# §12.8 Punkt 5 (B22): „versioniert" heißt ^jev-\d+\.\d+\.\d+$ — nur dann
# Hinweis modell_abweichend.
MODELL_VERSION_RE = re.compile(r"^jev-\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Antwort:
    """Eine Jev-Antwort: geprüft gültige Antworten plus verworfene."""

    status: str  # "bewertet" | "teilweise" | "nicht_bewertet"
    grund: str | None
    modell: str | None  # aus der Antwort (konkrete Version)
    antworten: dict = field(default_factory=dict)
    ungueltig: dict = field(default_factory=dict)
    eingabe_token: int | None = None
    hinweise: tuple = ()


@dataclass(frozen=True)
class Eintrag:
    """Ein Kandidat in der Ordnung — jeder Eingabekandidat genau einmal."""

    id: str
    lokal_rang: int
    noul: float | None
    status: str  # "bewertet" | "nicht_bewertet"
    grund: str | None


@dataclass(frozen=True)
class Ordnung:
    """Das Ordne-Ergebnis: alle Kandidaten, nichts entfernt."""

    eintraege: tuple
    status_gesamt: str  # "bewertet" | "teilweise" | "nicht_bewertet" | "unzuverlaessig"
    modell: str | None
    eingabe_token: int
    kontrolle: dict | None  # {"noul": float|None, "bestanden": bool|None}


def _maskiere(schluessel, text):
    """Jedes Vorkommen des Schlüssels durch *** ersetzen (§12.3 Punkt 7)."""
    if schluessel and text:
        return text.replace(schluessel, "***")
    return text


def _maskiere_tief(schluessel, wert):
    """_maskiere für geschachtelte Antwortwerte (dict/Liste/Tupel/str).

    Maskiert auch String-Schlüssel (§12.9 Punkt 1): ein ungefragter
    Antwortschlüssel, der den Schlüssel enthält, darf nicht unmaskiert
    in ``ungueltig`` und damit in ``repr()`` landen.
    """
    if not schluessel:
        return wert
    if isinstance(wert, str):
        return _maskiere(schluessel, wert)
    if isinstance(wert, dict):
        return {(_maskiere(schluessel, k) if isinstance(k, str) else k):
                _maskiere_tief(schluessel, v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return type(wert)(_maskiere_tief(schluessel, v) for v in wert)
    return wert


def _ohne_schluessel(schluessel, antwort):
    """Eine Antwort ohne jede Spur des Schlüssels (auch für repr()).

    Maskiert jedes aus der Serverantwort übernommene Feld — auch
    ``modell`` (§12.9 Punkt 1).
    """
    if not schluessel:
        return antwort
    return replace(
        antwort,
        grund=_maskiere(schluessel, antwort.grund),
        modell=_maskiere(schluessel, antwort.modell),
        antworten=_maskiere_tief(schluessel, antwort.antworten),
        ungueltig=_maskiere_tief(schluessel, antwort.ungueltig),
        hinweise=tuple(_maskiere(schluessel, h) for h in antwort.hinweise),
    )


def _pruefe_fragen(fragen):
    """Lokale Prüfung der Fragen vor jedem Senden (§12.3 Punkt 1).

    Programmfehler → ValueError, keine Antwort.
    """
    if not isinstance(fragen, dict):
        raise ValueError("fragen muss eine Map Frageschlüssel → Frage sein")
    for name, frage in fragen.items():
        if not isinstance(frage, dict):
            raise ValueError(f"Frage {name!r}: keine Map")
        typ = frage.get("type")
        if typ not in ERLAUBTE_TYPEN:
            raise ValueError(f"Frage {name!r}: unbekannter Typ {typ!r}")
        if not frage.get("instructions"):
            raise ValueError(f"Frage {name!r}: instructions fehlt")
        if typ == "choice":
            optionen = frage.get("criteria")
            if not isinstance(optionen, dict) or not 1 <= len(optionen) <= 255:
                raise ValueError(
                    f"Frage {name!r}: choice braucht 1–255 Optionen als Map")
        if typ == "score":
            stufen = frage.get("criteria")
            if not isinstance(stufen, (list, tuple)) or not 2 <= len(stufen) <= 10:
                raise ValueError(
                    f"Frage {name!r}: score braucht 2–10 Stufen als Liste")


def _ist_zahl(wert):
    """Zahl, aber kein bool (True == 1 würde sonst als noul durchgehen)."""
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


class _Ungueltig(Exception):
    """Eine einzelne Antwort ist verworfen — mit Begründung."""

    def __init__(self, grund):
        super().__init__(grund)
        self.grund = grund


def _pruefe_noul(wert):
    if not _ist_zahl(wert) or not 0 <= wert <= 1:
        raise _Ungueltig("noul_ausserhalb")
    if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
        raise _Ungueltig("noul_ausserhalb")
    return float(wert)


def _pruefe_choice(frage, wert):
    optionen = list(frage["criteria"])
    wahl = wert.get("choice") if isinstance(wert, dict) else None
    if wahl not in optionen:
        raise _Ungueltig("choice_unbekannt")
    wahr = wert.get("probabilities")
    if (not isinstance(wahr, dict) or set(wahr) != set(optionen)
            or not all(_ist_zahl(v) for v in wahr.values())
            or not all(0 <= v <= 1 for v in wahr.values())):
        raise _Ungueltig("probabilities_falsch")
    if abs(sum(wahr.values()) - 1) > 0.01:
        raise _Ungueltig("probabilities_falsch")
    return wahl


def _pruefe_score(frage, wert):
    stufen = len(frage["criteria"])
    punkt = wert.get("score") if isinstance(wert, dict) else None
    if not _ist_zahl(punkt) or not 0 <= punkt <= stufen - 1:
        raise _Ungueltig("score_ausserhalb")
    if isinstance(punkt, float) and (math.isnan(punkt) or math.isinf(punkt)):
        raise _Ungueltig("score_ausserhalb")
    if "probabilities" in wert and wert["probabilities"] is not None:
        prob = wert["probabilities"]
        # §12.9 Punkt 3: die Schlüsselmenge muss genau {"0", …, "n-1"} sein.
        erwartet = {str(i) for i in range(stufen)}
        if (not isinstance(prob, dict) or set(prob) != erwartet
                or not all(_ist_zahl(v) for v in prob.values())
                or not all(0 <= v <= 1 for v in prob.values())):
            raise _Ungueltig("probabilities_falsch")
    return float(punkt)


def _pruefe_einzel(frage, roh):
    """Eine einzelne Antwort gegen ihre Frage prüfen (§12.3 Punkt 4)."""
    if not isinstance(roh, dict):
        raise _Ungueltig("kein_objekt")
    typ = frage["type"]
    if roh.get("type") != typ:
        raise _Ungueltig("falscher_typ")
    if typ == "noul":
        return _pruefe_noul(roh.get("noul"))
    if typ == "choice":
        return _pruefe_choice(frage, roh)
    return _pruefe_score(frage, roh)


def _live_transport(url, kopf, rumpf, timeout):
    """Eingebauter Transport über urllib (nur hier importiert, §12.1).

    Netzfehler und Timeouts werden in (0, {}, b"") übersetzt, nie als
    Ausnahme durchgereicht. Antwortköpfe mit kleinen Namen.
    """
    from urllib import request as _anfrage
    from urllib.error import HTTPError as _HTTPFehler
    anfrage = _anfrage.Request(url, data=rumpf, headers=kopf, method="POST")
    try:
        with _anfrage.urlopen(anfrage, timeout=timeout) as antwort:
            koepfe = {k.lower(): v for k, v in antwort.headers.items()}
            return antwort.status, koepfe, antwort.read()
    except _HTTPFehler as fehler:
        try:
            roh = fehler.read()
        except Exception:  # noqa: BLE001 — Rumpf ist Beiwerk, Code zählt
            roh = b""
        try:
            koepfe = {k.lower(): v for k, v in fehler.headers.items()}
        except Exception:  # noqa: BLE001 — Köpfe sind Beiwerk
            koepfe = {}
        return fehler.code, koepfe, roh
    except Exception:  # noqa: BLE001 — Netzfehler werden zu Status 0
        return 0, {}, b""


def _wartezeit(koepfe, rueckfall):
    """retry-after (höchstens 30 s) oder exponentieller Rückfall 1/2/4 … s."""
    for name, wert in koepfe.items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, min(30.0, float(str(wert).strip().split(",")[0])))
            except (TypeError, ValueError):
                return rueckfall
    return rueckfall


def frage(state, fragen, *, modell=MODELL_STANDARD, live=False,
          transport=None, schluessel=None, timeout=10.0,
          wiederholungen=2, schlafe=time.sleep):
    """Eine Jev-Anfrage stellen und Antwort für Antwort prüfen (§12.3).

    Ohne live=True wird kein Transport aufgerufen — auch kein
    hereingereichter (Gate 4: kein Netz ohne Live).
    """
    _pruefe_fragen(fragen)

    if not live:
        return Antwort(status="nicht_bewertet", grund="offline", modell=None)

    geheim = schluessel or os.environ.get(SCHLUESSEL_VAR)
    if not geheim:
        return Antwort(status="nicht_bewertet", grund="kein_schluessel",
                       modell=None)

    zustand_text = json.dumps(state, ensure_ascii=False)
    if fragen:
        laengste = max(len(json.dumps(f, ensure_ascii=False))
                       for f in fragen.values())
    else:
        laengste = 0
    rumpf_obj = {"state": state, "model": modell, "questions": fragen}
    rumpf_text = json.dumps(rumpf_obj, ensure_ascii=False)
    if len(zustand_text) + laengste > GRENZE_ZUSTAND_FRAGE \
            or len(rumpf_text) > GRENZE_RUMPF:
        return _ohne_schluessel(
            geheim, Antwort(status="nicht_bewertet", grund="zu_gross",
                            modell=None))

    sende = transport or _live_transport
    kopf = {"Authorization": f"Bearer {geheim}",
            "Content-Type": "application/json"}
    rumpf = rumpf_text.encode("utf-8")

    versuche = 0
    rueckfall = 1.0
    while True:
        try:
            status, koepfe, roh = sende(API_URL, kopf, rumpf, timeout)
        except Exception:  # noqa: BLE001 — Transportfehler werden zu „netz"
            return _ohne_schluessel(
                geheim, Antwort(status="nicht_bewertet", grund="netz",
                                modell=None))
        if status == 0:
            return _ohne_schluessel(
                geheim, Antwort(status="nicht_bewertet", grund="netz",
                                modell=None))
        if status == 200:
            break
        if status == 429 or 500 <= status <= 599:
            if versuche < wiederholungen:
                schlafe(_wartezeit(koepfe or {}, rueckfall))
                rueckfall *= 2
                versuche += 1
                continue
            return _ohne_schluessel(
                geheim, Antwort(status="nicht_bewertet",
                                grund=f"http_{status}", modell=None))
        ausschnitt = _maskiere(
            geheim, roh.decode("utf-8", errors="replace"))[:300]
        return _ohne_schluessel(
            geheim, Antwort(status="nicht_bewertet",
                            grund=f"http_{status}: {ausschnitt}",
                            modell=None))

    try:
        daten = json.loads(roh.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return _ohne_schluessel(
            geheim, Antwort(status="nicht_bewertet",
                            grund="antwort_kein_json", modell=None))
    if not isinstance(daten, dict):
        return _ohne_schluessel(
            geheim, Antwort(status="nicht_bewertet",
                            grund="antwort_kein_json", modell=None))

    roh_modell = daten.get("model")
    gemeldet = roh_modell if isinstance(roh_modell, str) else None
    nutzung = daten.get("usage") or {}
    token = nutzung.get("input_tokens") if isinstance(nutzung, dict) else None
    if not _ist_zahl(token):
        token = None
    hinweise = []
    if (gemeldet and gemeldet != modell
            and isinstance(modell, str)
            and MODELL_VERSION_RE.match(modell)):
        hinweise.append(f"modell_abweichend: {gemeldet}")

    gegeben = daten.get("answers")
    if not isinstance(gegeben, dict):
        gegeben = {}
    antworten, ungueltig = {}, {}
    for name, frage_def in fragen.items():
        if name not in gegeben:
            ungueltig[name] = "fehlt"
            continue
        try:
            antworten[name] = _pruefe_einzel(frage_def, gegeben[name])
        except _Ungueltig as verworfen:
            ungueltig[name] = verworfen.grund
    for name in gegeben:
        if name not in fragen:
            ungueltig[name] = "nicht_gefragt"

    if len(antworten) == len(fragen) and not ungueltig:
        status, grund = "bewertet", None
    elif antworten:
        status = "teilweise"
        grund = "antwort_ungueltig: " + ", ".join(
            sorted(f"{k} ({v})" for k, v in ungueltig.items()))
    else:
        status = "nicht_bewertet"
        if ungueltig:
            grund = "antwort_ungueltig: " + ", ".join(
                sorted(f"{k} ({v})" for k, v in ungueltig.items()))
        else:
            grund = "antwort_ungueltig: keine Antworten"
    return _ohne_schluessel(
        geheim, Antwort(status=status, grund=grund, modell=gemeldet,
                        antworten=antworten, ungueltig=ungueltig,
                        eingabe_token=token, hinweise=tuple(hinweise)))


# §12.9 Punkt 2: absolut ("/", "~", "\", Laufwerksbuchstabe) oder
# ausbrechend (".." als Pfadteil) — dann wird nur der Dateiname gesendet.
_ABSOLUT_RE = re.compile(r"^[A-Za-z]:")


def _norm_quelle(quelle):
    """``quelle`` für den State: relativer Projektpfad, sonst Dateiname.

    Absolute oder ausbrechende Pfade verlassen den Rechner nie — es wird
    nur der Dateiname gesendet (§12.9 Punkt 2, §12.8 Punkt 4).
    """
    if not isinstance(quelle, str):
        return quelle
    teile = quelle.replace("\\", "/").split("/")
    if (quelle.startswith(("/", "~", "\\")) or _ABSOLUT_RE.match(quelle)
            or ".." in teile):
        return quelle.replace("\\", "/").rstrip("/").split("/")[-1]
    return quelle


def _kandidat_zustand(auftrag, kandidat):
    """State je Kandidat in lokaler Reihenfolge (§12.4 Punkt 2, §12.8 Punkt 4).

    Im State steht nur ``quelle`` (relativ, §11.12 Punkt 19), nie der absolute
    Pfad: fehlt ``quelle``, wird nur der Dateiname von ``datei`` gesendet; eine
    absolute oder ausbrechende ``quelle`` wird auf den Dateinamen gekürzt
    (§12.9 Punkt 2).
    """
    innen = {"text": kandidat["text"]}
    if kandidat.get("quelle") is not None:
        innen["quelle"] = _norm_quelle(kandidat["quelle"])
    elif kandidat.get("datei") is not None:
        innen["quelle"] = os.path.basename(str(kandidat["datei"]))
    if "ueberschrift" in kandidat and kandidat["ueberschrift"] is not None:
        innen["ueberschrift"] = kandidat["ueberschrift"]
    return {"auftrag": auftrag, "kandidat": innen}


def _kontroll_zustand(auftrag):
    """Zustand der Negativkontrolle — offensichtlich unpassend (§12.4 Punkt 3)."""
    return {"auftrag": auftrag,
            "kandidat": {"quelle": "kontrolle",
                         "ueberschrift": "Kontrolle (gehört nicht zum Auftrag)",
                         "text": KONTROLL_TEXT}}


def ordne_kandidaten(auftrag, kandidaten, *, max_kandidaten=30,
                     kontrolle=True, **frage_optionen):
    """Kandidaten nach Jev-Wichtigkeit ordnen (§12.4).

    Je Kandidat eine Anfrage mit einer Noul-Frage, in lokaler Reihenfolge,
    höchstens max_kandidaten, keine Parallelität. Jeder Eingabekandidat
    steht genau einmal in der Ausgabe — nichts wird entfernt.
    """
    if not isinstance(kandidaten, list):
        raise ValueError("kandidaten muss eine Liste sein")
    gesehen = set()
    for kandidat in kandidaten:
        if not isinstance(kandidat, dict) or "id" not in kandidat \
                or "text" not in kandidat:
            raise ValueError(
                "jeder Kandidat braucht mindestens id und text (§11.7 Punkt 3)")
        if kandidat["id"] in gesehen:
            raise ValueError(f"doppelte id: {kandidat['id']!r}")
        gesehen.add(kandidat["id"])

    eintraege, bewertet_anzahl = [], 0
    token_summe, meldung_modell = 0, None
    for rang, kandidat in enumerate(kandidaten):
        if rang >= max_kandidaten:
            eintraege.append(Eintrag(id=kandidat["id"], lokal_rang=rang,
                                     noul=None, status="nicht_bewertet",
                                     grund="ueber_max"))
            continue
        antwort = frage(_kandidat_zustand(auftrag, kandidat),
                        {"wichtig": WICHTIG_FRAGE}, **frage_optionen)
        if antwort.modell and not meldung_modell:
            meldung_modell = antwort.modell
        if antwort.eingabe_token:
            token_summe += antwort.eingabe_token
        if "wichtig" in antwort.antworten:
            eintraege.append(Eintrag(id=kandidat["id"], lokal_rang=rang,
                                     noul=float(antwort.antworten["wichtig"]),
                                     status="bewertet", grund=None))
            bewertet_anzahl += 1
        else:
            eintraege.append(Eintrag(id=kandidat["id"], lokal_rang=rang,
                                     noul=None, status="nicht_bewertet",
                                     grund=antwort.grund))

    kontrolle_ergebnis = None
    if kontrolle:
        antwort = frage(_kontroll_zustand(auftrag), {"wichtig": WICHTIG_FRAGE},
                        **frage_optionen)
        if antwort.modell and not meldung_modell:
            meldung_modell = antwort.modell
        if antwort.eingabe_token:
            token_summe += antwort.eingabe_token
        wert = antwort.antworten.get("wichtig")
        wert = float(wert) if wert is not None else None
        if wert is None:
            kontrolle_ergebnis = {"noul": None, "bestanden": None,
                                  "grund": antwort.grund}
        else:
            kontrolle_ergebnis = {"noul": wert,
                                  "bestanden": wert < KONTROLL_GRENZE}

    bewertete = sorted(
        (e for e in eintraege if e.status == "bewertet"),
        key=lambda e: (-e.noul, e.lokal_rang))
    unbewertete = sorted(
        (e for e in eintraege if e.status != "bewertet"),
        key=lambda e: e.lokal_rang)
    reihenfolge = tuple(bewertete + unbewertete)

    if kontrolle_ergebnis and kontrolle_ergebnis.get("bestanden") is False:
        gesamt = "unzuverlaessig"
    elif (kontrolle and kontrolle_ergebnis
            and kontrolle_ergebnis.get("bestanden") is None
            and bewertet_anzahl):
        # §12.7 Punkt 3: Urteil ohne gelaufene Gegenprobe ist nicht
        # verlässlich — die Kontrolle ist angefragt, aber nicht bewertet.
        # Kein bewerteter Kandidat (z. B. offline): bleibt "nicht_bewertet".
        gesamt = "unzuverlaessig"
    elif bewertet_anzahl and bewertet_anzahl == len(eintraege):
        gesamt = "bewertet"
    elif not bewertet_anzahl:
        gesamt = "nicht_bewertet"
    else:
        gesamt = "teilweise"
    return Ordnung(eintraege=reihenfolge, status_gesamt=gesamt,
                   modell=meldung_modell, eingabe_token=token_summe,
                   kontrolle=kontrolle_ergebnis)


def _rumpfzeichen(auftrag, innen, modell):
    """Zeichen einer Anfrage, wie frage sie bauen würde (für schaetze)."""
    rumpf = {"state": {"auftrag": auftrag, "kandidat": innen},
             "model": modell, "questions": {"wichtig": WICHTIG_FRAGE}}
    return len(json.dumps(rumpf, ensure_ascii=False))


def schaetze_kosten(auftrag, kandidaten, *, max_kandidaten=30,
                    kontrolle=True):
    """Kosten eines Live-Laufs nur rechnen — fasst nie das Netz an (§12.5).

    Token = Zeichen ÷ 3 (aufgerundet, damit der Betrag nie zu klein
    ausfällt), × 0,042 $ je Mio Eingabetoken.
    """
    if not isinstance(kandidaten, list):
        raise ValueError("kandidaten muss eine Liste sein")
    zeichen = 0
    for kandidat in kandidaten[:max(0, max_kandidaten)]:
        innen = {"text": kandidat.get("text", "")}
        # §12.8 Punkt 8: dieselben Rümpfe, die frage senden würde — nach
        # Punkt 4 also nur quelle bzw. Dateiname, nie der absolute Pfad
        # (§12.9 Punkt 2: dieselbe Normalisierung wie _kandidat_zustand).
        if kandidat.get("quelle") is not None:
            innen["quelle"] = _norm_quelle(kandidat["quelle"])
        elif kandidat.get("datei") is not None:
            innen["quelle"] = os.path.basename(str(kandidat["datei"]))
        if kandidat.get("ueberschrift") is not None:
            innen["ueberschrift"] = kandidat["ueberschrift"]
        zeichen += _rumpfzeichen(auftrag, innen, MODELL_STANDARD)
    anfragen = len(kandidaten[:max(0, max_kandidaten)])
    if kontrolle:
        zeichen += _rumpfzeichen(
            auftrag, {"quelle": "kontrolle",
                      "ueberschrift": "Kontrolle (gehört nicht zum Auftrag)",
                      "text": KONTROLL_TEXT}, MODELL_STANDARD)
        anfragen += 1
    token = (zeichen + 2) // 3 if zeichen else 0
    return {"anfragen": anfragen, "zeichen": zeichen,
            "token_geschaetzt": token,
            "usd_geschaetzt": token * PREIS_JE_MIO_TOKEN / 1_000_000}


def _ordnung_json(ordnung):
    """Eine Ordnung als JSON-fähige Map (Felder wie §12.2)."""
    return {
        "eintraege": [{"id": e.id, "lokal_rang": e.lokal_rang, "noul": e.noul,
                       "status": e.status, "grund": e.grund}
                      for e in ordnung.eintraege],
        "status_gesamt": ordnung.status_gesamt,
        "modell": ordnung.modell,
        "eingabe_token": ordnung.eingabe_token,
        "kontrolle": ordnung.kontrolle,
    }


def _lies_kandidaten(pfad, *, von_stdin=False):
    """KANDIDATEN.json lesen (Ausgabe von kontextpaket.py kandidaten).

    pfad „-“ liest von stdin (UTF-8) — nur dort freigegeben, wo der
    Aufrufer es erlaubt (§13.7: „ordne“).
    """
    if pfad == "-":
        if not von_stdin:
            print("jev: „-“ (stdin) gibt es nur bei „ordne“", file=sys.stderr)
            raise SystemExit(2)
        try:
            # §15.2 Punkt 5: utf-8-sig entfernt ein führendes BOM; andere
            # ungültige Eingaben bleiben Exit 2 wie gehabt.
            roh = sys.stdin.buffer.read().decode("utf-8-sig")
        except UnicodeDecodeError as fehler:
            print(f"jev: stdin ist kein UTF-8 ({fehler})", file=sys.stderr)
            raise SystemExit(2)
        try:
            daten = json.loads(roh)
        except ValueError:
            print("jev: kein gültiges JSON: - (stdin)", file=sys.stderr)
            raise SystemExit(2)
    else:
        try:
            with open(pfad, encoding="utf-8-sig") as datei:
                daten = json.load(datei)
        except OSError as fehler:
            print(f"jev: Datei nicht lesbar: {pfad} ({fehler})", file=sys.stderr)
            raise SystemExit(2)
        except ValueError:
            print(f"jev: kein gültiges JSON: {pfad}", file=sys.stderr)
            raise SystemExit(2)
    if not isinstance(daten, dict) or not isinstance(daten.get("auftrag"), str) \
            or not isinstance(daten.get("kandidaten"), list):
        quelle = "stdin" if pfad == "-" else pfad
        print(f"jev: {quelle} enthält keine Kandidatenliste mit Auftrag (§11.7)",
              file=sys.stderr)
        raise SystemExit(2)
    return daten["auftrag"], daten["kandidaten"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jev.py",
        description="Jev-Baustein (Vertrag §12): ordnet Zusatz-Kandidaten "
                    "nach Wichtigkeit; ohne --live bleibt alles offline.")
    unter = parser.add_subparsers(dest="befehl", required=True)

    ordne = unter.add_parser(
        "ordne", help="Kandidaten nach Jev-Wichtigkeit ordnen (ohne --live: "
                      "lokale Reihenfolge, alles offline).")
    ordne.add_argument("kandidaten",
                       help="Datei aus kontextpaket.py kandidaten (§11.7); "
                            "„-“ liest von stdin (§13.7).")
    ordne.add_argument("--live", action="store_true",
                       help="Jev wirklich anfragen (braucht TYPESAFE_API_KEY).")
    ordne.add_argument("--max", type=int, default=30,
                       help="Höchstens N Kandidaten anfragen (Rest bleibt "
                            "lokal, Vorgabe 30).")
    ordne.add_argument("--ohne-kontrolle", action="store_true",
                       help="Negativkontrolle auslassen.")
    ordne.add_argument("--modell", default=MODELL_STANDARD,
                       help=f"Angefragte Modellversion (Vorgabe "
                            f"{MODELL_STANDARD}).")

    schaetze = unter.add_parser(
        "schaetze",
        help="Kosten eines Live-Laufs nur rechnen, ohne Netz.")
    schaetze.add_argument("kandidaten",
                          help="Datei aus kontextpaket.py kandidaten (§11.7).")
    schaetze.add_argument("--max", type=int, default=30,
                          help="Höchstens N Kandidaten anfragen (Vorgabe 30).")

    args = parser.parse_args(argv)

    if args.max is None or args.max < 0:
        print("jev: --max muss 0 oder größer sein", file=sys.stderr)
        return 2
    auftrag, kandidaten = _lies_kandidaten(
        args.kandidaten, von_stdin=(args.befehl == "ordne"))

    if args.befehl == "schaetze":
        print(json.dumps(schaetze_kosten(auftrag, kandidaten,
                                         max_kandidaten=args.max),
                         ensure_ascii=False, indent=2))
        return 0

    if args.live:
        if not os.environ.get(SCHLUESSEL_VAR):
            print(f"jev: --live braucht einen Schlüssel in {SCHLUESSEL_VAR} "
                  f"(nicht gesetzt, keine Anfrage gestellt)",
                  file=sys.stderr)
            return 2
        ordnung = ordne_kandidaten(
            auftrag, kandidaten, max_kandidaten=args.max,
            kontrolle=not args.ohne_kontrolle, live=True, modell=args.modell)
    else:
        ordnung = ordne_kandidaten(
            auftrag, kandidaten, max_kandidaten=args.max,
            kontrolle=not args.ohne_kontrolle)
    print(json.dumps(_ordnung_json(ordnung), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

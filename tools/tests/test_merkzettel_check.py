"""Tests für werkzeuge/merkzettel_check.py (Vertrag §10.4, §10.7).

Nur Wegwerf-Merkzettel unter tmp_path — der echte Ordner unter ~/.claude wird
nie angerührt, nicht einmal lesend. Je harter Befundart mindestens ein
auslösender Test, dazu ein sauberer Fall (Exit 0 ohne harten Befund), der
Sammel-Test gegen das Grünfärben, Nur-Lesen, die Platzhalter-Ausnahme von
PFAD_TOT und die Trennung hart/weich beim Exit-Code.
"""

import importlib.util
from pathlib import Path

import pytest

WERKZEUGE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "merkzettel_check", WERKZEUGE / "merkzettel_check.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

HARTE_ARTEN = [
    "KOPF_UNVOLLSTAENDIG",
    "NAME_UNGLEICH_DATEI",
    "VERWAIST",
    "INDEX_ZEIGT_INS_LEERE",
    "VERWEIS_TOT",
    "VERWEIS_MIT_ENDUNG",
    "VERWEIS_KEIN_SLUG",
    "INDEX_ZU_GROSS",
    "PFAD_TOT",
]

# Kopf-Varianten, die KOPF_UNVOLLSTAENDIG auslösen müssen (§10.2 Punkt 1)
KOPF_VARIANTEN = {
    "ohne-name": "---\ndescription: Etwas.\nmetadata:\n  type: user\n---\nInhalt.\n",
    "ohne-description": "---\nname: kaputt\nmetadata:\n  type: user\n---\nInhalt.\n",
    "ohne-type": "---\nname: kaputt\ndescription: Etwas.\n---\nInhalt.\n",
    "falscher-type": "---\nname: kaputt\ndescription: Etwas.\n"
                     "metadata:\n  type: firmenzeichen\n---\nInhalt.\n",
    "ohne-kopf": "Einfach Text ohne YAML-Kopf.\n",
    "kopf-offen": "---\nname: kaputt\ndescription: Etwas.\n",
}


def notiz_text(name, beschreibung="Eine Testnotiz für den Wegwerf-Merkzettel.",
               typ="project", koerper="Inhalt.\n"):
    return (f"---\nname: {name}\ndescription: {beschreibung}\n"
            f"metadata:\n  type: {typ}\n---\n{koerper}")


def baue_merkzettel(tmp_path, notizen, index_zeilen=None):
    """Wegwerf-Merkzettel unter tmp_path anlegen; notizen: {dateiname: text}."""
    ordner = tmp_path / "merkzettel"
    ordner.mkdir()
    for dateiname, text in notizen.items():
        (ordner / dateiname).write_text(text, encoding="utf-8")
    if index_zeilen is None:
        index_zeilen = [f"- [{name[:-3]}]({name}) — Köder für {name[:-3]}"
                        for name in notizen]
    (ordner / "MEMORY.md").write_text("\n".join(index_zeilen) + "\n", encoding="utf-8")
    return ordner


def gesund(tmp_path):
    """Gesunder Wegwerf-Merkzettel: gültige Köpfe, passender Index, lebende Verweise."""
    return baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz",
                                     koerper="Siehe [[zweite-notiz]].\n"),
        "zweite-notiz.md": notiz_text("zweite-notiz", typ="reference",
                                      beschreibung="Zweite Sache, anders als die erste."),
    })


def lauf(capsys, ordner):
    code = mc.main([str(ordner)])
    return code, capsys.readouterr().out


def snapshot(ordner):
    """Inhalt und Änderungszeit jeder Datei — für den Nur-Lesen-Test."""
    stand = {}
    for pfad in sorted(ordner.rglob("*")):
        if pfad.is_file():
            stand[pfad.relative_to(ordner).as_posix()] = (
                pfad.read_bytes(), pfad.stat().st_mtime_ns)
    return stand


# --- Sauberer Fall: Exit 0, kein harter Befund ---------------------------------

def test_sauber_exit_0_ohne_harten_befund(tmp_path, capsys):
    code, out = lauf(capsys, gesund(tmp_path))
    assert code == 0
    for art in HARTE_ARTEN:
        assert art not in out
    assert "keine harten Befunde" in out


# --- KOPF_UNVOLLSTAENDIG ---------------------------------------------------------

@pytest.mark.parametrize("fall", sorted(KOPF_VARIANTEN))
def test_kopf_unvollstaendig_ausgeloest(tmp_path, capsys, fall):
    ordner = baue_merkzettel(
        tmp_path,
        {"erste-notiz.md": notiz_text("erste-notiz")},
        index_zeilen=["- [Erste](erste-notiz.md) — Köder",
                      "- [Kaputt](kaputt.md) — Köder"])
    (ordner / "kaputt.md").write_text(KOPF_VARIANTEN[fall], encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "KOPF_UNVOLLSTAENDIG" in out
    assert "kaputt.md" in out


# --- NAME_UNGLEICH_DATEI ----------------------------------------------------------

def test_name_ungleich_datei(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "falsch.md": notiz_text("richtig"),
    })
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "NAME_UNGLEICH_DATEI" in out
    assert "falsch.md" in out
    assert "richtig" in out


# --- VERWAIST ----------------------------------------------------------------------

def test_verwaist_notiz_ohne_indexzeile(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "allein.md": notiz_text("allein"),
    }, index_zeilen=["- [Erste](erste-notiz.md) — Köder"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWAIST" in out
    assert "allein.md" in out


# --- INDEX_ZEIGT_INS_LEERE ------------------------------------------------------------

def test_index_zeigt_ins_leere(tmp_path, capsys):
    ordner = baue_merkzettel(
        tmp_path,
        {"erste-notiz.md": notiz_text("erste-notiz")},
        index_zeilen=["- [Erste](erste-notiz.md) — Köder",
                      "- [Geist](geist.md) — zeigt ins Leere"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "INDEX_ZEIGT_INS_LEERE" in out
    assert "geist.md" in out


def test_index_zeile_ohne_link_zeigt_ins_leere(tmp_path, capsys):
    ordner = baue_merkzettel(
        tmp_path,
        {"erste-notiz.md": notiz_text("erste-notiz")},
        index_zeilen=["- [Erste](erste-notiz.md) — Köder",
                      "- Nur Text, kein Link"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "INDEX_ZEIGT_INS_LEERE" in out


# --- Mehrere Verweise je Indexzeile (Vertrag §10.3, Paket G2) -------------------------------

def test_index_eintraege_je_link_ein_paar():
    """Paket G2: jeder Link einer Listenzeile ist ein eigener Eintrag — fünf
    Links liefern fünf Paare mit derselben Zeilennummer; eine Listenzeile ohne
    jeden Link liefert genau ein Paar (nr, None). Eingerückte Zeilen zählen
    nicht als Eintrag."""
    text = ("# Kopf\n"
            "- [A](a.md) [B](b.md) [C](c.md) [D](d.md) [E](e.md)\n"
            "  eingerückt: [F](f.md) — zählt nicht\n"
            "* [G](g.md)\n"
            "- ohne Link\n")
    assert mc.index_eintraege(text) == [
        (2, "a.md"), (2, "b.md"), (2, "c.md"), (2, "d.md"), (2, "e.md"),
        (4, "g.md"),
        (5, None),
    ]


def test_drei_links_in_einer_zeile_kein_befund(tmp_path, capsys):
    """Der tragende Fall (Paket G2): eine Indexzeile mit drei Links auf drei
    vorhandene Notizen — kein VERWAIST, kein INDEX_ZEIGT_INS_LEERE, Exit 0."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "zweite-notiz.md": notiz_text("zweite-notiz"),
        "dritte-notiz.md": notiz_text("dritte-notiz"),
    }, index_zeilen=["- [E](erste-notiz.md) [Z](zweite-notiz.md) "
                     "[D](dritte-notiz.md) — drei Verweise, eine Zeile"])
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "VERWAIST" not in out
    assert "INDEX_ZEIGT_INS_LEERE" not in out


def test_drei_links_einer_tot_genau_ein_befund(tmp_path, capsys):
    """Gemischt (Paket G2): drei Links in einer Zeile, einer auf eine fehlende
    Datei — genau ein INDEX_ZEIGT_INS_LEERE, und die beiden anderen Notizen
    gelten nicht als verwaist."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "zweite-notiz.md": notiz_text("zweite-notiz"),
    }, index_zeilen=["- [E](erste-notiz.md) [Z](zweite-notiz.md) [G](geist.md)"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert out.count("INDEX_ZEIGT_INS_LEERE") == 1
    assert "geist.md" in out
    assert "VERWAIST" not in out


def test_verwaist_bleibt_verwaist_bei_mehrverweis_zeilen(tmp_path, capsys):
    """Verwaist bleibt verwaist (Paket G2): eine Notiz, die in keiner Zeile
    verlinkt ist, wird weiterhin gemeldet — auch wenn andere Zeilen mehrere
    Links tragen."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "zweite-notiz.md": notiz_text("zweite-notiz"),
        "allein.md": notiz_text("allein"),
    }, index_zeilen=["- [E](erste-notiz.md) [Z](zweite-notiz.md) — zwei Verweise"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWAIST" in out
    assert "allein.md" in out


def test_zeilennummer_bei_mehreren_links_gleich_und_richtig(tmp_path, capsys):
    """Zeilennummer (Paket G2): jeder Befund einer Mehrverweis-Zeile nennt
    dieselbe, richtige Zeilennummer — hier Zeile 3, weil Überschrift und
    Leerzeile davor mitzählen."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
    }, index_zeilen=["# Kopfzeile",
                     "",
                     "- [E](erste-notiz.md) [G](geist.md) [H](holzweg.md) — drei Verweise"])
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert out.count("INDEX_ZEIGT_INS_LEERE") == 2
    assert out.count("MEMORY.md:3") == 2  # beide toten Links derselben Zeile 3
    assert "geist.md" in out and "holzweg.md" in out


# --- VERWEIS_TOT / VERWEIS_MIT_ENDUNG / VERWEIS_KEIN_SLUG --------------------------------

def test_verweis_tot(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz",
                                     koerper="Siehe [[gibt-es-nicht]].\n")})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWEIS_TOT" in out
    assert "[[gibt-es-nicht]]" in out


def test_verweis_mit_endung_gilt_auch_bei_vorhandener_datei(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz",
                                     koerper="Siehe [[erste-notiz.md]].\n")})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWEIS_MIT_ENDUNG" in out
    assert "VERWEIS_TOT" not in out  # die Endung ist der Befund, nicht der Tod


def test_verweis_ohne_slug_betonung(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz",
                                     koerper="Na gut, [[BITTE PRÜFEN]]!\n")})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWEIS_KEIN_SLUG" in out
    assert "[[BITTE PRÜFEN]]" in out


# --- INDEX_ZU_GROSS --------------------------------------------------------------------------

@pytest.mark.parametrize("art", ["zeilen", "bytes"])
def test_index_zu_gross(tmp_path, capsys, art):
    ordner = baue_merkzettel(tmp_path,
                             {"erste-notiz.md": notiz_text("erste-notiz")})
    index = ordner / "MEMORY.md"
    if art == "zeilen":
        index.write_text(
            "- [E](erste-notiz.md) — k\n" + "<!-- füller -->\n" * mc.ZEILEN_GRENZE,
            encoding="utf-8")
    else:
        index.write_text("- [E](erste-notiz.md) — " + "x" * (mc.GROESSE_GRENZE + 500) + "\n",
                         encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "INDEX_ZU_GROSS" in out
    assert "INDEX_FUELLSTAND" not in out  # über der Grenze ist hart, nicht weich


# --- PFAD_TOT -----------------------------------------------------------------------------------

def test_pfad_tot(tmp_path, capsys):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper="Konfig in `~/gibt-es-nicht/datei.txt` beschrieben.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "PFAD_TOT" in out
    assert "`~/gibt-es-nicht/datei.txt` existiert nicht" in out


def test_pfad_platzhalter_ausnahme(tmp_path, monkeypatch, capsys):
    """§10.4 Punkt 6: Platzhalter und :: melden nicht; tot ohne Platzhalter meldet;
    ein vorhandener Pfad meldet nicht. Der Präfix-Filter wird um tmp_path erweitert,
    damit der vorhandene Pfad echt geprüft wird, ohne außerhalb tmp_path anzulegen."""
    existiert = tmp_path / "wirklich" / "tief"
    existiert.mkdir(parents=True)
    (existiert / "datei.txt").write_text("da\n", encoding="utf-8")
    monkeypatch.setattr(mc, "PFAD_PRAEFIXE",
                        ("~/", "/" + "home/", "/" + "media/", str(tmp_path)))
    koerper = (
        "Bilder liegen in `~/Bilder/<projekt>/x.png`.\n"
        "Funktion: `~/a/b.py::funktion`.\n"
        "Vorlage: `~/vorlagen/{fid}/brief.md`.\n"
        "Vorhanden: `" + str(existiert / "datei.txt") + "`.\n"
        "Fehlt: `" + str(tmp_path / "fehlt-ganz.txt") + "`.\n")
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz", koerper=koerper)})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "PFAD_TOT" in out
    assert "`~/Bilder/<projekt>/x.png`" not in out
    assert "`~/a/b.py::funktion`" not in out
    assert "`~/vorlagen/{fid}/brief.md`" not in out
    assert str(existiert / "datei.txt") not in out  # vorhanden -> kein Befund
    assert "`" + str(tmp_path / "fehlt-ganz.txt") + "` existiert nicht" in out


# --- PFAD_TOT: Kommandozeilen-Ausnahme und ASCII-Auslassung (§10.4 Punkt 6, §10.7 Punkt 4/5) ---

def test_pfad_vorhanden_mit_argumenten_wird_nicht_gemeldet(tmp_path, monkeypatch, capsys):
    """Kommandozeile statt Pfad (§10.4 Punkt 6): existiert der Teil bis zum ersten
    Leerzeichen, gilt der ganze Ausdruck als vorhanden. Echter Fall: ein Befehl
    `.../bin/python -m web.server --gemma` ist kein toter Pfad."""
    python_pfad = tmp_path / "werkzeuge" / "bin" / "python"
    python_pfad.parent.mkdir(parents=True)
    python_pfad.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(mc, "PFAD_PRAEFIXE",
                        ("~/", "/" + "home/", "/" + "media/", str(tmp_path)))
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper=f"Server: `{python_pfad} -m web.server --gemma` gestartet.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "PFAD_TOT" not in out


def test_pfad_tot_mit_leerzeichen_wird_weiter_gemeldet(tmp_path, capsys):
    """Der wichtigste Test des Pakets: die Kommandozeilen-Ausnahme darf die Prüfung
    nicht aushebeln. Ein Pfad mit Leerzeichen, dessen Teil vor dem ersten Leerzeichen
    ebenfalls nicht existiert, bleibt ein Befund (Beispiel: `~/Bilder/projekt-a
    referenz/` — auch `~/Bilder/projekt-a` existiert nicht)."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper="Referenz: `~/gibt-es-nicht/projekt-a referenz/` beschrieben.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "PFAD_TOT" in out
    assert "`~/gibt-es-nicht/projekt-a referenz/` existiert nicht" in out


def test_pfad_mit_leerzeichen_vorhanden_wird_nicht_gemeldet(tmp_path, monkeypatch, capsys):
    """Umgekehrter Fall: existiert der ganze Pfad mit Leerzeichen, meldet der Check
    nicht — auch wenn der Teil vor dem ersten Leerzeichen allein nicht existiert."""
    wirklich = tmp_path / "projekt-a referenz"
    wirklich.mkdir()
    monkeypatch.setattr(mc, "PFAD_PRAEFIXE",
                        ("~/", "/" + "home/", "/" + "media/", str(tmp_path)))
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz", koerper=f"Bilder: `{wirklich}` gesammelt.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "PFAD_TOT" not in out


def test_pfad_ascii_auslassung_wird_nicht_gemeldet(tmp_path, capsys):
    """Die ASCII-Auslassung ... (drei Punkte) zählt wie … als Platzhalter (§10.4
    Punkt 6). Echter Fall: `~/.nvm/.../bin/codex mcp-server` — mit Argumenten, damit
    auch die Kombination aus Auslassung und Kommandozeile sauber durchgewinkt wird."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper="Node: `~/.nvm/.../bin/x`.\n"
                    "MCP: `~/.nvm/.../bin/codex mcp-server`.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "PFAD_TOT" not in out


# --- Sammel-Test gegen das Grünfärben (§10.7 Punkt 1) ---------------------------------------------

def test_sammel_alle_harten_arten_gemeldet(tmp_path, capsys):
    """Jede harte Befundart genau einmal auslösen — alle müssen gemeldet werden,
    nicht nur die erste. Gezählt werden die Befundarten, nicht die Zeilen."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz"),
        "kaputt-kopf.md": "---\nname: kaputt-kopf\ndescription: Kopf ohne Typ.\n---\nInhalt.\n",
        "falscher-name.md": notiz_text("richtiger-name"),
        "verweise.md": notiz_text(
            "verweise",
            koerper="Tot: [[gibt-es-nicht]]. Mit Endung: [[erste-notiz.md]]. "
                    "Betonung: [[BITTE PRÜFEN]]. Pfad: `~/gibt-es-nicht/datei.txt`.\n"),
        "verwaist.md": notiz_text("verwaist"),
    }, index_zeilen=[
        "- [Erste](erste-notiz.md) — Köder",
        "- [Kaputt](kaputt-kopf.md) — Köder",
        "- [Falsch](falscher-name.md) — Köder",
        "- [Verweise](verweise.md) — Köder",
        "- [Geist](geist.md) — zeigt ins Leere",
    ])
    # INDEX_ZU_GROSS über die Größe treiben, ohne neue Einträge
    index = ordner / "MEMORY.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "<!-- füller " + "x" * mc.GROESSE_GRENZE + " -->\n",
        encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 1
    for art in HARTE_ARTEN:
        assert out.count(art) == 1, f"{art} nicht genau einmal gemeldet:\n{out}"


# --- Nur-Lesen (§10.7 Punkt 2) ----------------------------------------------------------------------

def test_nur_lesen_inhalt_und_zeiten_unveraendert(tmp_path, capsys):
    ordner = gesund(tmp_path)
    # kaputter Zustand, damit der Lauf überhaupt etwas zu melden hat
    (ordner / "dritte.md").write_text(
        notiz_text("dritte", koerper="[[fehlt]] und `~/gibtsnicht.txt`\n"),
        encoding="utf-8")
    vorher = snapshot(ordner)
    code, _ = lauf(capsys, ordner)
    assert code == 1
    assert snapshot(ordner) == vorher  # gleiche Dateien, gleicher Inhalt, gleiche Zeiten


# --- NAEHE ohne Urteil (§10.4 Punkt 9) ----------------------------------------------------------------

def test_naehe_beeinflusst_exit_nicht(tmp_path, capsys):
    beschreibung = "Ein Coding-Agent, der Bauaufträge im Revier umsetzt."
    ordner = baue_merkzettel(tmp_path, {
        "mercury-agent.md": notiz_text("mercury-agent", beschreibung=beschreibung),
        "muse-agent.md": notiz_text("muse-agent", beschreibung=beschreibung),
    })
    code, out = lauf(capsys, ordner)
    assert code == 0  # NAEHE ist kein Gate
    assert "NAEHE" in out
    assert "mercury-agent" in out and "muse-agent" in out


# --- archiv/ wird übersprungen (§10.6) -------------------------------------------------------------------

def test_archiv_wird_uebersprungen(tmp_path, capsys):
    ordner = gesund(tmp_path)
    archiv = ordner / "archiv"
    archiv.mkdir()
    (archiv / "kaputt.md").write_text(
        "Völlig kaputt: kein Kopf, [[fehlt]], `~/weg/x.txt`\n", encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "kaputt" not in out


def test_verweis_auf_archiv_gilt_als_tot(tmp_path, capsys):
    ordner = gesund(tmp_path)
    (ordner / "archiv").mkdir()
    (ordner / "archiv" / "alt-notiz.md").write_text(notiz_text("alt-notiz"),
                                                    encoding="utf-8")
    (ordner / "erste-notiz.md").write_text(
        notiz_text("erste-notiz", koerper="Verweist auf [[alt-notiz]].\n"),
        encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 1
    assert "VERWEIS_TOT" in out
    assert "alt-notiz" in out


# --- Weiche Befunde allein: Exit bleibt 0 (§10.4) ----------------------------------------------------------

def test_index_fuellstand_allein_exit_0(tmp_path, capsys):
    ordner = gesund(tmp_path)
    schranke = int(mc.ZEILEN_GRENZE * mc.FUELLSTAND_AB)  # 170 von 200 Zeilen
    fueller = "\n".join(f"<!-- füller {nr} -->" for nr in range(schranke - 2))
    index = ordner / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8") + fueller + "\n", encoding="utf-8")
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "INDEX_FUELLSTAND" in out


def test_alterungssignal_allein_exit_0(tmp_path, capsys):
    # Vertragsänderung (§10.4 Punkt 8, Stand 2026-09-21): die alte Zusicherung dieses
    # Tests war „fertig löst ALTERUNGS_SIGNAL aus“ — `fertig` und `erledigt` sind aus
    # der Markerliste gefallen (Zustand statt Altern) und dürfen nicht mehr auslösen.
    # Als Auslöser dient jetzt `überholt`; der Zweck (weicher Befund allein → Exit 0)
    # bleibt unverändert.
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz", koerper="Diese Notiz ist überholt und kann ins Archiv.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "ALTERUNGS_SIGNAL" in out


# --- ALTERUNGS_SIGNAL: Wortgrenzen und geschärfte Liste (§10.4 Punkt 8, §10.7 Punkt 4/5) --------

@pytest.mark.parametrize("satz", [
    "Diese Notiz ist überholt.",                # Satzzeichen hinter dem Wort zählt als Grenze
    "Der Eintrag „überholt“ bleibt stehen.",    # Anführungszeichen ebenso
    "Als ÜBERHOLT markiert.",                   # Groß-/Kleinschreibung bleibt egal
    "Der Ansatz ist veraltet.",
    "Der Teil ist hinfällig.",
    "Die Route wird ersetzt durch die neue Führung.",  # mehrwortiger Marker
    "Der Plan gilt nicht mehr.",                       # mehrwortiger Marker
])
def test_alterung_marker_loesen_aus(tmp_path, capsys, satz):
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text("erste-notiz", koerper=satz + "\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0  # weicher Befund, der Exit bleibt 0
    assert "ALTERUNGS_SIGNAL" in out


def test_alterung_ganzes_wort_loest_nicht_aus(tmp_path, capsys):
    """Gesucht wird als ganzes Wort (§10.4 Punkt 8): kein Buchstabe/Ziffer unmittelbar
    davor oder dahinter. `unveraltet` enthält `veraltet` nur als Teilstring — genau
    gegen diese Verwechslung sichert dieser Test ab (Gegenprobe b wird hier rot)."""
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper="Der Entwurf ist unfertig, der Prototyp wird noch gefertigt, "
                    "und der Stand ist unveraltet.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "ALTERUNGS_SIGNAL" not in out


def test_alterung_fertig_und_erledigt_loesen_nicht_mehr_aus(tmp_path, capsys):
    # Vertragsänderung (§10.4 Punkt 8, Stand 2026-09-21), Absicht festgehalten:
    # `fertig` und `erledigt` beschreiben einen Zustand („das Video ist fertig“),
    # kein Altern — sie sind aus der Markerliste gefallen und lösen bewusst nicht aus.
    ordner = baue_merkzettel(tmp_path, {
        "erste-notiz.md": notiz_text(
            "erste-notiz",
            koerper="Das Video ist fertig, die Aufgabe ist erledigt.\n")})
    code, out = lauf(capsys, ordner)
    assert code == 0
    assert "ALTERUNGS_SIGNAL" not in out


# --- Exit 2: Aufruffehler ------------------------------------------------------------------------------------

def test_exit_2_ordner_fehlt(tmp_path, capsys):
    assert mc.main([str(tmp_path / "gibt-es-nicht")]) == 2


def test_exit_2_ohne_memory_md(tmp_path, capsys):
    ordner = tmp_path / "leer"
    ordner.mkdir()
    assert mc.main([str(ordner)]) == 2


# --- Vertragstreue ---------------------------------------------------------------------------------------------

def test_ohne_argument_gilt_standardordner(tmp_path, monkeypatch, capsys):
    ordner = gesund(tmp_path)
    monkeypatch.setattr(mc, "STANDARD_ORDNER", ordner)
    assert mc.main([]) == 0
    assert str(ordner) in capsys.readouterr().out


def test_quelltext_schreibt_nie():
    """Rein lesend (§10.1 Punkt 3): der Quelltext darf nichts Schreibendes enthalten."""
    quelle = (WERKZEUGE / "merkzettel_check.py").read_text(encoding="utf-8")
    for verboten in ("write_text", "write_bytes", "open(", "mkdir", "unlink",
                     "rmtree", "shutil", ".touch", "os.remove", "os.rename"):
        assert verboten not in quelle, \
            f"Quelltext enthält {verboten!r} — der Check muss rein lesend bleiben"

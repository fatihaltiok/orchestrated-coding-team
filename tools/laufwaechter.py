"""Phase 4: Laufwächter — Agentenläufe als systemd-Einheiten überwachen
(Vertrag: INTERFACES.md §8.1 und §9).

    laufwaechter.py start NAME --ordner DIR --marke PFAD --log PFAD [--env K=V ...]
                         [--melde-an UUID] [--teamkanal-db PFAD] -- BEFEHL [ARG ...]
    laufwaechter.py start NAME --ordner REPO --arbeitskopie [--basis REV] [--verlinke PFAD ...]
                         [--trotz-uncommittet] --marke PFAD --log PFAD [--env K=V ...]
                         [--melde-an UUID] [--teamkanal-db PFAD] -- BEFEHL ...
    laufwaechter.py warte NAME [--max-sekunden 540] [--stille-sekunden 1200]
    laufwaechter.py status NAME
    laufwaechter.py ergebnis NAME
    laufwaechter.py abschliessen NAME --uebernehmen (--gate BEFEHL ... | --ohne-gate)
    laufwaechter.py abschliessen NAME --verwerfen
    laufwaechter.py melde --name NAME --marke MARKE --log LOG --an UUID [--db PFAD]
                         [--lauf ID]

Ein Lauf wird nur über zwei Signale beurteilt: die **Marke** (Datei, die der Auftrag als
allerletzte Handlung anlegt) und der Zustand der Einheit `lw-NAME` (systemctl --user is-active).
Nie über Prozessnamen oder Kommandotext.

Mit --arbeitskopie (§9) läuft der Befehl in einer eigenen Git-Arbeitskopie auf dem Zweig
lw/NAME des Repos; `ergebnis` sichert den Stand und zeigt ihn (vollständiger Diff in
$LAUFWAECHTER_ZUSTAND/NAME.diff), `abschliessen --uebernehmen` führt den Zweig nach
bestandenen Gates ins Hauptrepo zusammen, `abschliessen --verwerfen` sichert und entfernt
die Arbeitskopie (der Zweig bleibt stehen). Die Git-Handgriffe stehen in
tools/arbeitskopie.py; ohne --arbeitskopie verhält sich das Werkzeug wie in §8.1.

Mit --melde-an (§17.12) hängt die Hülle nach dem Lauf einen Unterbefehl "melde" an, der
die Ergebniszeile (fertig/abgebrochen, letzte Exit-Zahl) als note an einen
Teamkanal-Empfänger schickt — idempotent, ohne die Marke zu ändern. Die Vorprüfung
(UUID, vorhandene DB, Empfänger-Typ) läuft vor jedem Seiteneffekt des Starts.
Der Start erzeugt dazu eine Lauf-ID (uuid4, im Zustand: melde_lauf) und gibt sie der
Hülle als "melde … --lauf ID" mit (§17.13 Punkt 4): Thema und Idempotenz hängen dann
an der Lauf-ID, je Lauf ein eigenes Gespräch — gleicher Laufname erzeugt zwei
Gespräche. Ein vom Empfänger geschlossenes Gespräch wird nie wiederverwendet. Ohne
--lauf (melde von Hand) gilt das Verhalten von §17.12 unverändert.

Exit-Codes:
    0  gestartet bzw. FERTIG (Marke da) bzw. Ergebnis angesehen / abgeschlossen;
       bei melde: Meldung gesendet
    1  systemd-run fehlgeschlagen; bei melde: Meldung fehlgeschlagen (Grund auf
       stdout) — auch: Codex-Zustellung fehlgeschlagen (die Nachricht ist gespeichert,
       §17.13 Punkt 5)
    2  unbekannter NAME oder unbrauchbarer Aufruf — auch: --melde-an ungeeignet
       (Vorprüfung §17.12, vor jedem Seiteneffekt)
    3  LÄUFT NOCH (--max-sekunden erreicht; erneut "warte" aufrufen)
    4  ABGEBROCHEN (Einheit nicht aktiv, keine Marke)
    5  Doppelstart (Zustand vorhanden und Einheit aktiv); bei --arbeitskopie auch:
       Zweig lw/NAME oder Arbeitskopie-Pfad existiert schon
    6  Marke existiert schon (Lauf würde sofort als fertig gelten)
    7  HÄNGT VERMUTLICH (--stille-sekunden ohne Dateiänderung unter --ordner)
    8  Hauptrepo nicht bereit (uncommittete Änderungen, detached HEAD); beim Übernehmen:
       Hauptrepo nicht sauber oder nicht auf dem Zweig vom Start
    9  Lauf läuft noch (ergebnis/abschliessen)
   10  Gate in der Arbeitskopie rot — oder es hat den geprüften Stand verändert (§9.5
       Punkt 6); nichts übernommen
   11  Konflikt beim Zusammenführen — nichts übernommen
   12  Ergebnis nicht (oder nicht in diesem Stand) angesehen
   13  Fremder Verweis (Symlink aus der Arbeitskopie heraus, §9.6) — nichts
       gesichert bzw. nichts übernommen; Hauptrepo unberührt, Arbeitskopie steht

Zum Testen: LAUFWAECHTER_ZUSTAND (Zustandsordner, Vorgabe ~/.local/state/laufwaechter/),
LAUFWAECHTER_TAKT (Sekunden zwischen zwei Prüfungen, Vorgabe 10) und
LAUFWAECHTER_ARBEITSKOPIEN (Ordner für die Arbeitskopien, Vorgabe
~/.local/share/laufwaechter/arbeitskopien/).
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
import uuid

import arbeitskopie
import teamkanal

ZUSTAND_VORGABE = pathlib.Path.home() / ".local" / "state" / "laufwaechter"
ARBEITSKOPIEN_VORGABE = (pathlib.Path.home() / ".local" / "share" / "laufwaechter"
                         / "arbeitskopien")
NAME_MUSTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")  # taugt als systemd-Einheitenname
LOG_ZEILEN = 5          # wie viele Zeilen bei ABGEBROCHEN und im status gezeigt werden
WARTEN_AKTIV = ("active", "activating", "reloading")  # gilt als aktiv (Start-Fenster abdecken)
UNCOMMITTET_ANZAHL = 10  # so viele Pfade werden bei Exit 8 höchstens aufgezählt
LAUFWAECHTER_SKRIPT = str(pathlib.Path(__file__).resolve())  # absolut, für die melde-Hülle (§17.12)


def zustandspfad(name):
    ordner = os.environ.get("LAUFWAECHTER_ZUSTAND")
    return pathlib.Path(ordner if ordner else ZUSTAND_VORGABE) / (name + ".json")


def arbeitskopien_ordner():
    ordner = os.environ.get("LAUFWAECHTER_ARBEITSKOPIEN")
    return pathlib.Path(ordner) if ordner else ARBEITSKOPIEN_VORGABE


def lade_zustand(name):
    try:
        return json.loads(zustandspfad(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def speichere_zustand(z):
    p = zustandspfad(z["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(z, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def einheit_aktiv(einheit):
    r = subprocess.run(["systemctl", "--user", "is-active", einheit],
                       capture_output=True, text=True)
    return r.stdout.strip() in WARTEN_AKTIV


def relativ_aufgeloest(pfad, ordner):
    p = pathlib.Path(pfad)
    if not p.is_absolute():
        p = pathlib.Path(ordner) / p
    return str(p)


def hinweis_zweig_loeschen(zweig):
    """Einziges Stelle im Quelltext, die sagt, wie ein lw-Zweig von Hand endgültig gelöscht
    wird (§9.1: das Werkzeug löscht lw/NAME nie selbst — außer beim Aufräumen des eigenen
    fehlgeschlagenen Starts, siehe arbeitskopie.py). Der Quelltext-Test in
    tests/test_arbeitskopie.py erlaubt diese Handgriffs-Wörter nur hier und dort."""
    return f"git branch -D {zweig}"


def hinweis_fremde_verweise(funde, kopie):
    """Meldung zu fremden Verweisen (§9.6 Punkt 4): je Fund PFAD -> ZIEL und
    wörtlich, was zu tun ist — den Verweis entfernen mit rm PFAD (nie rm -r, das
    folgte dem Verweis und löschte die echten Daten), oder den Lauf neu starten
    mit --verlinke PFAD. Hauptrepo unberührt, Arbeitskopie bleibt stehen."""
    print(f"Fremde Verweise in der Arbeitskopie {kopie} (§9.6) — Hauptrepo unberührt, "
          "die Arbeitskopie bleibt stehen:", file=sys.stderr)
    for pfad, ziel in funde:
        print(f"  {pfad} -> {ziel}", file=sys.stderr)
        print(f"  diesen Verweis entfernen mit: rm {pfad}", file=sys.stderr)
        print("    (nie rm -r — das folgt dem Verweis und löscht die echten Daten)",
              file=sys.stderr)
        print(f"  oder den Lauf neu starten mit: --verlinke {pfad}", file=sys.stderr)


def hinweis_aufraeumen(zweig, kopie):
    """Die einzige weitere Stelle im Quelltext, die sagt, wie eine gebliebene
    Arbeitskopie mit Gewalt entfernt und ein lw-Zweig endgültig gelöscht wird
    (§9.5 Punkt 7: die Warnung nach gescheitertem Aufräumen nennt die genauen
    Handgriffe). Der Quelltext-Test in tests/test_arbeitskopie.py erlaubt diese
    Handgriffs-Wörter nur hier und in den dokumentierten Stellen von arbeitskopie.py."""
    return [f"git worktree remove --force {kopie}", f"git branch -D {zweig}"]


def warne_kontext_fehlt(ordner, marke_arg, repo=None):
    """Kontextpaket-Warnung beim Start (§15.1): eine Zeile auf stderr, wenn ORDNER eine
    KONTEXT.json hat, die Marke auf der Kommandozeile auf -FERTIG endet und die
    erwartete Paket-Datei fehlt (docs/auftraege/x-FERTIG → docs/auftraege/x-kontext.md).
    Nur Warnung, nie Abbruch — Exit, Zustand und Start bleiben unverändert; nichts wird
    geschrieben. Mit repo (Hauptrepo beim Arbeitskopie-Start) zählt die Datei nur, wenn
    sie in HEAD steht (git cat-file -e), denn nur Committetes kommt in die Arbeitskopie;
    liegt sie nur uncommittet vor, trägt die Warnung den Zusatz "(liegt vor, ist aber
    nicht committet)"."""
    if not marke_arg.endswith("-FERTIG"):
        return
    if not os.path.exists(os.path.join(ordner, "KONTEXT.json")):
        return
    erwartet = relativ_aufgeloest(marke_arg[:-len("-FERTIG")] + "-kontext.md", ordner)
    pfad = os.path.relpath(erwartet, ordner)
    if repo is None:
        if os.path.exists(erwartet):
            return
        zusatz = ""
    else:
        gefunden = arbeitskopie.git(repo, "cat-file", "-e", "HEAD:" + pfad,
                                    lesend=True, pruefen=False)
        if gefunden.returncode == 0:
            return
        zusatz = (" (liegt vor, ist aber nicht committet)"
                  if os.path.exists(os.path.join(repo, pfad)) else "")
    auftrag = pfad[:-len("-kontext.md")] + ".md"
    print(f"WARNUNG KONTEXT_FEHLT: {pfad} fehlt{zusatz} — das Repo hat eine "
          f"KONTEXT.json (E-018). Bauen: kontextpaket bau . --auftrag-datei {auftrag} "
          f"> {pfad}", file=sys.stderr)


def letzte_logzeilen(log):
    try:
        zeilen = pathlib.Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(kein Log vorhanden)"]
    return zeilen[-LOG_ZEILEN:] if zeilen else ["(Log leer)"]


def stillstand_sekunden(z):
    """Sekunden seit der letzten Dateiänderung unter --ordner (ohne .git/) bzw. im Log.

    Gibt es dort noch gar nichts, zählt ab dem Startzeitpunkt des Laufs.
    """
    neuest = datetime.datetime.fromisoformat(z["gestartet"]).timestamp()
    for wurzel, unter, dateien in os.walk(z["ordner"]):
        unter[:] = [u for u in unter if u != ".git"]
        for d in dateien:
            try:
                neuest = max(neuest, os.path.getmtime(os.path.join(wurzel, d)))
            except OSError:
                pass
    try:
        neuest = max(neuest, os.path.getmtime(z["log"]))
    except OSError:
        pass
    return max(0.0, time.time() - neuest)


# --- melde (§17.12) --------------------------------------------------------------

def melde_db_pfad(db):
    """Teamkanal-DB für --melde-an bzw. --db: Vorgabe teamkanal.DEFAULT_DB, ~ über HOME."""
    if db is None:
        return str(teamkanal.DEFAULT_DB)
    return os.path.abspath(os.path.expanduser(db))


def melde_ziel_pruefen(melde_an, db):
    """Vorprüfung nach §17.12 Punkt 2 — VOR JEDEM Seiteneffekt des Starts: gültige
    UUID, vorhandene DB (wird nicht neu angelegt), Empfänger vom Typ claude/codex.
    Rückgabe None (in Ordnung) oder 2, mit deutscher Meldung auf stderr."""
    try:
        teamkanal.uid(melde_an, "--melde-an")
    except teamkanal.TeamError as fehler:
        print(f"--melde-an {melde_an!r}: {fehler}", file=sys.stderr)
        return 2
    if not os.path.exists(db):
        print(f"Teamkanal-DB {db} existiert nicht — für --melde-an wird sie nicht neu "
              "angelegt.", file=sys.stderr)
        return 2
    try:
        ziel = teamkanal.Store(db)
        try:
            person = ziel.status(melde_an)["participant"]
        finally:
            ziel.close_db()
    except Exception as fehler:
        print(f"Teamkanal-Empfänger {melde_an}: {fehler}", file=sys.stderr)
        return 2
    if person["kind"] not in ("claude", "codex"):
        print(f"Teamkanal-Empfänger {melde_an} hat den Typ {person['kind']} — "
              "empfangsberechtigt sind nur claude und codex.", file=sys.stderr)
        return 2
    return None


def letzte_exit_zahl(log):
    """Zahl aus der letzten LAUFWAECHTER-EXIT-Zeile des Laufs — sonst "unbekannt"."""
    try:
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unbekannt"
    treffer = re.findall(r"LAUFWAECHTER-EXIT (\d+)", text)
    return treffer[-1] if treffer else "unbekannt"


# --- start ----------------------------------------------------------------------

def starte_einheit(befehl, einheit, ordner, marke, log, umwelt, melde=None):
    """Hülle bauen und systemd-run aufrufen; Rückgabe ist der Exit von systemd-run.

    Die Log-Umleitung der Hülle scheitert sonst VOR dem Befehl, wenn der Ordner fehlt
    (Befund 19.09.). Mit melde=(name, an, db, lauf) hängt die Hülle nach der EXIT-Zeile
    den Unterbefehl melde an (§17.12 Punkt 3, §17.13 Punkt 4 — die Lauf-ID als
    --lauf); ohne --melde-an bleibt die Hülle byte-gleich zum Stand davor.
    """
    for pfad in (log, marke):
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
    huelle = (f"{shlex.join(befehl)} </dev/null >{shlex.quote(log)} 2>&1"
              f'; printf \'LAUFWAECHTER-EXIT %s\\n\' "$?" >>{shlex.quote(log)}')
    if melde is not None:
        name, an, db, lauf = melde
        huelle += (f"; {shlex.quote(sys.executable)} {shlex.quote(LAUFWAECHTER_SKRIPT)} melde"
                   f" --name {shlex.quote(name)} --marke {shlex.quote(marke)}"
                   f" --log {shlex.quote(log)} --an {shlex.quote(an)} --db {shlex.quote(db)}"
                   f" --lauf {shlex.quote(lauf)}"
                   f" >>{shlex.quote(log)} 2>&1")
    cmd = ["systemd-run", "--user", f"--unit={einheit}", "--collect",
           f"--working-directory={ordner}"]
    for schluessel in ("PATH", "HOME"):  # Vorgabe-Reihenfolge; --env darf überschreiben
        cmd.append(f"--setenv={schluessel}={os.environ[schluessel]}")
    cmd += ["--setenv=" + paar for paar in umwelt]
    cmd += ["/bin/sh", "-c", huelle]
    return subprocess.run(cmd).returncode


def start_mit_arbeitskopie(a, einheit, alt, umwelt, melde=None):
    """start --arbeitskopie (Vertrag §9.1); Exit wie dort nummeriert.
    melde (§17.12) wird unverändert an starte_einheit und den Zustand durchgereicht."""
    repo = os.path.realpath(a.ordner)
    if arbeitskopie.oberste_ebene(repo) != repo:
        print(f"--ordner {repo} ist nicht die oberste Ebene eines Git-Repos "
              "(git rev-parse --show-toplevel).", file=sys.stderr)
        return 2
    hauptzweig = arbeitskopie.aktueller_zweig(repo)
    if hauptzweig is None:
        print(f"{repo} steht auf detached HEAD — ein Arbeitskopie-Lauf braucht einen "
              "Zweig im Hauptrepo.", file=sys.stderr)
        return 8
    zweig = arbeitskopie.arbeitskopien_zweig(a.name)
    uncommittet = arbeitskopie.status_porcelain(repo)
    if uncommittet:
        print("Das Hauptrepo hat uncommittete Änderungen — diese kämen NICHT in die "
              "Arbeitskopie (typisch: der gerade geschriebene Auftrag):", file=sys.stderr)
        for zeile in uncommittet[:UNCOMMITTET_ANZAHL]:
            print("  " + zeile, file=sys.stderr)
        if len(uncommittet) > UNCOMMITTET_ANZAHL:
            print(f"  … und {len(uncommittet) - UNCOMMITTET_ANZAHL} weitere", file=sys.stderr)
        if a.trotz_uncommittet:
            print(f"--trotz-uncommittet: Start trotzdem — die {len(uncommittet)} "
                  "uncommitteten Änderungen bleiben im Hauptrepo zurück.")
        else:
            print("Abbruch — wer trotzdem starten will: --trotz-uncommittet.", file=sys.stderr)
            return 8
    if arbeitskopie.zweig_existiert(repo, zweig):
        print(f"Zweig {zweig} existiert schon im Hauptrepo — von Hand entfernen mit "
              f"{hinweis_zweig_loeschen(zweig)}; das Werkzeug löscht ihn nie selbst.",
              file=sys.stderr)
        return 5
    kopie = arbeitskopien_ordner() / a.name
    if kopie.exists() or kopie.is_symlink():
        print(f"Arbeitskopie-Pfad {kopie} existiert schon — erst entfernen oder anderen "
              "Laufnamen wählen.", file=sys.stderr)
        return 5
    if alt is not None:
        print("alter Lauf beendet, ersetze Zustand.")

    kopie.parent.mkdir(parents=True, exist_ok=True)
    try:
        basis = arbeitskopie.volle_sha(repo, a.basis or "HEAD")
    except arbeitskopie.GitFehler as fehler:
        print(f"--basis {a.basis!r} ist keine brauchbare Revision: {fehler}", file=sys.stderr)
        return 2
    if not arbeitskopie.ist_vorfahre(repo, basis, "HEAD"):  # §9.5 Punkt 1
        print(f"--basis {a.basis or 'HEAD'} ({basis[:8]}) ist kein Vorfahre von "
              f"{hauptzweig} — die Übernahme holte sonst fremde, nie angezeigte "
              "Historie ins Hauptrepo.", file=sys.stderr)
        return 2

    def raeum_auf(woran):
        for meldung in arbeitskopie.raeume_gescheiterten_start_weg(repo, zweig, kopie):
            print(f"Aufräumen nach {woran}: {meldung}", file=sys.stderr)

    try:
        arbeitskopie.baue_arbeitskopie(repo, zweig, kopie, basis)
    except arbeitskopie.GitFehler as fehler:
        print(f"git worktree add fehlgeschlagen: {fehler}", file=sys.stderr)
        raeum_auf("gescheitertem git worktree add")
        return 1
    verlinkt = []
    for angabe in a.verlinke:
        try:
            ziel, rel = arbeitskopie.relativ_zu_repo(repo, angabe)
            if not ziel.exists():
                raise ValueError(f"{ziel} existiert nicht im Hauptrepo")
            if not arbeitskopie.ist_ignoriert(repo, rel):
                raise ValueError(f"{rel} ist nicht von Git ignoriert — verlinkt werden nur "
                                 "ignorierte, aber nötige Dinge (.env, .venv, node_modules)")
            if ziel.is_dir():  # §9.5 Punkt 2: Ordner als Verweis, Dateien als Kopie
                arbeitskopie.verlinke_erstellen(kopie, rel, ziel)
                print(f"WARNUNG: {rel} liegt als Verweis in der Arbeitskopie — "
                      f"Schreibzugriffe dort ({ziel}) treffen das Hauptrepo direkt. "
                      "Von Hand angelegte Verweise halten die Übernahme ab jetzt mit "
                      "Exit 13 an (§9.6) — nur diese angemeldeten Verweise sind "
                      "erlaubt.")
            else:
                arbeitskopie.kopiere_datei(kopie, rel, ziel)
            verlinkt.append(str(rel))
        except (ValueError, arbeitskopie.GitFehler, OSError) as fehler:
            print(f"--verlinke {angabe}: {fehler}", file=sys.stderr)
            raeum_auf("gescheiterter Verlinkung")
            return 2
    marke = relativ_aufgeloest(a.marke, kopie)
    log = relativ_aufgeloest(a.log, kopie)
    if os.path.exists(marke):
        print(f"Marke {marke} existiert schon — der Lauf würde sofort als fertig gelten. "
              "Erst die Marke löschen.", file=sys.stderr)
        raeum_auf("vorhandener Marke")
        return 6
    warne_kontext_fehlt(repo, a.marke, repo=repo)  # §15.1 — unmittelbar vor dem Start
    rueckgabe = starte_einheit(a.befehl, einheit, str(kopie), marke, log, umwelt, melde=melde)
    if rueckgabe != 0:
        print(f"systemd-run fehlgeschlagen (Exit {rueckgabe}); kein Zustand gespeichert, "
              "die Arbeitskopie wird entfernt.", file=sys.stderr)
        raeum_auf("gescheitertem systemd-run")
        return 1
    zustand = {
        "name": a.name,
        "einheit": einheit,
        "ordner": str(kopie),  # die Arbeitskopie — warte (Stillstandsmessung) bleibt gültig
        "marke": marke,
        "log": log,
        "befehl": a.befehl,
        "gestartet": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "arbeitskopie": str(kopie),
        "repo": repo,
        "zweig": zweig,
        "hauptzweig": hauptzweig,
        "basis": basis,
        "verlinkt": verlinkt,  # §9.5 Punkt 2: relative Pfade (Dateien und Ordner)
        "ergebnis_commit": None,
        "abgeschlossen": None,
    }
    if melde is not None:  # §17.12 Punkt 1: nur gesetzt, wenn --melde-an kam
        zustand["melde_an"] = melde[1]
        zustand["teamkanal_db"] = melde[2]
        zustand["melde_lauf"] = melde[3]  # §17.13 Punkt 4: Lauf-ID für die Hülle
    speichere_zustand(zustand)
    zusatz = (f" — kopiert/verlinkt: {', '.join(verlinkt)}") if verlinkt else ""
    print(f"Gestartet als {einheit} in Arbeitskopie {kopie} (Zweig {zweig}, "
          f"Basis {basis[:8]}) — Log: {log}, Marke: {marke}{zusatz}.")
    return 0


def cmd_start(a):
    if not NAME_MUSTER.match(a.name):
        print(f"Unbrauchbarer Name {a.name!r} (Buchstaben, Ziffern, . _ @ -; kein Leerraum).",
              file=sys.stderr)
        return 2
    if not a.befehl:
        print('Kein Befehl angegeben — hinter "--" muss BEFEHL [ARG ...] folgen.', file=sys.stderr)
        return 2
    umwelt = []
    for paar in a.env:
        if "=" not in paar:
            print(f"--env {paar!r} ist nicht in der Form K=V.", file=sys.stderr)
            return 2
        umwelt.append(paar)
    ordner = os.path.abspath(a.ordner)
    if not os.path.isdir(ordner):
        print(f"--ordner {ordner} existiert nicht.", file=sys.stderr)
        return 2
    melde = None
    if a.melde_an:  # §17.12 Punkt 2: Vorprüfung vor JEDEM Seiteneffekt
        db = melde_db_pfad(a.teamkanal_db)
        fehler = melde_ziel_pruefen(a.melde_an, db)
        if fehler:
            return fehler
        # §17.13 Punkt 4: eigene Lauf-ID je start — Thema/Idempotenz der Meldung
        # hängen daran, damit gleiche Laufnamen getrennte Gespräche bekommen.
        melde = (a.name, a.melde_an, db, str(uuid.uuid4()))
    einheit = "lw-" + a.name

    alt = lade_zustand(a.name)
    if alt is not None and einheit_aktiv(einheit):
        print(f"{a.name} läuft bereits als {einheit} (Doppelstart); "
              f'Zustand ansehen: "laufwaechter.py status {a.name}".', file=sys.stderr)
        return 5
    if a.arbeitskopie:
        return start_mit_arbeitskopie(a, einheit, alt, umwelt, melde=melde)
    marke = relativ_aufgeloest(a.marke, ordner)
    log = relativ_aufgeloest(a.log, ordner)
    if os.path.exists(marke):
        print(f"Marke {marke} existiert schon — der Lauf würde sofort als fertig gelten. "
              "Erst die Marke löschen.", file=sys.stderr)
        return 6
    if alt is not None:
        print("alter Lauf beendet, ersetze Zustand.")

    warne_kontext_fehlt(ordner, a.marke)  # §15.1 — unmittelbar vor dem Start
    rueckgabe = starte_einheit(a.befehl, einheit, ordner, marke, log, umwelt, melde=melde)
    if rueckgabe != 0:
        print(f"systemd-run fehlgeschlagen (Exit {rueckgabe}); kein Zustand gespeichert.",
              file=sys.stderr)
        return 1

    zustand = {
        "name": a.name,
        "einheit": einheit,
        "ordner": ordner,
        "marke": marke,
        "log": log,
        "befehl": a.befehl,
        "gestartet": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if melde is not None:  # §17.12 Punkt 1: nur gesetzt, wenn --melde-an kam
        zustand["melde_an"] = melde[1]
        zustand["teamkanal_db"] = melde[2]
        zustand["melde_lauf"] = melde[3]  # §17.13 Punkt 4: Lauf-ID für die Hülle
    speichere_zustand(zustand)
    print(f"Gestartet als {einheit} — Log: {log}, Marke: {marke}.")
    return 0


# --- ergebnis -------------------------------------------------------------------

def cmd_ergebnis(a):
    z = lade_zustand(a.name)
    if z is None:
        print(f"Unbekannter Lauf {a.name!r}: keine Zustandsdatei {zustandspfad(a.name)}.",
              file=sys.stderr)
        return 2
    if not z.get("arbeitskopie"):
        print(f"{a.name} läuft ohne Arbeitskopie — \"ergebnis\" gibt es nur für Läufe mit "
              "--arbeitskopie.", file=sys.stderr)
        return 2
    if z.get("abgeschlossen"):
        print(f"{a.name} ist schon abgeschlossen ({z['abgeschlossen']}) — nichts mehr "
              "anzusehen.", file=sys.stderr)
        return 2
    if einheit_aktiv(z["einheit"]):
        print(f"Lauf läuft noch (Einheit {z['einheit']} ist aktiv) — "
              f'erst "laufwaechter.py warte {a.name}".', file=sys.stderr)
        return 9
    if not os.path.isdir(z["arbeitskopie"]):
        print(f"Arbeitskopie {z['arbeitskopie']} fehlt — der Zustand ist so unbrauchbar.",
              file=sys.stderr)
        return 2
    if not os.path.exists(z["marke"]):
        print("OHNE MARKE — Lauf gilt als abgebrochen. Der Stand wird trotzdem gesichert "
              "und angesehen.")
    repo, kopie, zweig, basis = z["repo"], z["arbeitskopie"], z["zweig"], z["basis"]
    verlinkt = z.get("verlinkt", ())
    try:
        arbeitskopie.sichern(kopie, z["name"], ausschliessen=verlinkt)  # §9.5 Punkt 3
    except arbeitskopie.FremderVerweis as fremd:  # §9.6 Punkt 2: nichts gesichert
        hinweis_fremde_verweise(fremd.funde, kopie)
        return 13
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler beim Sichern: {fehler}", file=sys.stderr)
        return 2
    try:
        zweig_sha = arbeitskopie.volle_sha(repo, zweig)
        # §9.5 Punkt 4: relativ zum Hauptzweig — Commits hauptzweig..zweig, Diffs
        # hauptzweig...zweig (seit der gemeinsamen Vorfahrin)
        commits = arbeitskopie.commits_seit(repo, z["hauptzweig"], zweig)
        statistik = arbeitskopie.diff_statistik(repo, z["hauptzweig"], zweig)
        namen = arbeitskopie.diff_name_status(repo, z["hauptzweig"], zweig)
        unterschied = arbeitskopie.diff_voll(repo, z["hauptzweig"], zweig)
        weiter = arbeitskopie.anzahl_commits(repo, basis, z["hauptzweig"])
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler beim Ansehen: {fehler}", file=sys.stderr)
        return 2
    dateien = [zeile for zeile in namen.splitlines() if zeile.strip()]
    print(f"Basis:     {basis[:8]} ({z['hauptzweig']} beim Start)")
    print(f"Commits {basis[:8]}..{zweig}: {len(commits)}")
    for zeile in commits:
        print("  " + zeile)
    if statistik.strip():
        print("Diff --stat:")
        print(statistik.rstrip())
    print(f"Geänderte Dateien: {len(dateien)}")
    for zeile in dateien:
        print("  " + zeile)
    diff_pfad = zustandspfad(a.name).with_suffix(".diff")
    diff_pfad.write_text(unterschied, encoding="utf-8")
    print(f"Vollständiger Diff: {diff_pfad}")
    if weiter:
        print(f"Hinweis: das Hauptrepo ({z['hauptzweig']}) ist seit der Basis um {weiter} "
              "Commit(s) weitergelaufen — die Übernahme holt ihn in die Arbeitskopie.")
    z["ergebnis_commit"] = zweig_sha
    speichere_zustand(z)
    return 0


# --- abschliessen ---------------------------------------------------------------

def cmd_abschliessen(a):
    if a.uebernehmen == a.verwerfen:
        print("Genau eins von --uebernehmen oder --verwerfen ist nötig.", file=sys.stderr)
        return 2
    if a.verwerfen and (a.gate or a.ohne_gate):
        print("--gate und --ohne-gate gehören nur zu --uebernehmen.", file=sys.stderr)
        return 2
    z = lade_zustand(a.name)
    if z is None:
        print(f"Unbekannter Lauf {a.name!r}: keine Zustandsdatei {zustandspfad(a.name)}.",
              file=sys.stderr)
        return 2
    if not z.get("arbeitskopie"):
        print(f"{a.name} läuft ohne Arbeitskopie — abschliessen gilt nur für Läufe mit "
              "--arbeitskopie.", file=sys.stderr)
        return 2
    if z.get("abgeschlossen"):
        print(f"{a.name} ist schon abgeschlossen ({z['abgeschlossen']}).", file=sys.stderr)
        return 2
    if einheit_aktiv(z["einheit"]):
        print(f"Lauf läuft noch (Einheit {z['einheit']} ist aktiv) — "
              f'erst "laufwaechter.py warte {a.name}".', file=sys.stderr)
        return 9
    if not os.path.isdir(z["arbeitskopie"]):
        print(f"Arbeitskopie {z['arbeitskopie']} fehlt — der Zustand ist so unbrauchbar.",
              file=sys.stderr)
        return 2
    repo, kopie, zweig, basis = z["repo"], z["arbeitskopie"], z["zweig"], z["basis"]

    if a.verwerfen:
        try:
            arbeitskopie.sichern(kopie, z["name"], ausschliessen=z.get("verlinkt", ()))
        except arbeitskopie.FremderVerweis as fremd:  # §9.6 Punkt 2: nichts löschen
            hinweis_fremde_verweise(fremd.funde, kopie)
            return 13
        except arbeitskopie.GitFehler as fehler:
            print(f"Verwerfen gescheitert: {fehler} — es ist nichts verloren, die "
                  "Arbeitskopie bleibt stehen.", file=sys.stderr)
            return 1
        try:
            arbeitskopie.verlinke_weg(kopie, z.get("verlinkt", ()))  # §9.5 Punkt 3
            arbeitskopie.arbeitskopie_entfernen(repo, kopie)
        except (arbeitskopie.GitFehler, OSError) as fehler:
            print(f"Verwerfen gescheitert: {fehler} — es ist nichts verloren, die "
                  "Arbeitskopie bleibt stehen.", file=sys.stderr)
            return 1
        z["ergebnis_commit"] = arbeitskopie.volle_sha(repo, zweig)
        z["abgeschlossen"] = "verworfen"
        speichere_zustand(z)
        print(f"Verworfen: Arbeitskopie {kopie} entfernt, der Stand war vorher gesichert.")
        print(f"Zweig {zweig} bleibt stehen — von Hand entfernen mit "
              f"{hinweis_zweig_loeschen(zweig)}.")
        return 0

    # --uebernehmen, in genau der Reihenfolge aus §9.1; jeder Abbruch lässt Hauptrepo
    # und Arbeitskopie stehen.
    verlinkt = z.get("verlinkt", ())
    try:
        kopie_unruhig = arbeitskopie.status_porcelain(
            kopie, ausschliessen=verlinkt)  # §9.5 Punkt 3: verlinkt zählt nie
        zweig_sha = arbeitskopie.volle_sha(repo, zweig)
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler: {fehler}", file=sys.stderr)
        return 2
    if not z.get("ergebnis_commit") or kopie_unruhig or zweig_sha != z["ergebnis_commit"]:
        print(f'Erst ansehen: "laufwaechter.py ergebnis {a.name}" — die Übernahme braucht '
              "ein angesehenes Ergebnis, und was nach dem Ansehen dazukam, wurde nicht "
              "gesehen.", file=sys.stderr)
        return 12
    try:
        # §9.6 Punkt 3 (Prüfung B): der zu übernehmende Baum selbst, gemessen an der
        # Wurzel der ARBEITSKOPIE — bewusst vor jeder weiteren Handlung, damit das
        # Hauptrepo unberührt bleibt und die Arbeitskopie steht. Kein Schalter stellt
        # diese Prüfung ab (§9.6 Punkt 5).
        fremde = arbeitskopie.fremde_verweise_im_zweig(kopie, zweig)
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler beim Prüfen des Zweigs auf fremde Verweise: {fehler}",
              file=sys.stderr)
        return 2
    if fremde:
        hinweis_fremde_verweise(fremde, kopie)
        return 13
    if bool(a.gate) == a.ohne_gate:
        print("Genau eins von --gate (mehrfach) oder --ohne-gate ist Pflicht — die Prüfung "
              "vor der Übernahme wird nicht übersprungen.", file=sys.stderr)
        return 2
    try:
        # §9.5 Punkt 5: nur versionierte Änderungen blockieren — würde der Merge eine
        # unversionierte Datei überschreiben, bricht Git selbst ab (dann Exit 11)
        haupt_unruhig = arbeitskopie.status_porcelain(repo, nur_versioniert=True)
        hauptzweig_heute = arbeitskopie.aktueller_zweig(repo)
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler: {fehler}", file=sys.stderr)
        return 2
    if haupt_unruhig or hauptzweig_heute != z["hauptzweig"]:
        grund = ("uncommittete (versionierte) Änderungen" if haupt_unruhig
                 else f"der Zweig ist {hauptzweig_heute}, nicht {z['hauptzweig']}")
        print(f"Hauptrepo nicht bereit für die Übernahme ({grund}) — nichts wird "
              "umgeschaltet.", file=sys.stderr)
        return 8
    try:
        if arbeitskopie.anzahl_commits(repo, basis, z["hauptzweig"]):
            arbeitskopie.hauptzweig_hineinmergen(kopie, z["hauptzweig"])
        # §9.5 Punkt 6: der Stand, den die Gates sehen, wird als Prüfstand gemerkt
        pruef_sha = arbeitskopie.volle_sha(repo, zweig)
    except arbeitskopie.GitFehler as fehler:
        konflikt = arbeitskopie.konfliktdateien(kopie)
        arbeitskopie.merge_abbruch(kopie)
        print(f"Konflikt beim Holen des neuen Hauptrepo-Stands in die Arbeitskopie "
              f"({fehler}) — abgebrochen, nichts übernommen.", file=sys.stderr)
        if konflikt:
            print("Konfliktdateien:", file=sys.stderr)
            for zeile in konflikt:
                print("  " + zeile, file=sys.stderr)
        return 11
    for gate_befehl in a.gate:
        rueckgabe = subprocess.run(["/bin/sh", "-c", gate_befehl], cwd=kopie).returncode
        if rueckgabe != 0:
            print(f"Gate rot: {gate_befehl} (Exit {rueckgabe}) — nichts übernommen.",
                  file=sys.stderr)
            return 10
    try:
        # §9.5 Punkt 6: Nachprüfung — Gates dürfen SHA und Arbeitsbaum unangetastet
        # lassen (unversionierte Dateien ausgenommen; verlinkt zählt nie, Punkt 3)
        geblieben = arbeitskopie.volle_sha(repo, zweig) == pruef_sha
        uebrig = arbeitskopie.status_porcelain(kopie, ausschliessen=verlinkt)
    except arbeitskopie.GitFehler as fehler:
        print(f"Git-Fehler: {fehler}", file=sys.stderr)
        return 2
    if not geblieben or not arbeitskopie.nur_unversioniert(uebrig):
        grund = ("der Zweig-SHA hat sich unter den Gates verschoben" if not geblieben
                 else "der Arbeitsbaum ist nicht mehr sauber: "
                      + "; ".join(uebrig[:UNCOMMITTET_ANZAHL]))
        print(f"Gate hat den geprüften Stand verändert ({grund}) — nichts übernommen; "
              "der Stand bleibt auf dem Zweig stehen.", file=sys.stderr)
        return 10
    try:
        arbeitskopie.im_hauptrepo_mergen(repo, zweig,
                                         f"Lauf {a.name} übernommen ({pruef_sha})")
    except arbeitskopie.GitFehler as fehler:
        arbeitskopie.merge_abbruch(repo)
        print(f"Konflikt beim Zusammenführen im Hauptrepo ({fehler}) — abgebrochen, "
              "nichts übernommen.", file=sys.stderr)
        return 11
    neuer_kopf = arbeitskopie.volle_sha(repo, "HEAD")
    try:
        arbeitskopie.raume_nach_uebernahme(repo, kopie, verlinkt)  # §9.5 Punkt 7
        arbeitskopie.zweig_loeschen_gemerged(repo, zweig)
    except (arbeitskopie.GitFehler, OSError) as fehler:
        print(f"WARNUNG: das Aufräumen schlug fehl ({fehler}) — die Übernahme im "
              "Hauptrepo ist trotzdem geschehen. Von Hand:", file=sys.stderr)
        for handgriff in hinweis_aufraeumen(zweig, kopie):
            print("  " + handgriff, file=sys.stderr)
    z["abgeschlossen"] = "uebernommen"
    speichere_zustand(z)
    print(f"Übernommen — neuer Stand des Hauptrepos ({z['hauptzweig']}): {neuer_kopf[:8]}")
    return 0


# --- warte ----------------------------------------------------------------------

def cmd_warte(a):
    z = lade_zustand(a.name)
    if z is None:
        print(f"Unbekannter Lauf {a.name!r}: keine Zustandsdatei {zustandspfad(a.name)}.",
              file=sys.stderr)
        return 2
    takt = float(os.environ.get("LAUFWAECHTER_TAKT") or 10)
    beginn = time.monotonic()
    while True:
        if os.path.exists(z["marke"]):
            print(f"FERTIG: Marke {z['marke']} vorhanden.")
            if einheit_aktiv(z["einheit"]):
                print(f"Hinweis: Einheit {z['einheit']} läuft noch — die Hülle schreibt "
                      "den Exit erst danach ins Log.")
            return 0
        if not einheit_aktiv(z["einheit"]):
            if os.path.exists(z["marke"]):  # Marke kam zwischen beiden Prüfungen, Einheit ist schon zu
                print(f"FERTIG: Marke {z['marke']} vorhanden.")
                return 0
            print(f"ABGEBROCHEN: Einheit {z['einheit']} ist nicht aktiv und keine Marke da.")
            print(f"Letzte {LOG_ZEILEN} Logzeilen ({z['log']}):")
            for zeile in letzte_logzeilen(z["log"]):
                print("  " + zeile)
            return 4
        still = stillstand_sekunden(z)
        if still >= a.stille_sekunden:
            print(f"HÄNGT VERMUTLICH: seit {int(still)} s keine Änderung unter {z['ordner']} "
                  f"(ohne .git/) und im Log. Einheit {z['einheit']} läuft weiter.")
            return 7
        if time.monotonic() - beginn >= a.max_sekunden:
            print(f"LÄUFT NOCH: {a.max_sekunden:g} s erreicht — "
                  f'erneut "laufwaechter.py warte {a.name}" aufrufen.')
            return 3
        time.sleep(takt)


# --- status ---------------------------------------------------------------------

def cmd_status(a):
    z = lade_zustand(a.name)
    if z is None:
        print(f"Unbekannter Lauf {a.name!r}: keine Zustandsdatei {zustandspfad(a.name)}.",
              file=sys.stderr)
        return 2
    aktiv = einheit_aktiv(z["einheit"])
    print(f"Lauf:      {z['name']}  (Einheit {z['einheit']})")
    print(f"Einheit:   {'aktiv' if aktiv else 'nicht aktiv'}")
    print(f"Marke:     {z['marke']} — {'da' if os.path.exists(z['marke']) else 'fehlt'}")
    print(f"Ordner:    {z['ordner']}")
    if z.get("arbeitskopie"):
        ergebnis_sha = z.get("ergebnis_commit")
        ergebnis_text = ("angesehen (" + ergebnis_sha[:8] + ")" if ergebnis_sha
                         else "noch nicht angesehen")
        print(f"Repo:      {z['repo']} (dort Zweig {z['hauptzweig']})")
        print(f"Arbeitskopie: {z['arbeitskopie']}")
        print(f"Zweig:     {z['zweig']} — Basis {z['basis'][:8]}")
        print(f"Ergebnis:  {ergebnis_text}")
        print(f"Abgeschlossen: {z['abgeschlossen'] or 'nein'}")
    print(f"Befehl:    {shlex.join(z['befehl'])}")
    print(f"Gestartet: {z['gestartet']}")
    print(f"Log:       {z['log']} — letzte {LOG_ZEILEN} Zeilen:")
    for zeile in letzte_logzeilen(z["log"]):
        print("  " + zeile)
    return 0


# --- melde ----------------------------------------------------------------------

def cmd_melde(a):
    """Fertigmeldung über den Teamkanal (§17.12, §17.13 Punkte 4–5); die Marke
    bleibt unangetastet.

    Mit --lauf (vom start über die Hülle, §17.13 Punkt 4) trägt das Thema die
    Lauf-ID — "Laufwächter: NAME (Lauf <erste 8 Zeichen>)" — und der
    Idempotenzschlüssel hängt an der Lauf-ID: je Lauf genau eine Nachricht in einem
    eigenen Gespräch, auch bei gleichen Laufnamen; nur ein offenes Gespräch mit
    genau diesem Thema und Empfänger wird wiederverwendet. Ohne --lauf (melde von
    Hand) gilt §17.12 unverändert: Thema "Laufwächter: NAME", Schlüssel am Namen.
    Die Ausgabe ist genau eine Zeile (gesendet, gespeichert oder fehlgeschlagen);
    der Unterbefehl läuft aus der Hülle heraus und hängt sich ans Lauf-Log an.
    """
    db = melde_db_pfad(a.db)
    status = "fertig" if os.path.exists(a.marke) else "abgebrochen"
    zahl = letzte_exit_zahl(a.log)
    text = (f"Laufwächter-Meldung: {a.name}\n"
            f"Status: {status}\n"
            f"Letzter Exit: {zahl}\n"
            f"Nächster Schritt: laufwaechter warte {a.name}\n"
            "Meldung des Laufwächters, keine Nutzerfreigabe; maßgeblich bleibt "
            "laufwaechter warte.")
    store = None
    try:
        if not os.path.exists(db):
            raise FileNotFoundError(f"Teamkanal-DB {db} existiert nicht")
        store = teamkanal.Store(db)
        ziel = store.status(a.an)["participant"]
        # Der lw-Teilnehmer hängt am Projekt des Empfängers und ist für alle Läufe
        # derselbe (§17.12 Punkt 3); idempotent über register.
        lw_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "laufwaechter:" + ziel["project"]))
        store.register(lw_id, "Laufwächter", "manual", ziel["project"])
        empf = str(uuid.UUID(a.an))
        # Mit --lauf: Thema trägt die Lauf-ID (§17.13 Punkt 4) — je Lauf ein
        # eigenes Gespräch; ohne --lauf bleibt das Thema von §17.12 unverändert.
        thema = f"Laufwächter: {a.name}" + (f" (Lauf {a.lauf[:8]})" if a.lauf else "")
        gespraech = None
        for c in store.status(lw_id)["conversations"]:
            # Nur ein OFFENES Gespräch mit genau diesem Thema und Empfänger wird
            # wiederverwendet — ein geschlossenes nie (§17.13 Punkt 4).
            if c["topic"] == thema and c["state"] == "open":
                anderer = c["b"] if c["a"] == lw_id else c["a"]
                if anderer == empf:
                    gespraech = c["id"]
                    break
        if gespraech is None:  # §17.12 Punkt 4: erstes mal anlegen, danach wiederverwenden
            gespraech = store.open(lw_id, empf, thema, 2)["id"]
        if a.lauf:  # §17.13 Punkt 4: Idempotenz je Lauf-ID, nicht je Laufname
            idempotenz = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lw:{a.lauf}:{status}"))
        else:
            idempotenz = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lw:{a.name}:{status}"))
        nachricht, _ = store.send(lw_id, gespraech, "note", text, idempotency=idempotenz)
        if ziel["kind"] == "codex":  # §17.12 Punkt 5: Zustellung an die Codex-Queue
            nachricht = store.deliver_codex(nachricht)
            if nachricht["delivery"] == "queue_failed":  # §17.13 Punkt 5: ehrliche Zeile
                print(f"LAUFWAECHTER-MELDUNG gespeichert {nachricht['id']}, "
                      f"Codex-Zustellung fehlgeschlagen: {nachricht['delivery_error']}")
                return 1
        print(f"LAUFWAECHTER-MELDUNG gesendet {nachricht['id']}")
        return 0
    except Exception as fehler:
        print(f"LAUFWAECHTER-MELDUNG fehlgeschlagen: {fehler}")
        return 1
    finally:
        if store is not None:
            try:
                store.close_db()
            except Exception:
                pass


def haupt(argv=None):
    p = argparse.ArgumentParser(
        prog="laufwaechter.py",
        description="Agentenläufe als systemd-Einheiten starten und überwachen "
                    "(Vertrag: INTERFACES.md §8.1 und §9).")
    u = p.add_subparsers(dest="befehl_art", required=True)

    s = u.add_parser("start", help="Lauf als Einheit lw-NAME starten")
    s.add_argument("name")
    s.add_argument("--ordner", required=True, help="Arbeitsordner des Laufs")
    s.add_argument("--marke", required=True,
                   help="Fertig-Marke; relativ zu --ordner verstanden")
    s.add_argument("--log", required=True, help="Logdatei; relativ zu --ordner verstanden")
    s.add_argument("--env", action="append", default=[], metavar="K=V",
                   help="Zusatz-Umgebung für den Lauf (mehrfach möglich)")
    s.add_argument("--arbeitskopie", action="store_true",
                   help="Lauf in eigener Arbeitskopie auf Zweig lw/NAME (Vertrag §9)")
    s.add_argument("--basis", help="Start-Revision der Arbeitskopie (Vorgabe: HEAD)")
    s.add_argument("--verlinke", action="append", default=[], metavar="PFAD",
                   help="Ignorierte Datei (Kopie) bzw. ignorierten Ordner (Verweis) aus "
                        "dem Hauptrepo in die Arbeitskopie legen (mehrfach möglich; "
                        "§9.5 Punkt 2)")
    s.add_argument("--trotz-uncommittet", action="store_true",
                   help="Start erlauben, obwohl das Hauptrepo uncommittete Änderungen hat")
    s.add_argument("--melde-an", metavar="UUID", default=None,
                   help="nach dem Laufende die Ergebniszeile (fertig/abgebrochen) an "
                        "diesen Teamkanal-Empfänger schicken (Vertrag §17.12)")
    s.add_argument("--teamkanal-db", metavar="PFAD", default=None,
                   help="Teamkanal-DB für --melde-an (Vorgabe: teamkanal.DEFAULT_DB; "
                        "~ wird über HOME aufgelöst)")

    w = u.add_parser("warte", help="auf Marke oder Ende der Einheit warten")
    w.add_argument("name")
    w.add_argument("--max-sekunden", type=float, default=540)
    w.add_argument("--stille-sekunden", type=float, default=1200)

    t = u.add_parser("status", help="Zustand des Laufs zeigen")
    t.add_argument("name")

    e = u.add_parser("ergebnis", help="Stand eines Arbeitskopie-Laufs sichern und ansehen")
    e.add_argument("name")

    b = u.add_parser("abschliessen",
                     help="Arbeitskopie übernehmen (mit Gate) oder verwerfen")
    b.add_argument("name")
    b.add_argument("--uebernehmen", action="store_true",
                   help="Zweig lw/NAME nach bestandenen Gates ins Hauptrepo zusammenführen")
    b.add_argument("--verwerfen", action="store_true",
                   help="Arbeitskopie sichern und entfernen; der Zweig bleibt stehen")
    b.add_argument("--gate", action="append", default=[], metavar="BEFEHL",
                   help="Prüfung in der Arbeitskopie (mehrfach möglich; läuft per /bin/sh -c)")
    b.add_argument("--ohne-gate", action="store_true",
                   help="bewusst ohne Gate übernehmen")

    m = u.add_parser("melde",
                     help="Fertigmeldung über den Teamkanal schicken (§17.12)")
    m.add_argument("--name", required=True, help="Laufname")
    m.add_argument("--marke", required=True,
                   help="Fertig-Marke des Laufs (ihr Vorhandensein bedeutet fertig)")
    m.add_argument("--log", required=True,
                   help="Lauf-Log; die letzte LAUFWAECHTER-EXIT-Zeile liefert die Zahl")
    m.add_argument("--an", required=True, metavar="UUID",
                   help="Teamkanal-Empfänger (claude oder codex)")
    m.add_argument("--db", default=None, metavar="PFAD",
                   help="Teamkanal-DB (Vorgabe: teamkanal.DEFAULT_DB)")
    m.add_argument("--lauf", default=None, metavar="ID",
                   help="Lauf-ID des start (§17.13 Punkt 4): Thema und Idempotenz "
                        "beziehen sich auf genau diesen Lauf — die Hülle setzt sie "
                        "von selbst; ohne --lauf gilt §17.12 unverändert")

    rest = list(sys.argv[1:] if argv is None else argv)
    befehl = []
    if rest and rest[0] == "start" and "--" in rest:
        # argparse verschluckt Optionen nach dem ersten Namenswort — beim start selbst
        # teilen; nur dort folgt ein Befehl
        i = rest.index("--")
        befehl = rest[i + 1:]
        rest = rest[:i]
    a = p.parse_args(rest)
    a.befehl = befehl
    return {"start": cmd_start, "warte": cmd_warte, "status": cmd_status,
            "ergebnis": cmd_ergebnis, "abschliessen": cmd_abschliessen,
            "melde": cmd_melde}[a.befehl_art](a)


if __name__ == "__main__":
    sys.exit(haupt())

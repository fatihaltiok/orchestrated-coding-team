"""Phase 4: Arbeitskopie — alle Git-Handgriffe für eigene Arbeitskopien je Agentenlauf
(Vertrag: INTERFACES.md §9).

Jeder Agentenlauf kann in einer eigenen Git-Arbeitskopie (git worktree) auf dem Zweig
lw/NAME arbeiten. Dieses Modul bündelt die Git-Handgriffe dazu; der Laufwächter
(laufwaechter.py) ruft sie auf und macht die Ausgaben. Scheitert ein Handgriff, wird
GitFehler geworfen. Lesende Aufrufe laufen mit GIT_OPTIONAL_LOCKS=0. Nur Standardbibliothek.

Im Hauptrepo wird hier nichts umgeschaltet und nichts verändert außer: Zweig lw/NAME und
die Worktree-Verwaltung unter .git/worktrees/ beim Anlegen, und beim Übernehmen der eine
Merge-Commit auf dem Arbeitszweig des Hauptrepos.
"""
import os
import pathlib
import shutil
import subprocess


class GitFehler(RuntimeError):
    """Ein Git-Aufruf ist fehlgeschlagen; Befehl, Exit und Ausgaben hängen an der Ausnahme."""

    def __init__(self, args, rueckgabe, ausgabe, fehlerausgabe):
        super().__init__(f"git {' '.join(args)} scheiterte (Exit {rueckgabe}): "
                         f"{(fehlerausgabe or ausgabe).strip()}")
        self.args = args
        self.rueckgabe = rueckgabe
        self.ausgabe = ausgabe
        self.fehlerausgabe = fehlerausgabe


class FremderVerweis(RuntimeError):
    """Ein Symlink in der Arbeitskopie zeigt aus ihr heraus (§9.6); die Fundliste
    (Paare PFAD -> ZIEL, Pfad relativ zur Arbeitskopie, Ziel wie im Symlink
    gespeichert) hängt an der Ausnahme."""

    def __init__(self, funde):
        super().__init__("fremde Verweise (§9.6): "
                         + "; ".join(f"{pfad} -> {ziel}" for pfad, ziel in funde))
        self.funde = list(funde)


AUTOR_NAME_VORGABE = "Orchestrated Team"
AUTOR_MAIL_VORGABE = "team@example.com"


def autor_umgebung():
    """GIT_AUTHOR_*/GIT_COMMITTER_* fuer die EIGENEN Commits des Laufwaechters (§9.7):
    Sicherungs-Commit und Merge-Commit tragen einen festen Autor, statt von der
    Konfiguration des jeweiligen Repos abzuhaengen — ein Repo ohne eigene Einstellung
    erbt sonst stillschweigend die globale. Ueberschreibbar ueber
    LAUFWAECHTER_AUTOR_NAME / LAUFWAECHTER_AUTOR_MAIL (fuer Tests und Fremdnutzung);
    ist nur eine gesetzt, gilt fuer die andere die Vorgabe.

    **Warum Umgebungsvariablen und nicht `-c user.email=…`:** Git liest den Autor in
    dieser Rangfolge GIT_AUTHOR_* > `-c user.*` > Repo-Konfiguration > global. Ein
    `-c` haette also still NICHT gegriffen, sobald GIT_AUTHOR_EMAIL in der Umgebung
    steht (Testlaeufe, CI, Wrapper-Skripte). Die erste Fassung dieser Funktion machte
    genau diesen Fehler; die Tests zu §9.7 haben ihn aufgedeckt."""
    name = os.environ.get("LAUFWAECHTER_AUTOR_NAME") or AUTOR_NAME_VORGABE
    mail = os.environ.get("LAUFWAECHTER_AUTOR_MAIL") or AUTOR_MAIL_VORGABE
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": mail,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": mail}


def git(repo, *args, lesend=False, pruefen=True, autor=False):
    """Führt `git -C REPO ARGS` aus; wirft GitFehler, wenn es scheitert (pruefen=True).
    autor=True setzt zusätzlich den festen Autor des Laufwächters (§9.7) — nur für
    seine zwei eigenen Commits, nie für die Arbeit des Agenten."""
    umgebung = dict(os.environ)
    if autor:
        umgebung.update(autor_umgebung())
    if lesend:
        umgebung["GIT_OPTIONAL_LOCKS"] = "0"  # bei reinem Lesen keine Sperrversuche
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=umgebung)
    if pruefen and r.returncode != 0:
        raise GitFehler(args, r.returncode, r.stdout, r.stderr)
    return r


def oberste_ebene(repo):
    """Realpath der obersten Repo-Ebene, oder None, wenn dort kein Git-Repo liegt."""
    r = git(repo, "rev-parse", "--show-toplevel", lesend=True, pruefen=False)
    if r.returncode != 0:
        return None
    return os.path.realpath(r.stdout.strip())


def aktueller_zweig(repo):
    """Name des aktuellen Zweigs; None bei detached HEAD."""
    name = git(repo, "rev-parse", "--abbrev-ref", "HEAD", lesend=True).stdout.strip()
    return None if name == "HEAD" else name


def status_porcelain(repo, ausschliessen=(), nur_versioniert=False):
    """Nicht committete oder neue Dateien als Porcelain-Zeilen (leere Liste = sauber).

    ausschliessen: relative Pfade, die nie erscheinen (§9.5 Punkt 3: die Einträge aus
    verlinkt zählen nie zur Arbeit). nur_versioniert: unversionierte Dateien weglassen
    (§9.5 Punkt 5: im Hauptrepo blockiert nur Versioniertes die Übernahme).
    """
    args = ["status", "--porcelain"]
    if nur_versioniert:
        args.append("--untracked-files=no")
    args += ["--", ".", *(f":(exclude){pfad}" for pfad in ausschliessen)]
    r = git(repo, *args, lesend=True)
    return [zeile for zeile in r.stdout.splitlines() if zeile.strip()]


def nur_unversioniert(zeilen):
    """True, wenn jede Porcelain-Zeile nur eine unversionierte Datei meldet („?? …“)."""
    return all(zeile.startswith("??") for zeile in zeilen)


def zweig_existiert(repo, zweig):
    r = git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + zweig,
            lesend=True, pruefen=False)
    return r.returncode == 0


def volle_sha(repo, revision):
    """Revision zur vollen Commit-SHA auflösen (HEAD, Zweigname, SHA …)."""
    return git(repo, "rev-parse", "--verify", revision + "^{commit}",
               lesend=True).stdout.strip()


def ist_vorfahre(repo, vorfahre, nachfahre):
    """True, wenn VORFAHRE Vorfahre von (oder gleich) NACHFAHRE ist
    (git merge-base --is-ancestor; §9.5 Punkt 1)."""
    r = git(repo, "merge-base", "--is-ancestor", vorfahre, nachfahre,
            lesend=True, pruefen=False)
    if r.returncode not in (0, 1):
        raise GitFehler(("merge-base", "--is-ancestor", vorfahre, nachfahre),
                        r.returncode, r.stdout, r.stderr)
    return r.returncode == 0


def arbeitskopien_zweig(laufname):
    """Der Zweigname eines Laufs: lw/NAME."""
    return "lw/" + laufname


def relativ_zu_repo(repo, angabe):
    """(absoluter Pfad, Pfad relativ zum Repo) für eine --verlinke-Angabe;
    ValueError, wenn die Angabe außerhalb des Repos liegt."""
    wurzel = pathlib.Path(repo).resolve()
    pfad = pathlib.Path(angabe)
    if not pfad.is_absolute():
        pfad = wurzel / pfad
    pfad = pathlib.Path(os.path.normpath(pfad))
    return pfad, pfad.relative_to(wurzel)


def baue_arbeitskopie(repo, zweig, pfad, basis):
    """`git worktree add -b ZWEIG PFAD BASIS` — neuer Zweig, als Arbeitskopie in PFAD."""
    git(repo, "worktree", "add", "-b", zweig, str(pfad), basis)


def ist_ignoriert(repo, pfad):
    """True, wenn Git PFAD (relativ zum Repo) ignoriert (git check-ignore -q)."""
    r = git(repo, "check-ignore", "-q", str(pfad), lesend=True, pruefen=False)
    if r.returncode not in (0, 1):
        raise GitFehler(("check-ignore", "-q", str(pfad)), r.returncode, r.stdout, r.stderr)
    return r.returncode == 0


def verlinke_erstellen(arbeitskopie_pfad, rel_pfad, ziel):
    """Symlink REL_PFAD (relativ zur Arbeitskopie) auf ZIEL (absolut) anlegen
    (§9.5 Punkt 2: nur für Ordner — Kopieren wäre zu teuer; Schreibzugriffe dort
    treffen das Hauptrepo, die start-Ausgabe warnt je Ordner davor)."""
    link = pathlib.Path(arbeitskopie_pfad) / rel_pfad
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.fspath(ziel), link)


def kopiere_datei(arbeitskopie_pfad, rel_pfad, ziel):
    """Datei ZIEL per shutil.copy2 als REL_PFAD in die Arbeitskopie kopieren
    (§9.5 Punkt 2: eine Kopie kann die Datei im Hauptrepo nicht verändern —
    der Durchgriff per Symlink traf sie unbemerkt, siehe Gutachten Befund 3)."""
    kopie = pathlib.Path(arbeitskopie_pfad) / rel_pfad
    kopie.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.fspath(ziel), kopie)


def verlinke_weg(arbeitskopie_pfad, verlinkt):
    """Entfernt genau die eigenen Einträge aus verlinkt (§9.5 Punkt 3 und 7): Symlink
    per os.unlink (nie das Ziel), kopierte Datei nur, wenn sie eine reguläre Datei ist.
    Alles andere bleibt liegen; OSError wandert zum Aufrufer."""
    for rel in verlinkt:
        ort = pathlib.Path(arbeitskopie_pfad) / rel
        if ort.is_symlink() or (ort.exists() and ort.is_file()):
            os.unlink(ort)


def arbeitskopie_entfernen(repo, pfad):
    """`git worktree remove PFAD` ohne Gewalt; ein unruhiger Baum lässt es scheitern."""
    git(repo, "worktree", "remove", str(pfad))


def arbeitskopie_gewaltsam_entfernen(repo, pfad):
    """`git worktree remove --force`. Der Quelltext-Test in tests/test_arbeitskopie.py
    erlaubt diesen Handgriff nur an zwei Stellen: raeume_gescheiterten_start_weg (§9.1)
    und raume_nach_uebernahme (§9.5 Punkt 7)."""
    git(repo, "worktree", "remove", "--force", str(pfad))


def zweig_loeschen_gemerged(repo, zweig):
    """Klein -d: Git löscht nur, was im Zweig des Hauptrepos aufgegangen ist."""
    git(repo, "branch", "-d", zweig)


def zweig_loeschen_erzwungen(repo, zweig):
    """Zweig endgültig löschen, auch mit ungemergter Arbeit darin."""
    git(repo, "branch", "-D", zweig)


def raeume_gescheiterten_start_weg(repo, zweig, pfad):
    """Nur hier — als Aufräumen des eigenen fehlgeschlagenen Starts (Vertrag §9.1) — darf
    das Werkzeug eine Arbeitskopie mit Gewalt entfernen (worktree remove --force) und den
    Zweig endgültig löschen (branch -D). Die Arbeitskopie steckt zu diesem Zeitpunkt noch
    in keiner eigenen Arbeit des Agenten. Gibt die aufgetretenen Fehler zurück."""
    fehler = []
    try:
        arbeitskopie_gewaltsam_entfernen(repo, pfad)
    except GitFehler as einzeln:
        fehler.append(str(einzeln))
    try:
        zweig_loeschen_erzwungen(repo, zweig)
    except GitFehler as einzeln:
        fehler.append(str(einzeln))
    return fehler


def raume_nach_uebernahme(repo, pfad, verlinkt):
    """Aufräumen nach der Übernahme (§9.5 Punkt 7): eigene verlinkt-Einträge entfernen;
    liegen in der Arbeitskopie danach nur noch unversionierte Dateien, stammen sie
    nachweislich von den Gates (vor den Gates war der Baum sauber, §9.5 Punkt 6) —
    nur in diesem Fall darf mit Gewalt entfernt werden, sonst ohne. Wirft GitFehler
    oder OSError, wenn es scheitert; der Aufrufer warnt dann mit genauen Handgriffen."""
    verlinke_weg(pfad, verlinkt)
    uebrig = status_porcelain(pfad)
    if uebrig and nur_unversioniert(uebrig):
        arbeitskopie_gewaltsam_entfernen(repo, pfad)
    else:
        arbeitskopie_entfernen(repo, pfad)


def ziel_fremd(wurzel, ort, ziel):
    """§9.6 Punkt 1: Beurteilt ein Symlink-Ziel, OHNE ihm zu folgen — der Zielpfad
    wird (relativ zum Ort des Symlinks) nur normalisiert (os.path.normpath) und dann
    danach gefragt, ob er unterhalb der Wurzel liegt. (resolve/realpath auf dem
    Symlink selbst wäre falsch: sie laufen ins Ziel und reagieren bei einem Verweis
    ins Leere anders.) Fremd heißt: der Ziel-Ort liegt nicht unterhalb der Wurzel."""
    if not os.path.isabs(ziel):
        ziel = os.path.join(ort, ziel)
    ziel = os.path.normpath(ziel)
    if ziel == wurzel:
        return False
    return os.path.commonpath([wurzel, ziel]) != wurzel


def symlinks_im_baum(repo, revision):
    """(Pfad, Ziel) für jeden Symlink-Eintrag in REVISIONs Baum: `git ls-tree -r`,
    jeder Eintrag mit Modus 120000, sein Ziel ist der Blob-Inhalt (§9.6 Punkt 3)."""
    r = git(repo, "ls-tree", "-r", revision, lesend=True)
    verweise = []
    for zeile in r.stdout.splitlines():
        if not zeile:
            continue
        metadaten, pfad = zeile.split("\t", 1)
        modus, _art, inhalt = metadaten.split()
        if modus != "120000":
            continue
        ziel = git(repo, "cat-file", "blob", inhalt, lesend=True).stdout.rstrip("\n")
        verweise.append((pfad, ziel))
    return verweise


def fremde_verweise(arbeitskopie_pfad, ausschliessen=()):
    """Fremde Verweise im Arbeitsbaum (§9.6 Punkt 1 und 2): Symlinks, deren
    normalisiertes Ziel nicht unterhalb der Wurzel der Arbeitskopie liegt — ohne
    `.git/`, und beim Durchlaufen wird keinem Symlink gefolgt
    (os.walk(..., followlinks=False), das ist die Vorgabe). Die eigenen
    ausschliessen-Einträge (verlinkt, §9.5 Punkt 3) sind angemeldet und zählen nie.
    Schon unverändert im Zweig (HEAD) steckende Verweise überspringt die Prüfung:
    sie liegen in der Geschichte des Zweigs, wo Prüfung A sie nicht mehr sieht (§9.6
    Punkt 3) — Prüfung B (fremde_verweise_im_zweig) fängt sie vor dem Zusammenführen."""
    wurzel = os.path.realpath(arbeitskopie_pfad)
    angemeldet = {os.path.normpath(str(pfad)) for pfad in ausschliessen}
    funde = []
    for ort, unterordner, dateien in os.walk(wurzel, followlinks=False):
        unterordner[:] = sorted(name for name in unterordner if name != ".git")
        for name in sorted(dateien) + unterordner:
            voll = os.path.join(ort, name)
            if not os.path.islink(voll):
                continue
            rel = os.path.normpath(os.path.relpath(voll, wurzel))
            if rel in angemeldet:
                continue
            ziel = os.readlink(voll)
            if ziel_fremd(wurzel, ort, ziel):
                funde.append((rel, ziel))
    if funde:
        try:
            bereits_gesichert = set(symlinks_im_baum(arbeitskopie_pfad, "HEAD"))
        except GitFehler:
            bereits_gesichert = set()  # ohne lesbare Geschichte zählt alles als ungesichert
        funde = [paar for paar in funde if paar not in bereits_gesichert]
    return funde


def fremde_verweise_im_zweig(repo, zweig):
    """Fremde Verweise im zu übernehmenden Baum (§9.6 Punkt 3, Prüfung B): jeder
    ls-tree-Eintrag mit Modus 120000, beurteilt nach derselben Regel wie Punkt 1,
    gemessen relativ zum Ort des Eintrags im Baum. REPO ist der Pfad der
    ARBEITSKOPIE (git liest den Zweig aus dem Worktree) — die Wurzel, an der das
    Ziel gemessen wird, ist die der Arbeitskopie, denn ihr Baum ist es, der
    übernommen wird; ein Ziel im Hauptrepo liegt außerhalb und ist fremd. Deckt den
    Fall ab, dass der Agent den Verweis selbst committet hat — dann liegt er in der
    Geschichte des Zweigs, wo Prüfung A ihn nicht mehr sieht."""
    wurzel = os.path.realpath(repo)
    funde = []
    for pfad, ziel in symlinks_im_baum(repo, zweig):
        ort = os.path.join(wurzel, os.path.dirname(pfad))
        if ziel_fremd(wurzel, ort, ziel):
            funde.append((pfad, ziel))
    return funde


def sichern(repo, laufname, ausschliessen=()):
    """Alles Nicht-Committete/Neue mit `git add -A` aufnehmen und einen Commit anfügen
    (--no-verify, Nachricht ohne jeden Trailer; Autor ist die Git-Konfiguration).
    ausschliessen: Pfade, die nie zur Arbeit gehören (§9.5 Punkt 3: die Einträge aus
    verlinkt — ein Symlink erfüllt ein .gitignore-Muster wie `.venv/` nicht, weil er
    kein Ordner ist, und würde sonst committet und ins Hauptrepo übernommen).
    Wirft FremderVerweis (§9.6 Punkt 2), wenn der Baum fremde Verweise enthält — dann
    wird nichts gestagt und nichts committet.
    Gibt die SHA des neuen Commits zurück, None, wenn es nichts zu sichern gab."""
    funde = fremde_verweise(repo, ausschliessen=ausschliessen)  # §9.6 Punkt 2: zuerst
    if funde:
        raise FremderVerweis(funde)
    if not status_porcelain(repo, ausschliessen=ausschliessen):
        return None
    git(repo, "add", "-A")
    for pfad in ausschliessen:
        # `git add` mit Ausschluss-Pfadspec lehnt ignorierte Dateien als "explizit
        # verlangt" ab (Exit 1, nichts wird gestagt); deshalb ganze Stagung und dann
        # die ausgeschlossenen Pfade aus dem Index zurücknehmen (--cached: nie der
        # Arbeitsbaum). War ein Pfad nie im Index (ignorierte Datei), schweigt das.
        git(repo, "rm", "--cached", "-r", "--quiet", "--", str(pfad), pruefen=False)
    git(repo, "commit", "--no-verify", autor=True,  # §9.7: fester Autor
        *["-m", f"laufwaechter: Stand von {laufname} gesichert"])
    return git(repo, "rev-parse", "HEAD", lesend=True).stdout.strip()


def commits_seit(repo, basis, zweig):
    """Commits basis..zweig, je Zeile 'kurze-SHA Betreff' (neuester zuerst)."""
    r = git(repo, "log", "--oneline", f"{basis}..{zweig}", lesend=True)
    return [zeile for zeile in r.stdout.splitlines() if zeile.strip()]


def anzahl_commits(repo, von, bis):
    r = git(repo, "rev-list", "--count", f"{von}..{bis}", lesend=True)
    return int(r.stdout.strip() or "0")


def diff_statistik(repo, von, bis):
    """Diff --stat seit der gemeinsamen Vorfahrin (`von...bis`, §9.5 Punkt 4) — so
    erscheint nach einem hereingeholten Hauptstand nicht dessen Arbeit als Agentenarbeit."""
    return git(repo, "diff", "--stat", f"{von}...{bis}", lesend=True).stdout


def diff_name_status(repo, von, bis):
    """Diff --name-status seit der gemeinsamen Vorfahrin (`von...bis`, §9.5 Punkt 4)."""
    return git(repo, "diff", "--name-status", f"{von}...{bis}", lesend=True).stdout


def diff_voll(repo, von, bis):
    """Der vollständige Diff seit der gemeinsamen Vorfahrin (`von...bis`, §9.5 Punkt 4;
    enthält neue Dateien, weil gesichert)."""
    return git(repo, "diff", f"{von}...{bis}", lesend=True).stdout


def hauptzweig_hineinmergen(arbeitskopie_pfad, hauptzweig):
    """`git merge --no-edit <hauptzweig>` in der Arbeitskopie (holt den neuen Stand herein)."""
    git(arbeitskopie_pfad, "merge", "--no-edit", hauptzweig)


def im_hauptrepo_mergen(repo, zweig, nachricht):
    """`git merge --no-ff --no-edit` — der eine Schreibgriff im Hauptrepo (§9)."""
    git(repo, "merge", "--no-ff", "--no-edit", "-m", nachricht, zweig,
        autor=True)  # §9.7: fester Autor


def merge_abbruch(repo):
    """`git merge --abort`; gibt es nichts mehr abzubrechen, wird der Fehler still ignoriert."""
    git(repo, "merge", "--abort", pruefen=False)


def konfliktdateien(repo):
    """Dateien mit unentschiedenem Zusammenführen (leer, wenn es ohne Konflikt lief)."""
    r = git(repo, "diff", "--name-only", "--diff-filter=U", lesend=True, pruefen=False)
    return [zeile for zeile in r.stdout.splitlines() if zeile.strip()]

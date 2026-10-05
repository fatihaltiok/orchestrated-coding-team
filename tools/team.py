#!/usr/bin/env python3
# Kurzbefehl "team PROJEKT" (01.10.2026). Hülle ~/.local/bin/team ruft diese Datei auf; Anleitung docs/TEAMKANAL.md "Kurzstart"
# team            -> zeigt die Projekte
# team PROJEKT    -> öffnet ein Claude-Fenster mit Teamkanal für dieses Projekt (aus ~/projects gestartet, Merkzettel)
import os
import sys

sys.dont_write_bytecode = True
ORTE = [os.path.expanduser(ort) for ort in
        os.environ.get("TEAM_PROJEKTORDNER", "~/projects").split(os.pathsep) if ort]
projekte = {}
for ort in ORTE:  # jedes Unterverzeichnis mit eigenem Git-Repo oder PROGRESS/ARBEITSWEISE ist ein Projekt
    if os.path.isdir(ort):
        for name in os.listdir(ort):
            pfad = os.path.join(ort, name)
            if not name.startswith(".") and os.path.isdir(pfad) and any(
                    os.path.exists(os.path.join(pfad, f)) for f in (".git", "PROGRESS.md", "ARBEITSWEISE.md")):
                projekte.setdefault(name, pfad)

args = sys.argv[1:]
if not args or args[0] in ("-h", "--help"):
    print("Aufruf: team PROJEKT   (öffnet ein Claude-Fenster mit Teamkanal)\nProjekte:")
    for name in sorted(projekte, key=str.lower):
        print("  " + name)
    sys.exit(0)

wunsch = args[0].lower()
treffer = ([n for n in projekte if n.lower() == wunsch]
           or [n for n in projekte if n.lower().startswith(wunsch)]
           or [n for n in projekte if wunsch in n.lower()])
if len(treffer) != 1:
    print(("Mehrdeutig: " + ", ".join(sorted(treffer))) if treffer
          else f"Kein Projekt passt zu {args[0]!r}. Liste: team", file=sys.stderr)
    sys.exit(2)

pfad = projekte[treffer[0]]
# Merkzettel hängt am Startordner: optional eigener Startordner je Projekt
start = pfad if treffer[0] == os.environ.get("TEAM_EIGENES_STARTPROJEKT") else ORTE[0]
os.chdir(start)
print(f"Teamkanal-Fenster für {treffer[0]} ({pfad}), gestartet aus {start}", file=sys.stderr)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.execv(sys.executable, [sys.executable, os.path.join(os.path.dirname(__file__),
                 "teamkanal_start.py"), "claude", "--projekt", pfad, *args[1:]])

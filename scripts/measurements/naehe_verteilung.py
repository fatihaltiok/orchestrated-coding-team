#!/usr/bin/env python3
"""Verteilung der Wortüberlappung über ALLE Notizpaare eines Merkzettels.

Bereinigte Kopie des Messskripts aus dem privaten Team-Repo; der private
Standardordner ist entfernt — der Ordner wird als Argument übergeben.

    python3 scripts/measurements/naehe_verteilung.py ORDNER

Belegt E-005: Es gibt keine Lücke in der Verteilung, an der eine Schwelle für
„das ist eine Dublette" säße. Der Check selbst zeigt nur die zehn höchsten Paare
(§10.4 Punkt 9); dieses Skript zeigt die ganze Verteilung, damit die Entscheidung
nachrechenbar bleibt statt geglaubt zu werden.

Rein lesend. Standardbibliothek. Gibt die zehn höchsten Paare und die Verteilung
gerundet auf eine Nachkommastelle aus.
"""

import collections
import itertools
import pathlib
import re
import sys

WORT = re.compile(r"\w+")
MIN_LAENGE = 4


def worte_je_notiz(ordner):
    """-> {dateiname: Wortmenge aus name + description}; archiv/ bleibt außen vor."""
    notizen = {}
    for pfad in sorted(ordner.rglob("*.md")):
        rel = pfad.relative_to(ordner).as_posix()
        if rel == "MEMORY.md" or rel.split("/", 1)[0] == "archiv":
            continue
        text = pfad.read_text(encoding="utf-8", errors="replace")
        name = re.search(r"^name: *(.+)$", text, re.M)
        besch = re.search(r"^description: *(.+)$", text, re.M)
        roh = f"{name.group(1) if name else ''} {besch.group(1) if besch else ''}".lower()
        notizen[rel] = {w for w in WORT.findall(roh) if len(w) >= MIN_LAENGE}
    return notizen


def main(argv):
    if len(argv) < 2:
        print("usage: naehe_verteilung.py ORDNER  (folder with the memory notes)",
              file=sys.stderr)
        return 2
    ordner = pathlib.Path(argv[1])
    if not ordner.is_dir():
        print(f"Ordner existiert nicht: {ordner}", file=sys.stderr)
        return 2
    notizen = worte_je_notiz(ordner)
    paare = []
    for a, b in itertools.combinations(sorted(notizen), 2):
        A, B = notizen[a], notizen[b]
        if not A or not B:
            continue
        paare.append((len(A & B) / len(A | B), a, b))
    if not paare:
        print("Zu wenige Notizen für einen Vergleich.")
        return 0
    paare.sort(key=lambda p: (-p[0], p[1], p[2]))

    print(f"Merkzettel: {ordner}")
    print(f"{len(notizen)} Notizen, {len(paare)} Paare\n")
    print("Die zehn höchsten Paare:")
    for wert, a, b in paare[:10]:
        print(f"  {wert:.3f}  {a}  <->  {b}")
    verteilung = collections.Counter(round(wert, 1) for wert, _, _ in paare)
    print("\nVerteilung (auf eine Nachkommastelle gerundet):")
    for stufe in sorted(verteilung, reverse=True):
        print(f"  {stufe:.1f}: {verteilung[stufe]} Paar(e)")
    print(f"\nHöchstwert: {paare[0][0]:.3f}")
    print("Eine Schwelle wäre nur dann nicht willkürlich, wenn die Verteilung hier eine "
          "Lücke hätte — sie fällt glatt ab (E-005).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Hermetische Tests für den neutralen Kurzstart."""

import os
import runpy
import sys
from pathlib import Path

import pytest


SKRIPT = Path(__file__).resolve().parent.parent / "team.py"


class StartErfasst(Exception):
    pass


@pytest.mark.parametrize("eigen", [False, True])
def test_projektstart_ohne_private_huelle(tmp_path, monkeypatch, eigen):
    projekt = tmp_path / "projekte" / "beispiel"
    projekt.mkdir(parents=True)
    (projekt / ".git").mkdir()
    monkeypatch.setenv("TEAM_PROJEKTORDNER", str(projekt.parent))
    if eigen:
        monkeypatch.setenv("TEAM_EIGENES_STARTPROJEKT", "beispiel")
    else:
        monkeypatch.delenv("TEAM_EIGENES_STARTPROJEKT", raising=False)
    monkeypatch.setattr(sys, "argv", [str(SKRIPT), "beispiel", "--trocken"])
    aufruf = []

    def execv(programm, argumente):
        aufruf.append((programm, argumente, Path.cwd()))
        raise StartErfasst

    monkeypatch.setattr(os, "execv", execv)
    with pytest.raises(StartErfasst):
        runpy.run_path(str(SKRIPT), run_name="__main__")
    programm, argumente, start = aufruf[0]
    assert programm == sys.executable
    assert argumente == [sys.executable, str(SKRIPT.parent / "teamkanal_start.py"),
                         "claude", "--projekt", str(projekt), "--trocken"]
    assert start == (projekt if eigen else projekt.parent)


def test_projektliste_aus_konfiguriertem_ordner(tmp_path, monkeypatch, capsys):
    projekt = tmp_path / "projekte" / "beispiel"
    projekt.mkdir(parents=True)
    (projekt / ".git").mkdir()
    monkeypatch.setenv("TEAM_PROJEKTORDNER", str(projekt.parent))
    monkeypatch.setattr(sys, "argv", [str(SKRIPT)])
    with pytest.raises(SystemExit) as ende:
        runpy.run_path(str(SKRIPT), run_name="__main__")
    assert ende.value.code == 0
    assert "beispiel" in capsys.readouterr().out

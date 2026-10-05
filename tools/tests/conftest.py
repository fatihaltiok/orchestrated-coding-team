"""Gemeinsame Sicherheitsschranke: Tests dürfen Jev nie versehentlich live rufen."""

import json
import socket
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def _offline_jev_suite(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def verboten(*_args, **_kwargs):
        raise AssertionError("Netzwerkzugriff in werkzeuge/tests ist verboten")

    monkeypatch.setattr(socket.socket, "connect", verboten)
    monkeypatch.setattr(urllib.request, "urlopen", verboten)


@pytest.fixture(autouse=True)
def _merkzettel_wegwerf(monkeypatch, tmp_path):
    """§16.1 Punkt 9: KONTEXTPAKET_MERKZETTEL zeigt in jedem Test auf einen
    nicht existierenden Pfad — kein bau-Test liest den echten Merkzettel
    und bestehende Tests bleiben deterministisch."""
    monkeypatch.setenv("KONTEXTPAKET_MERKZETTEL",
                       str(tmp_path / "kein-merkzettel-vorhanden"))


@pytest.fixture(scope="session")
def regelquelle(tmp_path_factory):
    quelle = tmp_path_factory.mktemp("regeln-quelle")
    text = ("# Neutrale Testregeln\n"
            "Codex-Subagenten arbeiten mit Beispielen.\n"
            "Claude-Agenten prüfen lokal.\n"
            "Quelle bleibt unverändert.\n"
            "Vier Vertragsstellen.\n"
            + "".join(f"Testzeile {nr}\n" for nr in range(50)))
    (quelle / "CLAUDE.md").write_text(text, encoding="utf-8")
    (quelle / "ersetzungen-codex.json").write_text(json.dumps([
        {"alt": alt, "neu": neu, "anzahl": 1} for alt, neu in (
            ("Codex-Subagenten", "Codex-Tester"),
            ("Claude-Agenten", "Claude-Tester"),
            ("Quelle bleibt", "Vorlage bleibt"),
            ("Vier Vertragsstellen", "Vier Teststellen"),
        )
    ]), encoding="utf-8")
    installiert = quelle / "installiert"
    installiert.mkdir()
    (installiert / "CLAUDE.md").write_text(text, encoding="utf-8")
    return quelle

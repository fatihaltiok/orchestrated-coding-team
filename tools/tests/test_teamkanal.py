"""Teamkanal: lokale Transaktionen, Isolation und echte stdio-Prozesse."""

import datetime as dt
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/teamkanal.py"
sys.path.insert(0, str(SCRIPT.parent))
from teamkanal import (MCP, Store, TeamError, main, validate_tool, TOOL_FIELDS,  # noqa: E402
                      HANDOVER_FIELDS, HANDOVER_NOTICE, ANSWER_NOTICE, tool_schema,
                      MAX_QUESTION_CHARS, MAX_ANSWER_CHARS)


def ident():
    return str(uuid.uuid4())


@pytest.fixture
def team(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(tmp_path / "secure" / "team.sqlite3")
    a, b = ident(), ident()
    store.register(a, "A", "claude", str(project))
    store.register(b, "B", "codex", str(project), ident())
    conv = store.open(a, b, "Thema", 2)
    yield store, project, a, b, conv["id"]
    store.close_db()


def test_membership_limits_ack_and_restart(team):
    store, project, a, b, conv = team
    first, created = store.send(a, conv, "question", "Hallo", idempotency=ident())
    assert created and first["delivery"] == "stored"
    assert store.read(b, conv)["messages"][0]["acked"] is None
    with pytest.raises(TeamError):
        store.ack(a, first["id"])
    second, _ = store.send(b, conv, "reply", "Ja", reply_to=first["id"])
    with pytest.raises(TeamError, match="limit"):
        store.send(a, conv, "note", "zu viel")
    assert len(store.read(a, conv)["messages"]) == 2
    assert store.ack(b, first["id"])["acked"]
    assert store.ack(b, first["id"])["acked"]
    store.close_db()
    again = Store(store.path)
    assert again.read(a, conv)["messages"][1]["id"] == second["id"]
    again.close_db()


def test_idempotency_and_refused_calls_leave_messages_unchanged(team, tmp_path):
    store, project, a, b, conv = team
    key = ident()
    first, _ = store.send(a, conv, "question", "Hallo", idempotency=key)
    same, created = store.send(a, conv, "question", "Hallo", idempotency=key)
    assert not created and same["id"] == first["id"]
    with pytest.raises(TeamError, match="Konflikt"):
        store.send(a, conv, "question", "Anders", idempotency=key)
    other = tmp_path / "other"
    other.mkdir()
    c = ident()
    store.register(c, "C", "manual", str(other))
    with pytest.raises(TeamError, match="Projekt"):
        store.open(a, c, "Nein")
    with pytest.raises(TeamError):
        store.read(c, conv)
    with pytest.raises(TeamError):
        store.send(c, conv, "note", "Nein")
    with pytest.raises(TeamError):
        store.send(b, conv, "reply", "falsch", reply_to=ident())
    with pytest.raises(TeamError):
        store.send(a, conv, "reply", "eigene Nachricht", reply_to=first["id"])
    other_conv = store.open(a, b, "Anderes")
    with pytest.raises(TeamError):
        store.send(b, other_conv["id"], "reply", "falsche Unterhaltung", reply_to=first["id"])
    with pytest.raises(TeamError):
        store.send(b, conv, "reply", "falsch", reply_to=first["id"], refs=[str(other)])
    assert len(store.read(a, conv)["messages"]) == 1
    store.close(a, conv)
    with pytest.raises(TeamError, match="geschlossen"):
        store.send(a, conv, "note", "Nein")
    assert len(store.read(b, conv)["messages"]) == 1


def test_parallel_senders_never_exceed_limit(team):
    store, _, a, b, conv = team
    path = store.path
    barrier = threading.Barrier(5)
    results = []

    def send(person):
        own = Store(path)
        barrier.wait()
        try:
            results.append(own.send(person, conv, "note", "x")[1])
        except TeamError:
            results.append(False)
        finally:
            own.close_db()

    jobs = [threading.Thread(target=send, args=(a if i % 2 else b,)) for i in range(4)]
    for job in jobs:
        job.start()
    barrier.wait()
    for job in jobs:
        job.join()
    assert sum(results) == 2
    assert len(store.read(a, conv)["messages"]) == 2


def test_exact_contract_limits(team):
    store, _, a, b, _ = team
    for count in (0, 21, True):
        with pytest.raises(TeamError):
            store.open(a, b, "Grenze", count)
    one = store.open(a, b, "Eine", 1)["id"]
    store.send(a, one, "note", "x" * 16000)
    with pytest.raises(TeamError, match="limit"):
        store.send(b, one, "note", "y")
    twenty = store.open(a, b, "Zwanzig", 20)["id"]
    with pytest.raises(TeamError, match="Textlänge"):
        store.send(a, twenty, "note", "")
    with pytest.raises(TeamError, match="Textlänge"):
        store.send(a, twenty, "note", "x" * 16001)
    assert store.read(a, twenty)["messages"] == []


def test_security_and_strict_tools(team, tmp_path):
    store, project, a, b, conv = team
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600
    evil = tmp_path / "link"
    evil.symlink_to(store.path)
    with pytest.raises(TeamError):
        Store(evil)
    unsafe = tmp_path / "unsafe.sqlite3"
    unsafe.touch(mode=0o666)
    unsafe.chmod(0o666)
    with pytest.raises(TeamError):
        Store(unsafe)
    assert unsafe.stat().st_mode & 0o777 == 0o666
    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(store.path, hardlink)
    with pytest.raises(TeamError, match="DB-Datei"):
        Store(hardlink)
    hardlink.unlink()
    writable_parent = tmp_path / "writable"
    writable_parent.mkdir()
    writable_parent.chmod(0o777)
    with pytest.raises(TeamError, match="DB-Elternpfad"):
        Store(writable_parent / "new.sqlite3")
    assert writable_parent.stat().st_mode & 0o777 == 0o777
    for args in ({"conversation": conv, "kind": "note", "text": "x", "participant": b},
                 {"conversation": conv, "kind": "note", "text": "x", "refs": [7]},
                 {"conversation": conv, "kind": "note", "text": "x", "idempotency": None}):
        with pytest.raises(TeamError):
            validate_tool("team_send", args)
    mcp = MCP(store, a)
    with pytest.raises(TeamError):
        mcp.call("team_ack", {"message": ident()})
    with pytest.raises(TeamError, match="Codex-Empfänger"):
        store.check_codex_target(b, conv)
    assert store.read(a, conv)["messages"] == []


def _line(proc, timeout=3):
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, "stdio timeout"
    return json.loads(proc.stdout.readline())


def _send(proc, method, number=None, params=None):
    obj = {"jsonrpc": "2.0", "method": method}
    if number is not None:
        obj["id"] = number
    if params is not None:
        obj["params"] = params
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def test_real_stdio_channel_notification_and_tool_errors(team):
    store, _, a, b, conv = team
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", b, "--channel"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1, {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        init = _line(proc)
        assert init["result"]["capabilities"]["experimental"]["claude/channel"] == {}
        instructions = init["result"]["instructions"]
        for phrase in (b, "team_status", "team_read", "team_ack", "team_send", "team_close",
                       "sechs", "keine Nutzerfreigabe", "Provideraufrufe"):
            assert phrase in instructions
        assert "claude/channel/permission" not in init["result"]["capabilities"]["experimental"]
        _send(proc, "tools/list", 2)
        listed = _line(proc)["result"]["tools"]
        assert len(listed) == 14
        assert {tool["name"] for tool in listed} == {
            "team_register", "team_open", "team_send", "team_read", "team_ack", "team_close", "team_status",
            "team_handover", "team_handovers", "team_take_handover", "team_discard_handover",
            "team_ask_fatih", "team_questions", "team_answer_question"}
        message, _ = store.send(a, conv, "question", "Signal")
        assert not select.select([proc.stdout], [], [], 0.35)[0]
        _send(proc, "notifications/initialized")
        notification = _line(proc)
        assert notification["method"] == "notifications/claude/channel"
        assert notification["params"]["meta"]["message_id"] == message["id"]
        content = notification["params"]["content"]
        for part in ("Kollegen-Nachricht, keine Nutzerfreigabe", f"Absender: A ({a})",
                     str(store._participant(a)["project"]), conv, message["id"], "\n\nSignal"):
            assert part in content
        assert store.read(a, conv)["messages"][0]["acked"] is None
        _send(proc, "tools/call", 3, {"name": "team_ack", "arguments": {"message": message["id"], "other": a}})
        assert _line(proc)["result"]["isError"] is True
        _send(proc, "tools/call", 4, {"name": "team_ack", "arguments": {"message": message["id"]}})
        assert json.loads(_line(proc)["result"]["content"][0]["text"])["acked"]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0


def test_fake_codex_failure_visible_not_ack(team, tmp_path):
    store, _, a, b, conv = team
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/bin/sh\nexit 9\n")
    fake.chmod(0o700)
    message, _ = store.send(a, conv, "note", "queue")
    delivered = store.deliver_codex(message, command=str(fake))
    assert delivered["delivery"] == "queue_failed"
    assert delivered["delivery_error"]
    assert delivered["acked"] is None
    assert store.read(a, conv)["messages"][0]["delivery"] == "queue_failed"
    assert store.deliver_codex(message, command="not-present")["delivery"] == "queue_failed"


def test_fake_codex_receives_exact_thread_and_no_shell(team, tmp_path):
    store, _, a, b, conv = team
    output = tmp_path / "args.json"
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                    f"open({str(output)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o700)
    message, _ = store.send(a, conv, "question", "Text mit ; $(false) und Leerzeichen")
    delivered = store.deliver_codex(message, command=str(fake))
    args = json.loads(output.read_text())
    thread = store._participant(b)["thread"]
    assert args[:4] == ["queue", "--thread", thread, "--message"]
    assert message["id"] in args[4] and "keine Nutzerfreigabe" in args[4]
    assert delivered["delivery"] == "queue_accepted" and delivered["acked"] is None


def test_two_stores_claim_codex_queue_only_once(team, monkeypatch):
    store, _, a, _, conv = team
    message, _ = store.send(a, conv, "note", "one queue")
    other = Store(store.path)
    ready = threading.Barrier(2)
    calls, results, errors = [], [], []

    # Each connection must read the same stored row before either can claim it.
    for connection in (store, other):
        original_one = connection._one
        seen = [False]

        def first_read(sql, args=(), original_one=original_one, seen=seen):
            row = original_one(sql, args)
            if sql == "SELECT * FROM messages WHERE id=?" and not seen[0]:
                seen[0] = True
                assert row["delivery"] == "stored"
                ready.wait(timeout=3)
            return row

        monkeypatch.setattr(connection, "_one", first_read)

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("teamkanal.subprocess.run", fake_run)

    def deliver(connection):
        try:
            results.append(connection.deliver_codex(message))
        except BaseException as exc:
            errors.append(exc)

    jobs = [threading.Thread(target=deliver, args=(connection,)) for connection in (store, other)]
    try:
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(5)
        assert not any(job.is_alive() for job in jobs)
        assert not errors
        assert len(results) == 2
        assert len(calls) == 1
        assert store.read(a, conv)["messages"][0]["delivery"] == "queue_accepted"
        assert store.deliver_codex(message)["delivery"] == "queue_accepted"
        assert other.deliver_codex(message)["delivery"] == "queue_accepted"
        assert len(calls) == 1
        restarted = Store(store.path)
        try:
            assert restarted.deliver_codex(message)["delivery"] == "queue_accepted"
            assert restarted.read(a, conv)["messages"][0]["acked"] is None
        finally:
            restarted.close_db()
        assert len(calls) == 1
    finally:
        other.close_db()


def test_codex_timeout_does_not_retry_ambiguous_delivery(team, monkeypatch):
    store, _, a, _, conv = team
    message, _ = store.send(a, conv, "note", "timeout")
    calls = []

    def time_out(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr("teamkanal.subprocess.run", time_out)
    first = store.deliver_codex(message)
    assert first["delivery"] == "queue_failed"
    assert first["delivery_error"] and first["acked"] is None
    assert store.deliver_codex(message)["delivery"] == "queue_failed"
    restarted = Store(store.path)
    try:
        assert restarted.deliver_codex(message)["delivery"] == "queue_failed"
    finally:
        restarted.close_db()
    assert len(calls) == 1


def test_mcp_queue_failure_is_tool_error_without_ack(team, tmp_path):
    store, _, a, b, conv = team
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\nexit 8\n")
    fake.chmod(0o700)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", a, "--deliver-codex"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        _send(proc, "initialize", 1)
        _line(proc)
        _send(proc, "tools/call", 2, {"name": "team_send", "arguments": {
            "conversation": conv, "kind": "note", "text": "queue Fehler"}})
        response = _line(proc)["result"]
        assert response["isError"] is True
        message = json.loads(response["content"][0]["text"])
        assert message["delivery"] == "queue_failed" and message["acked"] is None
        assert store.read(b, conv)["messages"][0]["id"] == message["id"]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0


def test_cli_help_json_ids_and_error(tmp_path):
    db = tmp_path / "db.sqlite3"
    project = tmp_path / "p"
    project.mkdir()
    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), *args],
                              capture_output=True, text=True)
    assert run("--help").returncode == 0
    joined = run("join", "--name", "A", "--kind", "manual", "--project", str(project))
    assert joined.returncode == 0
    participant = json.loads(joined.stdout)["id"]
    failed = run("ack", "--participant", participant, "--message", ident())
    assert failed.returncode == 2
    assert "error" in json.loads(failed.stderr)
    assert "Traceback" not in failed.stderr
    malformed = run("send", "--kind", "wrong")
    assert malformed.returncode == 2 and "error" in json.loads(malformed.stderr)


def test_same_store_transaction_blocks_readers_and_channel_until_commit(tmp_path):
    class PausingStore(Store):
        def _one(self, sql, args=()):
            row = super()._one(sql, args)
            if sql == "SELECT * FROM messages WHERE id=?" and row and row["body"] == "held":
                inserted.set()
                assert release.wait(3)
            return row

    inserted, release = threading.Event(), threading.Event()
    project = tmp_path / "project"
    project.mkdir()
    store = PausingStore(tmp_path / "private" / "team.sqlite3")
    a, b = ident(), ident()
    store.register(a, "A", "claude", str(project))
    store.register(b, "B", "codex", str(project), ident())
    conv = store.open(a, b, "parallel")["id"]
    prior, _ = store.send(a, conv, "note", "already acked")
    store.ack(b, prior["id"])
    sent = []
    sender = threading.Thread(target=lambda: sent.append(store.send(a, conv, "note", "held")))
    sender.start()
    assert inserted.wait(3)
    # A second connection sees only committed rows while this connection is mid-send.
    separate = Store(store.path)
    assert separate.pending(b) == []
    separate.close_db()
    started = threading.Barrier(5)
    completed = []
    calls = (lambda: store.pending(b), lambda: store.status(a), lambda: store.read(b, conv),
             lambda: store.ack(b, prior["id"]))
    def reader(fn):
        started.wait()
        completed.append(fn())
    readers = [threading.Thread(target=reader, args=(fn,)) for fn in calls]
    for reader_thread in readers:
        reader_thread.start()
    started.wait()
    # A real channel poller shares precisely this Store.
    notices = []
    mcp = MCP(store, b, channel=True)
    mcp.write = notices.append
    poller = threading.Thread(target=mcp._notify)
    poller.start()
    time.sleep(0.35)
    assert not notices
    assert not completed
    release.set()
    sender.join(3)
    for reader_thread in readers:
        reader_thread.join(3)
    assert len(sent) == 1 and len(completed) == 4
    deadline = time.monotonic() + 3
    while not notices and time.monotonic() < deadline:
        time.sleep(0.01)
    mcp.stop.set()
    poller.join(3)
    assert len(notices) == 1
    assert notices[0]["params"]["meta"]["message_id"] == sent[0][0]["id"]
    store.close_db()


def test_rollback_never_notifies_or_leaks_pending(tmp_path):
    class FailingStore(Store):
        def _one(self, sql, args=()):
            row = super()._one(sql, args)
            if sql == "SELECT * FROM messages WHERE id=?" and row and row["body"] == "rollback":
                inserted.set()
                assert release.wait(3)
                raise TeamError("forced rollback")
            return row

    inserted, release = threading.Event(), threading.Event()
    project = tmp_path / "project"
    project.mkdir()
    store = FailingStore(tmp_path / "private" / "team.sqlite3")
    a, b = ident(), ident()
    store.register(a, "A", "claude", str(project))
    store.register(b, "B", "manual", str(project))
    conv = store.open(a, b, "rollback")["id"]
    notices = []
    mcp = MCP(store, b, channel=True)
    mcp.write = notices.append
    poller = threading.Thread(target=mcp._notify)
    poller.start()
    errors = []
    sender = threading.Thread(target=lambda: errors.append(pytest.raises(TeamError, store.send, a, conv, "note", "rollback")))
    sender.start()
    assert inserted.wait(3)
    separate = Store(store.path)
    assert separate.pending(b) == []
    separate.close_db()
    assert notices == []
    release.set()
    sender.join(3)
    time.sleep(0.3)
    mcp.stop.set()
    poller.join(3)
    assert errors and notices == [] and store.pending(b) == []
    store.close_db()


def test_optional_adapter_both_directions_and_retry_is_single_queue(team, monkeypatch):
    store, project, a, b, conv = team
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("teamkanal.subprocess.run", fake_run)
    to_codex = MCP(store, a, deliver=True).call("team_send", {"conversation": conv, "kind": "question",
                                                   "text": "to codex", "idempotency": ident()})
    assert to_codex["delivery"] == "queue_accepted" and len(calls) == 1
    to_claude = MCP(store, b, deliver=True).call("team_send", {"conversation": conv, "kind": "reply",
                                                    "text": "to claude", "reply_to": to_codex["id"]})
    assert to_claude["delivery"] == "stored" and len(calls) == 1
    assert store.read(a, conv)["messages"][-1]["body"] == "to claude"
    repeated = store.deliver_codex(to_codex)
    assert repeated["delivery"] == "queue_accepted" and len(calls) == 1
    assert [peer["id"] for peer in store.status(a)["peers"]] == [b]
    c = ident()
    other = project.parent / "other"
    other.mkdir()
    store.register(c, "Other", "manual", str(other))
    assert [peer["id"] for peer in store.status(a)["peers"]] == [b]


def test_missing_codex_thread_rejected_before_send(team):
    store, project, a, _, _ = team
    no_thread = ident()
    store.register(no_thread, "No thread", "codex", str(project))
    conv = store.open(a, no_thread, "No queue")["id"]
    with pytest.raises(TeamError, match="Thread-ID"):
        MCP(store, a, deliver=True).call("team_send", {"conversation": conv, "kind": "note", "text": "x"})
    assert store.read(a, conv)["messages"] == []


def test_cli_deliver_option_works_in_both_directions(team, monkeypatch, capsys):
    store, _, a, b, conv = team
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("teamkanal.subprocess.run", fake_run)
    prefix = ["--db", str(store.path), "send"]
    assert main(prefix + ["--participant", a, "--conversation", conv, "--kind", "question",
                          "--text", "hin", "--deliver-codex"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["delivery"] == "queue_accepted" and len(calls) == 1
    assert main(prefix + ["--participant", b, "--conversation", conv, "--kind", "reply",
                          "--text", "zurück", "--reply-to", first["id"], "--deliver-codex"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["delivery"] == "stored" and len(calls) == 1
    assert store.read(a, conv)["messages"][-1]["id"] == second["id"]


def test_queue_subprocess_does_not_hold_store_lock(team, monkeypatch):
    store, _, a, b, conv = team
    entered, release = threading.Event(), threading.Event()
    def slow_run(args, **kwargs):
        entered.set()
        assert release.wait(3)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("teamkanal.subprocess.run", slow_run)
    message, _ = store.send(a, conv, "note", "slow")
    job = threading.Thread(target=lambda: store.deliver_codex(message))
    job.start()
    assert entered.wait(3)
    # Status, pending, and acknowledgment can proceed during the slow queue call.
    assert store.status(a)["participant"]["id"] == a
    assert store.pending(b)[0]["delivery"] == "queue_attempted"
    assert store.ack(b, message["id"])["acked"]
    release.set()
    job.join(3)
    assert store.read(a, conv)["messages"][0]["delivery"] == "queue_accepted"


TRANSACTIONS = [
    ("register", "INSERT INTO participants"),
    ("open", "INSERT INTO conversations"),
    ("send", "INSERT INTO messages"),
    ("ack", "UPDATE messages SET acked"),
    ("close", "UPDATE conversations SET state"),
]


class FaultConnection:
    def __init__(self, connection, trigger, original, rollback_error=None, close_error=None):
        self.connection = connection
        self.trigger = trigger
        self.original = original
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.interrupted = False

    def execute(self, sql, args=()):
        if sql == "ROLLBACK" and self.rollback_error is not None:
            raise self.rollback_error
        result = self.connection.execute(sql, args)
        if sql.startswith(self.trigger) and not self.interrupted:
            self.interrupted = True
            raise self.original
        return result

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.connection.close()

    def __getattr__(self, name):
        return getattr(self.connection, name)


def transaction_case(store, project, a, b):
    conversation = store.open(a, b, "transaction probe", 6)["id"]
    baseline, _ = store.send(a, conversation, "note", "existing")
    person, key = ident(), ident()
    actions = {
        "register": lambda: store.register(person, "New", "manual", str(project)),
        "open": lambda: store.open(a, b, "new conversation"),
        "send": lambda: store.send(a, conversation, "note", "new message", idempotency=key),
        "ack": lambda: store.ack(b, baseline["id"]),
        "close": lambda: store.close(a, conversation),
    }
    return conversation, baseline, person, key, actions


def assert_transaction_effect(observer, operation, a, conversation, baseline, person):
    assert (observer._one("SELECT * FROM participants WHERE id=?", (person,)) is not None) == (operation == "register")
    assert len(observer.db.execute("SELECT * FROM conversations WHERE topic='new conversation'").fetchall()) == (operation == "open")
    messages = observer.read(a, conversation)["messages"]
    assert [message["body"] for message in messages] == (["existing", "new message"] if operation == "send" else ["existing"])
    assert bool(messages[0]["acked"]) == (operation == "ack")
    assert observer.read(a, conversation)["conversation"]["state"] == ("closed" if operation == "close" else "open")
    assert len({message["id"] for message in messages}) == len(messages)


@pytest.mark.parametrize("operation, mutation", TRANSACTIONS)
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_after_commit_interrupt_preserves_durable_effect_and_connection(team, operation, mutation, failure_type):
    store, project, a, b, _ = team
    conversation, baseline, person, key, actions = transaction_case(store, project, a, b)
    original = failure_type("after commit")
    store.db = FaultConnection(store.db, "COMMIT", original)
    with pytest.raises(failure_type) as caught:
        actions[operation]()
    assert caught.value is original
    assert store.db.interrupted and not store.db.in_transaction and not store._poisoned
    observer = Store(store.path)
    try:
        assert_transaction_effect(observer, operation, a, conversation, baseline, person)
        assert [message["id"] for message in store.pending(b)] == [message["id"] for message in observer.pending(b)]
        if operation == "register":
            assert store.register(person, "New", "manual", str(project))["id"] == person
        elif operation == "open":
            assert len(store.db.execute("SELECT * FROM conversations WHERE topic='new conversation'").fetchall()) == 1
        elif operation == "send":
            repeated, created = store.send(a, conversation, "note", "new message", idempotency=key)
            assert not created and repeated["id"] == observer.read(a, conversation)["messages"][-1]["id"]
        elif operation == "ack":
            assert store.ack(b, baseline["id"])["acked"] == observer.read(a, conversation)["messages"][0]["acked"]
        else:
            conversation = store.open(a, b, "after close")["id"]
        followup, created = store.send(a, conversation, "note", "followup")
        assert created and observer.read(a, conversation)["messages"][-1]["id"] == followup["id"]
        assert len(store.pending(b)) == len(observer.pending(b))
    finally:
        observer.close_db()


@pytest.mark.parametrize("operation, mutation", TRANSACTIONS)
@pytest.mark.parametrize("rollback_type", [sqlite3.OperationalError, KeyboardInterrupt])
@pytest.mark.parametrize("close_fails", [False, True])
def test_rollback_failure_keeps_original_and_quarantines_store(team, operation, mutation, rollback_type, close_fails):
    store, project, a, b, _ = team
    conversation, baseline, person, _, actions = transaction_case(store, project, a, b)
    observer = Store(store.path)
    original = KeyboardInterrupt("after mutation")
    rollback_error = rollback_type("rollback failed")
    close_error = RuntimeError("close failed") if close_fails else None
    store.db = FaultConnection(store.db, mutation, original, rollback_error, close_error)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            actions[operation]()
        assert caught.value is original
        assert store.db.interrupted and store._poisoned
        notes = "\n".join(original.__notes__)
        assert "Rollback fehlgeschlagen" in notes and "rollback failed" in notes
        assert ("Schließen der Verbindung fehlgeschlagen" in notes) == close_fails
        with pytest.raises(TeamError, match="gesperrt"):
            store.pending(b)  # The Channel poller must not read an uncommitted row.
        with pytest.raises(TeamError, match="gesperrt"):
            store.send(a, conversation, "note", "forbidden")
        assert observer.pending(b) == [baseline]
        assert observer.read(a, conversation)["messages"] == [baseline]
        assert observer.read(a, conversation)["conversation"]["state"] == "open"
        assert observer._one("SELECT * FROM participants WHERE id=?", (person,)) is None
        assert observer._one("SELECT * FROM conversations WHERE topic='new conversation'") is None
    finally:
        if close_fails:
            store.db.connection.close()  # Release the simulated close failure's write lock.
            store.db.close_error = None
        observer.close_db()


def test_channel_poller_stops_after_rollback_failure(team):
    store, project, a, b, _ = team
    conversation, _, _, _, actions = transaction_case(store, project, a, b)
    store.db = FaultConnection(store.db, "INSERT INTO messages", KeyboardInterrupt("send failed"),
                               sqlite3.OperationalError("rollback failed"))
    with pytest.raises(KeyboardInterrupt):
        actions["send"]()
    notices = []
    mcp = MCP(store, b, channel=True)
    mcp.write = notices.append
    poller = threading.Thread(target=mcp._notify)
    poller.start()
    poller.join(2)
    assert not poller.is_alive() and notices == []
    assert store._poisoned


def test_cli_final_close_error_cannot_mask_transaction_error(monkeypatch, tmp_path, capsys):
    original = KeyboardInterrupt("original transaction error")

    class FailedStore:
        _poisoned = True

        def register(self, *args):
            raise original

        def close_db(self):
            raise RuntimeError("second close error")

    monkeypatch.setattr("teamkanal.Store", lambda path: FailedStore())
    with pytest.raises(KeyboardInterrupt) as caught:
        main(["--db", str(tmp_path / "unused.sqlite3"), "join", "--name", "A",
              "--kind", "manual", "--project", str(tmp_path)])
    assert caught.value is original
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("operation, mutation", [
    ("register", "INSERT INTO participants"),
    ("open", "INSERT INTO conversations"),
    ("send", "INSERT INTO messages"),
    ("ack", "UPDATE messages SET acked"),
    ("close", "UPDATE conversations SET state"),
])
@pytest.mark.parametrize("interrupt_at_begin", [False, True])
def test_keyboard_interrupt_rolls_back_every_store_transaction(team, operation, mutation, interrupt_at_begin):
    store, project, a, b, conv = team
    message, _ = store.send(a, conv, "note", "existing")

    class InterruptingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.interrupted = False

        def execute(self, sql, args=()):
            result = self.connection.execute(sql, args)
            if sql.startswith("BEGIN IMMEDIATE" if interrupt_at_begin else mutation) and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("after mutation")
            return result

        def __getattr__(self, name):
            return getattr(self.connection, name)

    store.db = InterruptingConnection(store.db)
    person = ident()
    action = {
        "register": lambda: store.register(person, "New", "manual", str(project)),
        "open": lambda: store.open(a, b, "interrupted"),
        "send": lambda: store.send(a, conv, "note", "interrupted"),
        "ack": lambda: store.ack(b, message["id"]),
        "close": lambda: store.close(a, conv),
    }[operation]
    with pytest.raises(KeyboardInterrupt, match="after mutation"):
        action()
    assert store.db.interrupted
    assert not store.db.in_transaction
    assert store._one("SELECT * FROM participants WHERE id=?", (person,)) is None
    assert store._one("SELECT * FROM conversations WHERE topic=?", ("interrupted",)) is None
    assert [row["body"] for row in store.read(a, conv)["messages"]] == ["existing"]
    assert store.read(a, conv)["messages"][0]["acked"] is None
    assert store.read(a, conv)["conversation"]["state"] == "open"
    followup, created = store.send(a, conv, "note", "recovered")
    assert created and followup["body"] == "recovered"


def test_keyboard_interrupt_during_send_never_reaches_concurrent_poller(team):
    store, _, a, b, conv = team
    inserted, release = threading.Event(), threading.Event()

    class InterruptingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.interrupted = False

        def execute(self, sql, args=()):
            result = self.connection.execute(sql, args)
            if sql.startswith("INSERT INTO messages") and not self.interrupted:
                self.interrupted = True
                inserted.set()
                assert release.wait(3)
                raise KeyboardInterrupt("after insert")
            return result

        def __getattr__(self, name):
            return getattr(self.connection, name)

    store.db = InterruptingConnection(store.db)
    notices, errors = [], []
    mcp = MCP(store, b, channel=True)
    mcp.write = notices.append
    poller = threading.Thread(target=mcp._notify)
    sender = threading.Thread(target=lambda: _catch_interrupt(store, a, conv, errors))
    poller.start()
    sender.start()
    try:
        assert inserted.wait(3)
        time.sleep(0.35)
        assert not notices
        release.set()
        sender.join(3)
        assert not sender.is_alive() and len(errors) == 1
        assert isinstance(errors[0], KeyboardInterrupt)
        assert not store.db.in_transaction
        assert store.read(a, conv)["messages"] == []
        assert store.pending(b) == []
        time.sleep(0.35)
        assert not notices
        sent, created = store.send(a, conv, "note", "recovered")
        assert created and sent["body"] == "recovered"
    finally:
        release.set()
        mcp.stop.set()
        sender.join(3)
        poller.join(3)


def _catch_interrupt(store, participant, conversation, errors):
    try:
        store.send(participant, conversation, "note", "interrupted")
    except BaseException as exc:
        errors.append(exc)


@pytest.mark.parametrize("adapter", ["cli", "mcp"])
def test_each_adapter_recovers_stored_message_once(team, monkeypatch, capsys, adapter):
    store, _, a, _, conv = team
    key = ident()
    original, _ = store.send(a, conv, "note", "stored before claim", idempotency=key)
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("teamkanal.subprocess.run", fake_run)
    args = ["--db", str(store.path), "send", "--participant", a,
            "--conversation", conv, "--kind", "note", "--text", "stored before claim",
            "--idempotency", key, "--deliver-codex"]
    def retry():
        if adapter == "cli":
            assert main(args) == 0
            return json.loads(capsys.readouterr().out)
        return MCP(store, a, deliver=True).call("team_send", {
            "conversation": conv, "kind": "note", "text": "stored before claim", "idempotency": key})
    first = retry()
    assert first["id"] == original["id"] and first["delivery"] == "queue_accepted"
    assert len(calls) == 1
    restarted = Store(store.path)
    try:
        second = MCP(restarted, a, deliver=True).call("team_send", {
            "conversation": conv, "kind": "note", "text": "stored before claim", "idempotency": key})
        assert second["id"] == original["id"] and second["delivery"] == "queue_accepted"
    finally:
        restarted.close_db()
    assert retry()["id"] == original["id"]
    assert len(calls) == 1 and len(store.read(a, conv)["messages"]) == 1


@pytest.mark.parametrize("state", ["queue_attempted", "queue_accepted", "queue_failed"])
def test_adapters_never_retry_queue_state(team, monkeypatch, capsys, state):
    store, _, a, _, conv = team
    key = ident()
    original, _ = store.send(a, conv, "note", "uncertain", idempotency=key)
    store.db.execute("UPDATE messages SET delivery=? WHERE id=?", (state, original["id"]))
    calls = []
    monkeypatch.setattr("teamkanal.subprocess.run", lambda *args, **kwargs: calls.append(args))
    args = ["--db", str(store.path), "send", "--participant", a,
            "--conversation", conv, "--kind", "note", "--text", "uncertain",
            "--idempotency", key, "--deliver-codex"]
    assert main(args) == 0
    cli = json.loads(capsys.readouterr().out)
    mcp = MCP(store, a, deliver=True).call("team_send", {
        "conversation": conv, "kind": "note", "text": "uncertain", "idempotency": key})
    assert cli["id"] == mcp["id"] == original["id"]
    assert cli["delivery"] == mcp["delivery"] == state
    assert calls == [] and len(store.read(a, conv)["messages"]) == 1


def test_cli_and_mcp_retry_stored_message_claim_once_across_stores(team, monkeypatch):
    store, _, a, b, conv = team
    key = ident()
    original, _ = store.send(a, conv, "note", "before claim", idempotency=key)
    assert original["delivery"] == "stored"
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("teamkanal.subprocess.run", fake_run)
    other = Store(store.path)
    ready = threading.Barrier(2)
    original_deliver = Store.deliver_codex
    def simultaneous_deliver(self, message, command="codex"):
        ready.wait(timeout=3)
        return original_deliver(self, message, command)
    monkeypatch.setattr(Store, "deliver_codex", simultaneous_deliver)
    results, errors = [], []
    cli_args = ["--db", str(store.path), "send", "--participant", a,
                "--conversation", conv, "--kind", "note", "--text", "before claim",
                "--idempotency", key, "--deliver-codex"]
    def cli_retry():
        try:
            results.append(main(cli_args))
        except BaseException as exc:
            errors.append(exc)
    def mcp_retry():
        try:
            results.append(MCP(other, a, deliver=True).call("team_send", {
                "conversation": conv, "kind": "note", "text": "before claim", "idempotency": key}))
        except BaseException as exc:
            errors.append(exc)
    jobs = [threading.Thread(target=cli_retry), threading.Thread(target=mcp_retry)]
    try:
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(5)
        assert not any(job.is_alive() for job in jobs)
        assert not errors and len(results) == 2
        assert len(calls) == 1
        assert len(store.read(a, conv)["messages"]) == 1
        assert store.read(a, conv)["messages"][0]["id"] == original["id"]
        monkeypatch.setattr(Store, "deliver_codex", original_deliver)
        assert main(cli_args) == 0
        restarted = Store(store.path)
        try:
            retry = MCP(restarted, a, deliver=True).call("team_send", {
                "conversation": conv, "kind": "note", "text": "before claim", "idempotency": key})
            assert retry["id"] == original["id"]
            assert retry["delivery"] == "queue_accepted"
        finally:
            restarted.close_db()
        assert len(calls) == 1
        assert len(store.read(a, conv)["messages"]) == 1
    finally:
        other.close_db()


def test_json_rpc_error_responses_notification_and_recovery_over_stdio(team):
    store, _, a, _, _ = team
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", a], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        proc.stdin.write('{broken\n')
        proc.stdin.flush()
        error = _line(proc)
        assert error["id"] is None and error["error"]["code"] == -32700
        for invalid in ([], {"jsonrpc": "1.0", "method": "ping", "id": 2},
                        {"jsonrpc": "2.0", "method": 7, "id": 3}):
            proc.stdin.write(json.dumps(invalid) + "\n")
            proc.stdin.flush()
            error = _line(proc)
            assert error["id"] is None and error["error"]["code"] == -32600
        _send(proc, "missing/method", 11)
        error = _line(proc)
        assert error["id"] == 11 and error["error"]["code"] == -32601
        _send(proc, "missing/notification")
        assert not select.select([proc.stdout], [], [], 0.3)[0]
        _send(proc, "ping", 12)
        assert _line(proc) == {"jsonrpc": "2.0", "id": 12, "result": {}}
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0
        assert proc.stderr.read() == ""


class Clock:
    def __init__(self):
        self.value = dt.datetime(2026, 10, 1, 23, 59, 59, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.value


def test_daily_limit_project_manual_closed_and_next_utc_day(team, tmp_path):
    store, project, a, b, _ = team
    clock = Clock()
    store._clock = clock
    store.db.execute("UPDATE conversations SET created=?", (store._now(),))
    # Closed conversations also consume the project's daily budget.
    for _ in range(9):
        store.close(a, store.open(b, a, "daily")["id"])
    before = len(store.status(a)["conversations"])
    with pytest.raises(TeamError, match="Tagesgrenze"):
        store.open(a, b, "eleventh")
    assert len(store.status(a)["conversations"]) == before == 10
    manual = ident()
    store.register(manual, "Manual", "manual", str(project))
    for _ in range(11):
        store.open(a, manual, "manual is exempt")
    other = tmp_path / "other-project"
    other.mkdir()
    c, d = ident(), ident()
    store.register(c, "C", "claude", str(other))
    store.register(d, "D", "claude", str(other))
    for _ in range(10):
        store.open(c, d, "other budget")
    with pytest.raises(TeamError, match="Tagesgrenze"):
        store.open(d, c, "eleventh other")
    # +02:00 is still the same UTC day; local midnight cannot reset the limit.
    clock.value = dt.datetime(2026, 10, 2, 1, 59, 59, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    with pytest.raises(TeamError, match="Tagesgrenze"):
        store.open(a, b, "local midnight")
    clock.value += dt.timedelta(seconds=1)
    assert store.open(a, b, "next UTC day")["created"].startswith("2026-10-02")


def test_parallel_opens_cannot_exceed_daily_limit(team):
    store, _, a, b, _ = team
    for _ in range(8):
        store.open(a, b, "budget")
    barrier = threading.Barrier(8)
    results, errors = [], []
    def attempt():
        own = Store(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                results.append(own.open(a, b, "racing")["id"])
            except TeamError:
                results.append(None)
        except BaseException as exc:
            errors.append(exc)
        finally:
            own.close_db()
    jobs = [threading.Thread(target=attempt) for _ in range(8)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(10)
    assert not any(job.is_alive() for job in jobs) and not errors
    assert len(results) == 8 and sum(result is not None for result in results) == 1
    assert len(store.status(a)["conversations"]) == 10


def test_old_database_migration_preserves_all_rows(tmp_path):
    path = tmp_path / "old.sqlite3"
    path.touch(mode=0o600)
    project = tmp_path / "old-project"
    project.mkdir()
    a, b, cid, mid, key = [ident() for _ in range(5)]
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE participants (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            project TEXT NOT NULL, thread TEXT);
        CREATE TABLE conversations (id TEXT PRIMARY KEY, project TEXT NOT NULL, a TEXT NOT NULL,
            b TEXT NOT NULL, topic TEXT NOT NULL, state TEXT NOT NULL,
            max_messages INTEGER NOT NULL, created TEXT NOT NULL);
        CREATE TABLE messages (id TEXT PRIMARY KEY, conversation TEXT NOT NULL, sender TEXT NOT NULL,
            recipient TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
            refs TEXT NOT NULL, reply_to TEXT, idem TEXT NOT NULL,
            created TEXT NOT NULL, delivery TEXT NOT NULL, delivery_error TEXT,
            acked TEXT, UNIQUE(sender, idem));
        CREATE INDEX messages_recipient ON messages(recipient, acked);
    """)
    old.executemany("INSERT INTO participants VALUES (?,?,?,?,?)", [
        (a, "A", "claude", str(project), None), (b, "B", "manual", str(project), None)])
    old.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?)",
                (cid, str(project), a, b, "old", "open", 6, "2026-09-30T12:00:00+00:00"))
    old.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, cid, a, b, "note", "unverändert ä", "[]", None, key,
                 "2026-09-30T12:00:01+00:00", "stored", None, None))
    old.commit()
    original = {table: old.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("participants", "conversations", "messages")}
    old.close()
    for _ in range(2):  # Migration and re-opening are idempotent.
        migrated = Store(path)
        try:
            assert {r["name"] for r in migrated.db.execute("PRAGMA table_info(participants)")} == {
                "id", "name", "kind", "project", "thread", "last_seen"}
            for table, rows in original.items():
                query = "SELECT id,name,kind,project,thread FROM participants" if table == "participants" else f"SELECT * FROM {table}"
                assert [tuple(row) for row in migrated.db.execute(query)] == rows
            for table in ("handovers", "user_questions"):
                assert migrated.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            assert migrated.pending(b)[0]["id"] == mid
            assert migrated.read(a, cid)["messages"][0]["body"] == "unverändert ä"
            assert migrated._participant(a)["last_seen"] is None
            migrated.register(a, "A", "claude", str(project))
        finally:
            migrated.close_db()


def test_touch_liveness_boundary_and_register_idempotency(team):
    store, project, a, b, _ = team
    clock = Clock()
    store._clock = clock
    peer = store.status(a)["peers"][0]
    assert peer["last_seen"] is None and peer["lebend"] is False
    seen = store.touch(b)["last_seen"]
    store.register(b, "B", "codex", str(project), store._participant(b)["thread"])
    assert store._participant(b)["last_seen"] == seen
    for seconds, alive in ((0, True), (120, True), (121, False), (-1, False)):
        clock.value = dt.datetime.fromisoformat(seen) + dt.timedelta(seconds=seconds)
        peer = store.status(a)["peers"][0]
        assert peer["last_seen"] == seen and peer["lebend"] is alive
    with pytest.raises(TeamError):
        store.touch(ident())
    assert not store.db.in_transaction


def test_tools_call_updates_last_seen_over_real_stdio(team):
    store, _, a, b, _ = team
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", b], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1)
        instructions = _line(proc)["result"]["instructions"]
        for phrase in ("genau einer lebenden", "10 je UTC-Tag", "team_ask_fatih statt raten",
                       "team_handover", "team_take_handover", "team_discard_handover", "Freigaben nie"):
            assert phrase in instructions
        for number, arguments in ((2, {}), (3, {"unknown": True})):
            store.db.execute("UPDATE participants SET last_seen=NULL WHERE id=?", (b,))
            _send(proc, "tools/call", number, {"name": "team_status", "arguments": arguments})
            response = _line(proc)["result"]
            assert bool(response.get("isError")) == (number == 3)
            peer = store.status(a)["peers"][0]
            assert peer["last_seen"] and peer["lebend"] is True
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0
        assert proc.stderr.read() == ""


def test_heartbeat_without_channel_and_clean_shutdown(team):
    store, _, a, b, _ = team
    # Shorten only the timer in the subprocess; exercise the real stdio server,
    # independent of notifications/initialized and without channel mode.
    bootstrap = (f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); import teamkanal; "
                 "assert teamkanal.HEARTBEAT_SECONDS <= 30; teamkanal.HEARTBEAT_SECONDS = 0.05; "
                 "raise SystemExit(teamkanal.main(sys.argv[1:]))")
    proc = subprocess.Popen([sys.executable, "-c", bootstrap, "--db", str(store.path), "mcp",
                             "--participant", b], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1)
        assert "experimental" not in _line(proc)["result"]["capabilities"]
        times = set()
        deadline = time.monotonic() + 3
        while len(times) < 3 and time.monotonic() < deadline:
            peer = store.status(a)["peers"][0]
            if peer["last_seen"]:
                times.add(peer["last_seen"])
                assert peer["lebend"]
            time.sleep(0.01)
        assert len(times) == 3
        assert not select.select([proc.stdout], [], [], 0.15)[0]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0
        assert proc.stderr.read() == ""
    stopped = store._participant(b)["last_seen"]
    time.sleep(0.1)
    assert store._participant(b)["last_seen"] == stopped


def handover_texts(**overrides):
    return {**{field: "keine" for field in HANDOVER_FIELDS}, **overrides}


@pytest.mark.parametrize("field", HANDOVER_FIELDS)
@pytest.mark.parametrize("bad", ["", "x" * 16001])
def test_handover_all_text_fields_required(team, field, bad):
    store, _, a, _, _ = team
    with pytest.raises(TeamError, match=field):
        store.handover(a, **handover_texts(**{field: bad}))
    assert store.handovers(a) == []


def test_handover_project_isolation_order_and_parallel_claim(team, tmp_path):
    store, project, a, b, _ = team
    clock = Clock()
    store._clock = clock
    first = store.handover(a, **handover_texts(fertig="Erster"))
    clock.value += dt.timedelta(seconds=1)
    second = store.handover(a, **handover_texts(fertig="Zweiter"))
    assert [row["id"] for row in store.handovers(b)] == [second["id"], first["id"]]
    with pytest.raises(TeamError, match="eigene"):
        store.take_handover(a, second["id"])
    other = tmp_path / "other"
    other.mkdir()
    outsider = ident()
    store.register(outsider, "Other", "manual", str(other))
    assert store.handovers(outsider) == []
    with pytest.raises(TeamError):
        store.take_handover(outsider, first["id"])
    peers = [ident() for _ in range(8)]
    for peer in peers:
        store.register(peer, "New session", "claude", str(project))
    barrier = threading.Barrier(8)
    successes, refused, errors = [], [], []
    def take(peer):
        own = Store(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                successes.append(own.take_handover(peer, second["id"]))
            except TeamError:
                refused.append(peer)
        except BaseException as exc:
            errors.append(exc)
        finally:
            own.close_db()
    jobs = [threading.Thread(target=take, args=(peer,)) for peer in peers]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(10)
    assert not any(job.is_alive() for job in jobs) and not errors
    assert len(successes) == 1 and len(refused) == 7
    assert successes[0]["taken_by"] in peers and successes[0]["taken_at"]
    assert [row["id"] for row in store.handovers(b)] == [first["id"]]


def cli(store, *args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), "--db", str(store.path), *args],
                          capture_output=True, text=True, env=env, timeout=8)


def assert_answer(store, asker, result, answered_by, question_text, answer_text):
    message = result["message"]
    assert message in store.pending(asker)
    sender = store._participant(message["sender"])
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "teamkanal-fragenliste:" + sender["project"]))
    assert sender["id"] == expected_id and sender["name"] == "Fatih (Fragenliste)"
    assert sender["kind"] == "manual" and sender["thread"] is None
    assert message["kind"] == "note" and message["acked"] is None
    assert result["question"]["answer"] == answer_text
    assert result["question"]["answered_by"] == answered_by
    assert result["question"]["answered_at"]
    for text in (f"Frage: {question_text}", f"Antwort: {answer_text}",
                 f"eingetragen von {answered_by}", ANSWER_NOTICE):
        assert text in message["body"]
    conversation = store.read(asker, message["conversation"])["conversation"]
    assert conversation["max_messages"] == 2
    assert conversation["a"] == sender["id"] and conversation["b"] == asker


def test_questions_cli_end_to_end_and_no_second_answer(team):
    store, _, a, _, _ = team
    asked = cli(store, "ask", "--participant", a, "--text", "Welche Farbe?")
    assert asked.returncode == 0
    question = json.loads(asked.stdout)
    listed = cli(store, "fragen", "--project", question["project"])
    assert listed.returncode == 0 and json.loads(listed.stdout) == [question]
    answered = cli(store, "antworte", "--frage", question["id"], "--text", "Blau")
    assert answered.returncode == 0
    result = json.loads(answered.stdout)
    assert_answer(store, a, result, "fatih-cli", "Welche Farbe?", "Blau")
    assert json.loads(cli(store, "fragen", "--project", question["project"]).stdout) == []
    history = cli(store, "questions", "--participant", a)
    assert json.loads(history.stdout) == [result["question"]]
    repeated = cli(store, "antworte", "--frage", question["id"], "--text", "Rot")
    assert repeated.returncode == 2 and "bereits beantwortet" in json.loads(repeated.stderr)["error"]
    assert store.pending(a) == [result["message"]]


def test_questions_mcp_identity_isolation_and_manual_reuse(team, tmp_path):
    store, _, a, b, _ = team
    other = tmp_path / "other"
    other.mkdir()
    outsider = ident()
    store.register(outsider, "Other", "claude", str(other))
    question = MCP(store, b).call("team_ask_fatih", {"text": "Farbe?"})
    assert MCP(store, a).call("team_questions", {}) == [question]
    assert store.questions(outsider) == []
    with pytest.raises(TeamError, match="Projekt"):
        MCP(store, outsider).call("team_answer_question", {"question": question["id"], "answer": "Rot"})
    result = MCP(store, a).call("team_answer_question", {"question": question["id"], "answer": "Blau"})
    assert_answer(store, b, result, a, "Farbe?", "Blau")
    next_question = store.ask_fatih(a, "Noch eine Frage")
    second = store.answer_question(None, next_question["id"], "Sachantwort")
    assert second["message"]["sender"] == result["message"]["sender"]
    assert second["message"]["conversation"] != result["message"]["conversation"]
    assert len(store.questions(a)) == 2 and len(store.questions_for_project(str(other))) == 0


def test_question_and_answer_limits_before_claim(team):
    store, _, a, _, _ = team
    assert MAX_QUESTION_CHARS == 8000 and MAX_ANSWER_CHARS == 7000
    question = store.ask_fatih(a, "x" * 8000)
    with pytest.raises(TeamError, match="Frage.*8000"):
        store.ask_fatih(a, "x" * 8001)
    with pytest.raises(TeamError, match="Antwort.*7000"):
        store.answer_question(None, question["id"], "y" * 7001)
    assert store.questions(a) == [question] and store.pending(a) == []
    assert not store.db.in_transaction
    with pytest.raises(TeamError):
        store.ask_fatih(a, "")
    with pytest.raises(TeamError):
        store.answer_question(None, question["id"], "")
    result = store.answer_question(None, question["id"], "y" * 7000)
    assert_answer(store, a, result, "fatih-cli", "x" * 8000, "y" * 7000)
    assert len(result["message"]["body"]) <= 16000


@pytest.mark.parametrize("adapter", ["cli", "mcp"])
def test_legacy_long_question_is_answerable_without_changing_stored_text(team, adapter):
    store, _, a, b, _ = team
    question = store.ask_fatih(a, "Altfrage")
    legacy_text = "x" * 15950
    store.db.execute("UPDATE user_questions SET text=? WHERE id=?", (legacy_text, question["id"]))
    answer = "y" * 7000
    if adapter == "cli":
        response = cli(store, "antworte", "--frage", question["id"], "--text", answer)
        assert response.returncode == 0
        result = json.loads(response.stdout)
    else:
        result = MCP(store, b).call("team_answer_question", {"question": question["id"], "answer": answer})
    assert "[Frage gekürzt]" in result["message"]["body"]
    assert len(result["message"]["body"]) <= 16000
    assert f"Antwort: {answer}" in result["message"]["body"]
    assert ANSWER_NOTICE in result["message"]["body"]
    assert result["question"]["text"] == legacy_text
    assert store.questions(a)[0]["text"] == legacy_text
    assert store.pending(a) == [result["message"]]


@pytest.mark.parametrize("adapter", ["cli", "mcp"])
def test_answer_to_codex_queues_once_with_fake_program(team, tmp_path, monkeypatch, adapter):
    store, _, a, b, _ = team
    fake = tmp_path / "codex"
    output = tmp_path / "queue.jsonl"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                    f"with open({str(output)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    question = store.ask_fatih(b, "Liefertermin?")
    if adapter == "cli":
        args = ("antworte", "--frage", question["id"], "--text", "Montag", "--deliver-codex")
        response = cli(store, *args)
        assert response.returncode == 0
        result = json.loads(response.stdout)
        assert cli(store, *args).returncode == 2
    else:
        args = {"question": question["id"], "answer": "Montag"}
        mcp = MCP(store, a, deliver=True)
        result = mcp.call("team_answer_question", args)
        with pytest.raises(TeamError, match="beantwortet"):
            mcp.call("team_answer_question", args)
    assert result["message"]["delivery"] == "queue_accepted" and not result["message"]["acked"]
    queued = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(queued) == 1
    assert queued[0][:4] == ["queue", "--thread", store._participant(b)["thread"], "--message"]
    assert ANSWER_NOTICE in queued[0][4]
    store.deliver_codex(result["message"], command=str(fake))
    assert len(output.read_text().splitlines()) == 1


def test_channel_handover_and_answer_exactly_once_over_stdio(team, tmp_path):
    store, project, a, b, _ = team
    handover = store.handover(b, **handover_texts(fertig="Bereit"))
    store.handover(a, **handover_texts(fertig="Eigene, nicht melden"))
    other = tmp_path / "other"
    other.mkdir()
    outsider = ident()
    store.register(outsider, "Outsider", "claude", str(other))
    store.handover(outsider, **handover_texts(fertig="Fremdes Projekt"))
    question = store.ask_fatih(a, "Welche Variante?")
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", a, "--channel"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1)
        _line(proc)
        assert not select.select([proc.stdout], [], [], 0.3)[0]
        _send(proc, "notifications/initialized")
        notice = _line(proc)
        assert notice["method"] == "notifications/claude/channel"
        assert notice["params"]["meta"]["handover"] == handover["id"]
        assert HANDOVER_NOTICE in notice["params"]["content"]
        assert notice["params"]["meta"]["project"] == str(project)
        assert store._one("SELECT taken_by FROM handovers WHERE id=?", (handover["id"],))["taken_by"] is None
        assert not select.select([proc.stdout], [], [], 0.65)[0]
        answered = cli(store, "antworte", "--frage", question["id"], "--text", "Variante A")
        assert answered.returncode == 0
        message = json.loads(answered.stdout)["message"]
        notice = _line(proc)
        assert notice["method"] == "notifications/claude/channel"
        assert notice["params"]["meta"]["message_id"] == message["id"]
        assert ANSWER_NOTICE in notice["params"]["content"]
        assert "eingetragen von fatih-cli" in notice["params"]["content"]
        assert store.pending(a)[0]["acked"] is None
        _send(proc, "notifications/initialized")
        assert not select.select([proc.stdout], [], [], 0.65)[0]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0
        assert proc.stderr.read() == ""


def test_parallel_answers_create_one_complete_message(team):
    store, _, a, _, _ = team
    question = store.ask_fatih(a, "Entscheidung?")
    barrier = threading.Barrier(8)
    successes, failures, errors = [], [], []
    def answer():
        own = Store(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                successes.append(own.answer_question(None, question["id"], "Ja"))
            except TeamError:
                failures.append(True)
        except BaseException as exc:
            errors.append(exc)
        finally:
            own.close_db()
    jobs = [threading.Thread(target=answer) for _ in range(8)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(10)
    assert not any(job.is_alive() for job in jobs) and not errors
    assert len(successes) == 1 and len(failures) == 7
    assert_answer(store, a, successes[0], "fatih-cli", "Entscheidung?", "Ja")
    assert store.pending(a) == [successes[0]["message"]]
    assert len(store.status(a)["conversations"]) == 2  # fixture + one answer


@pytest.mark.parametrize("tool", ["team_handover", "team_handovers", "team_take_handover",
                                 "team_discard_handover", "team_ask_fatih", "team_questions", "team_answer_question"])
def test_new_tool_schemas_and_strict_argument_validation(tool):
    required, _ = TOOL_FIELDS[tool]
    valid = {field: "text" for field in required}
    validate_tool(tool, valid)
    schema = tool_schema(tool)["inputSchema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(required)
    assert set(schema["properties"]) == set(required)
    with pytest.raises(TeamError, match="unbekannte"):
        validate_tool(tool, {**valid, "participant": ident()})
    for field in required:
        with pytest.raises(TeamError, match="Fehlende"):
            validate_tool(tool, {key: value for key, value in valid.items() if key != field})
        with pytest.raises(TeamError, match="Typ"):
            validate_tool(tool, {**valid, field: 3})


def test_handover_cli_commands_end_to_end(team):
    store, _, a, b, _ = team
    args = ["handover", "--participant", a]
    for field, text in handover_texts(fertig="gebaut").items():
        args.extend(["--" + field.replace("_", "-"), text])
    created = cli(store, *args)
    assert created.returncode == 0
    handover = json.loads(created.stdout)
    assert json.loads(cli(store, "handovers", "--participant", b).stdout) == [handover]
    claimed = cli(store, "take-handover", "--participant", b, "--handover", handover["id"])
    assert claimed.returncode == 0 and json.loads(claimed.stdout)["taken_by"] == b
    assert json.loads(cli(store, "handovers", "--participant", b).stdout) == []


NEW_TRANSACTIONS = [
    ("migrate", "ALTER TABLE participants ADD COLUMN"),
    ("migrate", "CREATE TABLE IF NOT EXISTS handovers"),
    ("migrate", "CREATE TABLE IF NOT EXISTS user_questions"),
    ("migrate_discard", "ALTER TABLE handovers ADD COLUMN discarded"),
    ("touch", "UPDATE participants SET last_seen"),
    ("handover", "INSERT INTO handovers"),
    ("take", "UPDATE handovers SET taken_by"),
    ("discard", "UPDATE handovers SET taken_by"),
    ("ask", "INSERT INTO user_questions"),
    ("answer", "UPDATE user_questions SET answer"),
    ("answer", "INSERT INTO participants"),
    ("answer", "INSERT INTO conversations"),
    ("answer", "INSERT INTO messages"),
]


def new_transaction_case(store, a, b, operation):
    if operation == "migrate_discard":
        store.handover(a, **handover_texts())
        store.db.execute("ALTER TABLE handovers DROP COLUMN discarded")
        return store._migrate
    if operation == "migrate":
        store.db.execute("DROP TABLE handovers")
        store.db.execute("DROP TABLE user_questions")
        store.db.execute("ALTER TABLE participants DROP COLUMN last_seen")
        return store._migrate
    handover = store.handover(a, **handover_texts())
    question = store.ask_fatih(a, "Antwort nötig")
    return {
        "touch": lambda: store.touch(b),
        "handover": lambda: store.handover(a, **handover_texts(fertig="neu")),
        "take": lambda: store.take_handover(b, handover["id"]),
        "discard": lambda: store.discard_handover(a, handover["id"]),
        "ask": lambda: store.ask_fatih(a, "Neue Frage"),
        "answer": lambda: store.answer_question(None, question["id"], "Antwort"),
    }[operation]


def snapshot(connection):
    schema = {row["name"]: (row["type"], row["sql"]) for row in connection.execute("SELECT * FROM sqlite_master")}
    tables = {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
              for name, (kind, _) in schema.items() if kind == "table"}
    return schema, tables


@pytest.mark.parametrize("operation, mutation", NEW_TRANSACTIONS)
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("at_begin", [False, True])
def test_every_new_transaction_rolls_back_baseexception(team, operation, mutation, failure_type, at_begin):
    store, _, a, b, _ = team
    action = new_transaction_case(store, a, b, operation)
    before = snapshot(store.db)
    original = failure_type("new transaction interrupted")
    store.db = FaultConnection(store.db, "BEGIN IMMEDIATE" if at_begin else mutation, original)
    with pytest.raises(failure_type) as caught:
        action()
    assert caught.value is original
    assert store.db.interrupted and not store.db.in_transaction and not store._poisoned
    assert snapshot(store.db) == before  # Includes schema, question claim, participant, conversation, message.
    action()  # Same connection can perform the entire operation after cleanup.
    assert not store.db.in_transaction and snapshot(store.db) != before
    assert store.status(a)["participant"]["id"] == a


@pytest.mark.parametrize("operation, mutation", NEW_TRANSACTIONS)
def test_new_transactions_preserve_effect_after_commit_interrupt(team, operation, mutation):
    store, _, a, b, _ = team
    action = new_transaction_case(store, a, b, operation)
    before = snapshot(store.db)
    original = KeyboardInterrupt("after commit")
    store.db = FaultConnection(store.db, "COMMIT", original)
    with pytest.raises(KeyboardInterrupt) as caught:
        action()
    assert caught.value is original and store.db.interrupted
    assert not store.db.in_transaction and not store._poisoned
    observer = Store(store.path)
    try:
        assert snapshot(observer.db) == snapshot(store.db) != before
        assert store.pending(a) == observer.pending(a)
    finally:
        observer.close_db()


@pytest.mark.parametrize("operation, mutation", NEW_TRANSACTIONS)
def test_new_transactions_quarantine_after_rollback_failure(team, operation, mutation):
    store, _, a, b, _ = team
    action = new_transaction_case(store, a, b, operation)
    before = snapshot(store.db)
    # Raw observer avoids migrating the deliberately old schema under test.
    observer = sqlite3.connect(store.path)
    observer.row_factory = sqlite3.Row
    original = KeyboardInterrupt("original error")
    store.db = FaultConnection(store.db, mutation, original, sqlite3.OperationalError("rollback failed"))
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            action()
        assert caught.value is original and store._poisoned
        assert "Rollback fehlgeschlagen" in "\n".join(original.__notes__)
        assert snapshot(observer) == before
        with pytest.raises(TeamError, match="gesperrt"):
            store.pending(a)
        with pytest.raises(TeamError, match="gesperrt"):
            action()
    finally:
        observer.close()


def test_handover_mcp_tools_end_to_end(team):
    store, _, a, b, _ = team
    created = MCP(store, a).call("team_handover", handover_texts(fertig="Werkzeuge fertig"))
    receiver = MCP(store, b)
    assert receiver.call("team_handovers", {}) == [created]
    claimed = receiver.call("team_take_handover", {"handover": created["id"]})
    assert claimed["taken_by"] == b and claimed["taken_at"]
    assert receiver.call("team_handovers", {}) == []
    with pytest.raises(TeamError):
        receiver.call("team_take_handover", {"handover": created["id"]})


def test_answer_deliver_option_requires_thread_before_claim(team, capsys):
    store, project, a, _, _ = team
    no_thread = ident()
    store.register(no_thread, "No thread", "codex", str(project))
    question = store.ask_fatih(no_thread, "Termin?")
    args = {"question": question["id"], "answer": "Montag"}
    with pytest.raises(TeamError, match="Thread-ID"):
        MCP(store, a, deliver=True).call("team_answer_question", args)
    assert main(["--db", str(store.path), "antworte", "--frage", question["id"],
                 "--text", "Montag", "--deliver-codex"]) == 2
    assert "Thread-ID" in json.loads(capsys.readouterr().err)["error"]
    assert store.questions(no_thread) == [question] and store.pending(no_thread) == []
    result = MCP(store, a).call("team_answer_question", args)
    assert_answer(store, no_thread, result, a, "Termin?", "Montag")
    assert result["message"]["delivery"] == "stored"


def test_answer_queue_failure_visible_over_stdio(team, tmp_path):
    store, _, a, b, _ = team
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\nexit 8\n")
    fake.chmod(0o700)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    question = store.ask_fatih(b, "Sachfrage")
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", a, "--deliver-codex"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        _send(proc, "initialize", 1)
        _line(proc)
        _send(proc, "tools/call", 2, {"name": "team_answer_question", "arguments": {
            "question": question["id"], "answer": "Sachantwort"}})
        response = _line(proc)["result"]
        assert response["isError"] is True
        result = json.loads(response["content"][0]["text"])
        assert result["message"]["delivery"] == "queue_failed"
        assert result["message"]["delivery_error"] and not result["message"]["acked"]
        assert_answer(store, b, result, a, "Sachfrage", "Sachantwort")
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0 and proc.stderr.read() == ""


def test_migration_constructor_close_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    path.touch(mode=0o600)
    raw = sqlite3.connect(path, isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE participants (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
                "project TEXT NOT NULL, thread TEXT)")
    original = KeyboardInterrupt("migration error")
    connection = FaultConnection(raw, "ALTER TABLE participants", original,
                                 close_error=RuntimeError("close failed"))
    monkeypatch.setattr("teamkanal.sqlite3.connect", lambda *args, **kwargs: connection)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            Store(path)
        assert caught.value is original and not raw.in_transaction
        assert "Schließen nach Migration fehlgeschlagen" in "\n".join(original.__notes__)
        assert "last_seen" not in {r["name"] for r in raw.execute("PRAGMA table_info(participants)")}
    finally:
        raw.close()


def test_question_answer_schema_limits():
    question = tool_schema("team_ask_fatih")["inputSchema"]["properties"]["text"]
    answer = tool_schema("team_answer_question")["inputSchema"]["properties"]["answer"]
    assert question == {"type": "string", "minLength": 1, "maxLength": 8000}
    assert answer == {"type": "string", "minLength": 1, "maxLength": 7000}


@pytest.mark.parametrize("adapter", ["cli", "mcp"])
def test_discard_handover_sender_only_once_and_never_taken(team, tmp_path, adapter):
    store, _, a, b, _ = team
    handover = store.handover(a, **handover_texts(fertig="veraltet"))
    before = snapshot(store.db)
    with pytest.raises(TeamError, match="fremde"):
        store.discard_handover(b, handover["id"])
    assert snapshot(store.db) == before and not store.db.in_transaction
    other_project = tmp_path / "other"
    other_project.mkdir()
    outsider = ident()
    store.register(outsider, "Other", "claude", str(other_project))
    with pytest.raises(TeamError):
        store.discard_handover(outsider, handover["id"])
    assert store.handovers(a) == [handover]
    if adapter == "cli":
        response = cli(store, "discard-handover", "--participant", a, "--handover", handover["id"])
        assert response.returncode == 0
        discarded = json.loads(response.stdout)
        repeated = cli(store, "discard-handover", "--participant", a, "--handover", handover["id"])
        assert repeated.returncode == 2 and "verworfen" in json.loads(repeated.stderr)["error"]
    else:
        discarded = MCP(store, a).call("team_discard_handover", {"handover": handover["id"]})
        with pytest.raises(TeamError, match="verworfen"):
            MCP(store, a).call("team_discard_handover", {"handover": handover["id"]})
    assert discarded == {**handover, "discarded": 1, "taken_by": a, "taken_at": discarded["taken_at"]}
    assert discarded["taken_at"]
    assert store.handovers(a) == store.handovers(b) == []
    with pytest.raises(TeamError):
        store.take_handover(b, handover["id"])
    claimed = store.handover(a, **handover_texts())
    taken = store.take_handover(b, claimed["id"])
    with pytest.raises(TeamError):
        store.discard_handover(a, claimed["id"])
    assert store._one("SELECT * FROM handovers WHERE id=?", (claimed["id"],)) == taken
    assert taken["discarded"] == 0 and not store.db.in_transaction


def test_discarded_handover_not_notified_by_new_stdio_channel(team):
    store, _, a, b, _ = team
    handover = store.handover(a, **handover_texts())
    store.discard_handover(a, handover["id"])
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--db", str(store.path), "mcp",
                             "--participant", b, "--channel"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        _send(proc, "initialize", 1)
        assert "team_discard_handover" in _line(proc)["result"]["instructions"]
        _send(proc, "notifications/initialized")
        assert not select.select([proc.stdout], [], [], 0.8)[0]
        # A later live handover proves the actual poller is running.
        live = store.handover(a, **handover_texts(fertig="aktuell"))
        notice = _line(proc)
        assert notice["method"] == "notifications/claude/channel"
        assert notice["params"]["meta"]["handover"] == live["id"]
        assert not select.select([proc.stdout], [], [], 0.65)[0]
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=4) == 0 and proc.stderr.read() == ""


def test_migrate_t3_handover_schema_preserves_all_data(team):
    store, _, a, b, conv = team
    open_handover = store.handover(a, **handover_texts(fertig="offen"))
    taken_handover = store.handover(a, **handover_texts(fertig="übernommen"))
    store.take_handover(b, taken_handover["id"])
    store.send(a, conv, "note", "vor Migration")
    question = store.ask_fatih(b, "offene Frage")
    store.db.execute("ALTER TABLE handovers DROP COLUMN discarded")
    before_schema, before_tables = snapshot(store.db)
    migrated = Store(store.path)
    try:
        after_schema, after_tables = snapshot(migrated.db)
        assert {name: sql for name, sql in after_schema.items() if name != "handovers"} == {
            name: sql for name, sql in before_schema.items() if name != "handovers"}
        assert after_tables["handovers"] == [row + (0,) for row in before_tables["handovers"]]
        assert {name: rows for name, rows in after_tables.items() if name != "handovers"} == {
            name: rows for name, rows in before_tables.items() if name != "handovers"}
        column = next(row for row in migrated.db.execute("PRAGMA table_info(handovers)") if row["name"] == "discarded")
        assert column["type"] == "INTEGER" and column["notnull"] == 1 and column["dflt_value"] == "0"
        assert migrated.handovers(b) == [open_handover]
        assert migrated.questions(b) == [question]
        after = snapshot(migrated.db)
        migrated._migrate()
        assert snapshot(migrated.db) == after
        migrated.discard_handover(a, open_handover["id"])
        assert migrated.handovers(b) == []
    finally:
        migrated.close_db()


@pytest.mark.parametrize("compete_with_take", [False, True])
def test_discard_claim_once_across_independent_connections(team, compete_with_take):
    store, _, a, b, _ = team
    handover = store.handover(a, **handover_texts())
    barrier = threading.Barrier(8)
    successes, failures, errors = [], [], []
    def claim(index):
        own = Store(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                if compete_with_take and index % 2:
                    successes.append(own.take_handover(b, handover["id"]))
                else:
                    successes.append(own.discard_handover(a, handover["id"]))
            except TeamError:
                failures.append(index)
        except BaseException as exc:
            errors.append(exc)
        finally:
            own.close_db()
    jobs = [threading.Thread(target=claim, args=(index,)) for index in range(8)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(10)
    assert not any(job.is_alive() for job in jobs) and not errors
    assert len(successes) == 1 and len(failures) == 7
    assert successes[0]["taken_by"] in (a, b)
    assert successes[0]["discarded"] == (successes[0]["taken_by"] == a)
    assert store.handovers(a) == store.handovers(b) == []

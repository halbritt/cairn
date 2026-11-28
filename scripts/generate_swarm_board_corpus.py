#!/usr/bin/env python3
"""Regenerate core/testdata/swarm_board_cards.json from the pinned board code."""

import argparse
import ast
import json
import random
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

BOARD_REVISION = "5ccacf9"
RANDOM_SEED = 8319
PUBLISHED = datetime(2026, 11, 27, 20, tzinfo=timezone.utc)
FIXTURE = Path(__file__).resolve().parents[1] / "core/testdata/swarm_board_cards.json"


def load_board_module(checkout, revision, name):
    path = f"server/{name}.py"
    source = subprocess.check_output(["git", "-C", str(checkout), "show", f"{revision}:{path}"])
    module = ModuleType(name)
    exec(compile(source, path, "exec"), module.__dict__)
    # Read at publication to align the board and Cairn cap clocks.
    module.time = SimpleNamespace(time=PUBLISHED.timestamp)
    return module


def note(card):
    return "Choose a setting\n\n```board-card\n" + json.dumps(card, ensure_ascii=False) + "\n```"


def card_cases():
    yes = {"id": "yes", "label": "Proceed"}
    snooze = {"id": "later", "label": "Later", "snooze": 60}
    reviewer_default = {"ttl": 3600, "shape": "question", "verbs": [yes], "default": "missing"}
    cases = {
        "empty_card": {},
        "reviewer_epoch_1e_10": {"ttl": 86400, "expires_at": 1e-10},
        "reviewer_epoch_1e_12": {"ttl": 86400, "expires_at": 1e-12},
        "reviewer_epoch_4_9e_10": {"ttl": 86400, "expires_at": 4.9e-10},
        "epoch_subsecond": {"ttl": 86400, "expires_at": 0.5},
        "epoch_six_fraction_digits": {"ttl": 86400, "expires_at": 1.123456},
        "epoch_seven_fraction_digits": {"ttl": 86400, "expires_at": 1.1234567},
        "epoch_year_2100": {"ttl": 86400, "expires_at": 4102444800},
        "numeric_epoch_zero_ttl_86400": {"ttl": 86400, "expires_at": 0},
        "string_epoch_zero_ttl_86400": {"ttl": 86400, "expires_at": "1970-01-01T00:00:00Z"},
        "reviewer_ambiguous_week_date": {"ttl": 3600, "observed_at": "2026-W48-5012Z"},
        "reviewer_numeric_slot": {"ttl": 172800, "slot": 10.2},
        "non_object": [],
        "valid_default": {"shape": " QUESTION ", "verbs": [yes, snooze], "default": "yes"},
        "null_severity": {"severity": None},
        "unknown_shape": {"shape": "future-shape"},
        "all_typed_fields": {
            "severity": "warn", "observed_at": "2026-11-27T20Z", "until": "2026-11-27T21Z",
            "start": "2026-11-27T22Z", "end": "2026-11-27T23Z", "progress": 0.25,
            "items": ["one", {"label": "two", "done": True}], "series": [1, 2],
            "value": 1, "unit": "kV", "label": "Test", "all_day": True, "location": "Lab",
        },
        "slot_cleanup": {"slot": "  Ångström\t v1 / π² - _.  "},
        "slot_truncation": {"slot": "界" * 41 + "suffix"},
        "slot_empty_after_clean": {"slot": "   "},
        "slot_integer": {"slot": 42},
        "slot_boolean": {"slot": True},
        "slot_false": {"slot": False},
        "ttl_number": {"ttl": 1.5},
        "ttl_last_microsecond_below_cap": {"ttl": 259199.999999},
        "ttl_first_microsecond_above_cap": {"ttl": 259200.000001},
        "ttl_last_microsecond_below_minimum": {"ttl": 0.999999},
        "snooze_last_microsecond_below_minimum": {"verbs": [dict(snooze, snooze=59.999999)]},
        "snooze_first_microsecond_above_minimum": {"verbs": [dict(snooze, snooze=60.000001)]},
        "progress_last_microsecond_below_maximum": {"progress": 0.999999},
        "progress_first_microsecond_above_maximum": {"progress": 1.000001},
        "ttl_lower_bound": {"ttl": 1},
        "ttl_capped": {"ttl": 999999},
        "ttl_string": {"ttl": "3600"},
        "ttl_boolean": {"ttl": True},
        "ttl_zero": {"ttl": 0},
        "ttl_negative": {"ttl": -1},
        "ttl_nan": {"ttl": float("nan")},
        "ttl_infinity": {"ttl": float("inf")},
        "reviewer_invalid_default": reviewer_default,
        "default_without_verbs": {"default": "yes"},
        "default_null": {"verbs": [yes], "default": None},
        "default_snooze": {"verbs": [yes, snooze], "default": "later"},
        "question_without_verbs": {"shape": "question"},
        "severity_invalid": {"severity": "critical"},
        "verbs_wrong_type": {"verbs": {}},
        "verbs_empty": {"verbs": []},
        "verbs_too_many": {"verbs": [yes, snooze, {"id": "no", "label": "No"},
                                    {"id": "change", "label": "Change"}]},
        "verb_not_object": {"verbs": ["yes"]},
        "verb_invalid_id": {"verbs": [dict(yes, id="Yes")]},
        "verb_long_id": {"verbs": [dict(yes, id="a" * 25)]},
        "verb_duplicate_id": {"verbs": [yes, yes]},
        "verb_missing_label": {"verbs": [dict(yes, label=" \t")]},
        "verb_numeric_id": {"verbs": [dict(yes, id=7)], "default": "7"},
        "snooze_wrong_type": {"verbs": [dict(snooze, snooze="60")]},
        "snooze_too_short": {"verbs": [dict(snooze, snooze=59)]},
        "snooze_too_long": {"verbs": [dict(snooze, snooze=86401)]},
        "snooze_maximum": {"verbs": [dict(snooze, snooze=86400)]},
        "progress_wrong_type": {"progress": "0.5"},
        "progress_below_range": {"progress": -0.25},
        "progress_above_range": {"progress": 1.25},
        "items_wrong_type": {"items": "item"},
        "items_too_many": {"items": ["item"] * 13},
        "item_wrong_type": {"items": [1]},
        "item_missing_label": {"items": [{"label": " \t"}]},
        "series_wrong_type": {"series": "1,2"},
        "series_too_short": {"series": [1]},
        "series_too_long": {"series": [1] * 201},
        "series_invalid_point": {"series": [1, "2"]},
        "series_nonfinite_point": {"series": [1, float("nan")]},
    }
    for field in ("observed_at", "until", "start", "end"):
        cases[f"{field}_invalid"] = {field: "yesterday", "ttl": 3600}
    for name, timestamp in {
        "reviewer_compact_offset": "2026-11-29T20:00:00+0000",
        "offset_colon": "2026-11-27T13:00:00-08:00",
        "offset_minutes": "2026-11-27T13:30:00-07:30",
        "offset_hours": "2026-11-27T22:00:00+01",
        "offset_seconds": "2026-11-27T22:01:02+01:01:02",
        "offset_compact_fraction": "2026-11-27T22:01:02.123456+010102.123456",
        "offset_minute_carry": "2026-11-27T22:00+00:60",
        "offset_zero_components": "2026-11-27T21:00+00:00:00.5",
        "utc_z": "2026-11-27T21:00:00Z",
        "space_separator": "2026-11-27 21:00:00+00:00",
        "unicode_separator": "2026-11-27⌚21:00:00Z",
        "seconds_omitted": "2026-11-27T21:00+00:00",
        "minutes_omitted": "2026-11-27T21+00:00",
        "fraction_dot": "2026-11-27T21:00:00.123456789Z",
        "fraction_comma": "2026-11-27T21:00:00,12Z",
        "basic_date_and_clock": "20261127T210000Z",
        "basic_partial_clock": "20261127T2100Z",
        "week_date": "2026-W48-5T21:00:00Z",
        "basic_week_date": "2026W485T21:00:00Z",
        "week_date_without_day": "2026-W48T21:00:00Z",
        "epoch": PUBLISHED.timestamp() + 3600,
        "integer_epoch": int(PUBLISHED.timestamp()) + 3600,
        "fractional_epoch": PUBLISHED.timestamp() + 3600.25,
        "epoch_zero_uses_ttl": 0,
        "iso_epoch_zero_uses_ttl": "1970-01-01T00:00:00Z",
        "iso_before_epoch": "1969-12-31T23:59:59Z",
        "capped_absolute": PUBLISHED.timestamp() + 500000,
        "offset_missing": "2026-11-27T21:00:00",
        "invalid_offset": "2026-11-27T21:00:00+24:00",
        "invalid_calendar_day": "2026-11-31T21:00:00Z",
        "invalid_leap_second": "2026-11-27T21:00:60Z",
        "invalid_fraction_after_minutes": "2026-11-27T21:00.5Z",
        "negative_epoch": -1,
        "epoch_string": str(PUBLISHED.timestamp()),
        "null": None,
    }.items():
        cases[f"expires_{name}"] = {"expires_at": timestamp, "ttl": 60}

    bodies = {name: note(card) for name, card in cases.items()}
    for name, spelling in {"negative_zero_exponent": "-0e-300", "zero_extra_precision": "0.0000000"}.items():
        bodies[name] = bodies["numeric_epoch_zero_ttl_86400"].replace('"expires_at": 0', f'"expires_at": {spelling}')
    bodies.update({
        "no_block": "Question without a card",
        "not_json": "Question\n```board-card\n{broken\n```",
        "empty_block": "Question\n```board-card\n\n```",
        "trailing_json_value": "Question\n```board-card\n{} {}\n```",
        "crlf_is_not_a_fence": note({"ttl": 60}).replace("\n", "\r\n"),
        "two_blocks": note({"ttl": 120}) + "\n" + note({"ttl": 60}),
        "invalid_first_block": "Question\n```board-card\n{broken\n```\n" + note({"ttl": 60}),
        "fence_whitespace_and_multiline": "Question\n```board-card \t\n{\n \"ttl\":120\n}\n``` \t\n",
        "fence_not_at_line_start": "Question ```board-card\n{\"ttl\":60}\n```",
        "closing_fence_not_alone": "Question\n```board-card\n{\"ttl\":60}\n``` words",
    })
    return bodies


def documented_cases():
    """Ordinary examples for each shape and the fields in docs/agents.md."""
    return {
        "docs_message": note({"shape": "message", "severity": "notice", "slot": "backup"}),
        "docs_question": note({
            "shape": "question", "ttl": 7200, "default": "no",
            "verbs": [{"id": "open", "label": "Open it", "confirm": True, "style": "primary"},
                      {"id": "no", "label": "Not today"},
                      {"id": "later", "label": "Ask at 6", "snooze": 3600}],
        }),
        "docs_stat": note({"shape": "stat", "value": 31, "unit": "°C", "severity": "info"}),
        "docs_countdown": note({"shape": "countdown", "until": "2026-11-27T22:00:00Z"}),
        "docs_progress": note({"shape": "progress", "progress": 0.5, "expires_at": "2026-11-29T20:00:00+0000"}),
        "docs_trend": note({"shape": "trend", "series": [0, 0.5, 2], "value": 2, "unit": "kV"}),
        "docs_checklist": note({"shape": "checklist", "items": [{"id": "vent", "label": "Close the vent", "done": True}]}),
        "docs_event": note({"shape": "event", "start": "2026-11-29T12:00:00-08:00",
                            "end": "2026-11-29T12:30:00.123456-08:00", "all_day": False, "location": "Garden"}),
    }


def random_cases():
    """Exercise supported forms, coercions, bounds and interactions reproducibly."""
    rng = random.Random(RANDOM_SEED)
    edges = [0, -0.0, 1e-12, 1e-10, 4.9e-10, 0.000001, 0.5, 1.000001, 1.0000001,
             4102444799.999999, 4102444800, 1e20, -1e120]
    stamp = ["2026-11-29T21:00:00.125-01:00", "2026-11-29T20:00:00+0000",
             "2026-11-29⌚20:00:00Z", "2026-W48-5012Z", "20261129T2000Z",
             "2026-11-29T21:00:00+01", "2026-11-29 20:00:00Z",
             PUBLISHED.timestamp() + 172800, -1, None, [], *edges]
    yes = {"id": "yes", "label": "Proceed"}
    values = {
        "shape": ["message", " Question ", "event", "QUESTİON", 0, []],
        "severity": ["info", "notice", "warn", "urgent", "urgent!", None, 1],
        "ttl": [1, 1.5, 45.125, 3600, 172800, 259199, 999999, 0, -1, True, "3600", 1e99, float("nan"), *edges],
        "expires_at": stamp,
        "slot": ["settings", "   ", "a / _œ ", "A / .", "x" * 50, 10.2, False, [], ["a"], {"x": 1}, *edges],
        "verbs": [[yes], [yes, yes], [], {}, [dict(yes, id=7)], [dict(yes, label="Go π")],
                  [dict(yes, snooze=60)], [dict(yes, snooze=59)], [dict(yes, snooze=True)],
                  [dict(yes, snooze=86400)], [dict(yes, label=False)], [dict(yes, label=" ")],
                  [dict(yes, confirm=["x"], style=3)],
                  *([dict(yes, snooze=n)] for n in edges),
                  *([dict(yes, confirm=n)] for n in edges)],
        "default": ["yes", "missing", "", "π", 1, None],
        "progress": [0, 0.5, 1, 1.05, -0.01, True, "0.5", float("inf"), *edges],
        "items": [[], ["okay"], [{"label": "yes", "done": False}], [""], ["π"],
                  [{"label": True}], [1], {}, ["x"] * 13,
                  *([{"label": "yes", "done": n}] for n in edges)],
        "series": [[0, 1], [0.125, 1, 50], [], [1], [False, 1], ["1", 2], [float("nan"), 1], *([n, 1] for n in edges)],
        "value": [1.3, "42", "µs", None, *edges],
        "unit": ["V", "Ω", 2, None],
        "label": ["Readout", "π", True, {}],
        "all_day": [True, False, 5, {}, *edges],
        "location": ["Garden", "à l’étage", 1, []],
        "ignored": ["example", None, float("nan"), ["question"]],
    }
    values.update({field: stamp for field in ("observed_at", "until", "start", "end")})
    seen = set()
    while len(seen) < 2200:
        fields = rng.sample(list(values), rng.choices((1, 2, 3), (4, 5, 1))[0])
        card = {field: rng.choice(values[field]) for field in sorted(fields)}
        body = note(card)
        if body not in seen:
            seen.add(body)
            yield body


def case_result(cards, name, body, documented=False):
    title, detail, card, _ = cards.parse_note(body)
    item = cards.normalize({
        "id": name or "generated", "title": title, "detail": detail, "kind": "attention",
        "card": card, "card_trusted": True, "at": PUBLISHED.timestamp(), "ttl_from_at": True,
    })
    return {
        **({"name": name} if name else {}), "body": body, "valid": bool(card),
        "expires_epoch": item["expires"],
        "default_declared": "default" in card, "slot": card.get("slot", ""),
        **({"documented": True} if documented else {}),
    }


def board_mutations(checkout, revision, cards):
    """Use the board's real mutations without importing its hardware stack."""
    path = "server/board_server.py"
    source = subprocess.check_output(["git", "-C", str(checkout), "show", f"{revision}:{path}"])
    tree = ast.parse(source, filename=path)
    board = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Board")
    names = {"upsert", "delete", "answer"}
    methods = [node for node in board.body if isinstance(node, ast.FunctionDef) and node.name in names]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(name, ast.Name) and name.id in {"UNDO_WINDOW", "ANSWER_RETENTION"}
                         for name in node.targets)]
    namespace = {"normalize": cards.normalize, "time": cards.time, "uuid": uuid}
    exec(compile(ast.Module(body=constants + methods, type_ignores=[]), path, "exec"), namespace)
    return {name: namespace[name] for name in names}


def slot_cases(messages, mutations):
    first, second = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    class Board:
        def __init__(self):
            self.items = {}
            self.lock = threading.RLock()

        def _save(self):
            pass

    for method, implementation in mutations.items():
        setattr(Board, method, implementation)

    def run(name, events, answered=(), retained=()):
        events = [{"id": key, "sender": sender, "kind": kind, "body": note(card), "age_seconds": age}
                  for key, sender, kind, card, age in events]
        notes = {event["id"]: event["body"] for event in events}
        page = [{
            "event_id": event["id"], "from": "agent/" + event["sender"], "kind": event["kind"],
            "position": position, "ref": {"record_id": event["id"], "version": 1},
            "created_at": (PUBLISHED - timedelta(seconds=event["age_seconds"])).isoformat(),
        } for position, event in enumerate(events, 1)]

        class Poll(messages.CairnMessages):
            def _position(self):
                return 0

            def _save_position(self, position):
                pass

            def _run(self, args, payload=None, text=None):
                if args[0] == "events":
                    return {"events": page, "more": False}
                if args[-1] == "history":
                    return {"versions": [{"body": notes[payload["record_id"]]}]}
                if args[:2] == ["agents", "list"]:
                    return {"agents": []}
                raise AssertionError(f"Unexpected board command: {args}")

        board = Board()
        if Poll(board, "unused", "unused", "unused").poll() != len(page):
            raise AssertionError("Board poll did not consume the fixture page")
        for event_id in answered:
            item = next(item for item in board.items.values() if item["event"]["id"] == event_id)
            board.answer(item["id"], "yes")
        live = sorted(item["event"]["id"] for item in board.items.values()
                      if item["kind"] == "attention" and not item.get("answer"))
        return {"name": name, "events": events, "live_requests": live,
                "answered_requests": answered, "retain_conservatively": retained}

    slot = {"slot": "settings"}
    return [
        run("sender_scoped_replacement", [
            ("prior", first, "request", slot, 0),
            ("independent", second, "request", slot, 0),
            ("replacement", first, "request", slot, 0),
            ("invalid_card", first, "request", dict(slot, default="invalid"), 0),
        ]),
        run("notice_replaces_request", [
            ("prior", first, "request", slot, 0),
            ("replacement", first, "notice", slot, 0),
        ]),
        run("replacement_answered", [
            ("prior", first, "request", slot, 0),
            ("replacement", first, "request", dict(slot, verbs=[{"id": "yes", "label": "Yes"}]), 0),
        ], answered=("replacement",)),
        run("uncertain_replacement_keeps_prior", [
            ("prior", first, "request", slot, 0),
            ("replacement", first, "request", dict(slot, observed_at="2026-W48-5T20:00:00Z"), 0),
        ], retained=("prior",)),
        run("certain_replacement_keeps_uncertain_prior", [
            ("prior", first, "request", dict(slot, observed_at="2026-W48-5T20:00:00Z"), 0),
            ("replacement", first, "request", slot, 0),
        ], retained=("prior",)),
        run("expired_replacement_does_not_restore_prior", [
            ("prior", first, "request", slot, 120),
            ("expired", first, "request", dict(slot, ttl=1), 60),
        ]),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board_checkout", type=Path)
    args = parser.parse_args()
    commit = subprocess.check_output(
        ["git", "-C", str(args.board_checkout), "rev-parse", f"{BOARD_REVISION}^{{commit}}"], text=True,
    ).strip()
    cards = load_board_module(args.board_checkout, commit, "cards")
    sys.modules["cards"] = cards
    messages = load_board_module(args.board_checkout, commit, "cairn_messages")
    cases = [case_result(cards, name, body) for name, body in card_cases().items()]
    cases.extend(case_result(cards, name, body, documented=True) for name, body in documented_cases().items())
    cases.extend(case_result(cards, None, body) for body in random_cases())
    slots = slot_cases(messages, board_mutations(args.board_checkout, commit, cards))

    def compact(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    with FIXTURE.open("w", encoding="utf-8") as fixture:
        fixture.write('{"board_commit":' + compact(commit) + ', "published_at":' + compact(PUBLISHED.isoformat())
                      + ', "random_seed":' + str(RANDOM_SEED) + ',\n"cards":[\n')
        fixture.write(",\n".join(compact(case) for case in cases))
        fixture.write('\n],\n"slots":' + json.dumps(slots, ensure_ascii=False, indent=2) + '\n}\n')


if __name__ == "__main__":
    main()

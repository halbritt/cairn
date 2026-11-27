#!/usr/bin/env python3
"""Regenerate core/testdata/swarm_board_cards.json from the pinned board code."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

BOARD_REVISION = "5ccacf9"
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


def case_result(cards, name, body):
    title, detail, card, _ = cards.parse_note(body)
    item = cards.normalize({
        "id": name, "title": title, "detail": detail, "kind": "attention",
        "card": card, "card_trusted": True, "at": PUBLISHED.timestamp(), "ttl_from_at": True,
    })
    return {
        "name": name, "body": body, "valid": bool(card),
        "lifetime_seconds": item["expires"] - PUBLISHED.timestamp(),
        "default_declared": "default" in card, "slot": card.get("slot", ""),
    }


def slot_cases(cards, messages):
    first, second = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    class Board:
        def __init__(self):
            self.items = {}

        def upsert(self, raw):
            item = cards.normalize(raw)
            self.items[item["id"]] = item
            return item

        def delete(self, item_id):
            self.items.pop(item_id, None)

    def run(name, events):
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
        live = sorted(item["event"]["id"] for item in board.items.values() if item["kind"] == "attention")
        return {"name": name, "events": events, "live_requests": live}

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
    data = {
        "board_commit": commit,
        "published_at": PUBLISHED.isoformat(),
        "cards": [case_result(cards, name, body) for name, body in card_cases().items()],
        "slots": slot_cases(cards, messages),
    }
    FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

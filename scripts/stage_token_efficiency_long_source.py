#!/usr/bin/env python3
"""Prepare the long-source actor packet without launching an actor or provider."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import ordinary_task_receipt as receipt
import preflight_token_efficiency_panel as panel


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def private_dir(path):
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700, "owner-only directory required")


def private_file(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o600, "owner-only file required")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=panel.pairs_unique)


def frozen_inputs(export, workflow_id):
    private_dir(export)
    private_dir(export.parent)
    private_dir(export / "actor")
    controller_path = export / "controller.json"
    actor_path = export / "actor" / "long_source.json"
    private_file(controller_path)
    private_file(actor_path)
    fixture, fixture_hash = panel.read_fixture()
    controller = read_json(controller_path)
    require(controller == panel.manifest(fixture, fixture_hash), "controller export drift")
    binding = receipt.frozen_binding(controller, workflow_id)
    require(binding["case"] == "long_source", "long-source workflow required")
    row = next(row for row in controller["cases"] if row["id"] == "long_source")
    actor = read_json(actor_path)
    require(actor == row["actor"] and set(actor) == {"schema", "case", "task"},
            "actor export drift")
    setup = row["setup"]
    require(setup["sources"] == ["relay_history", "relay_current"]
            and setup["transitions"] == [{"phase": "before_read", "action": "repeat_source",
                                           "target": "relay_history", "count": 120}],
            "long-source transition drift")
    return controller, actor, binding


def actor_packet(controller, actor, binding):
    history_id, current_id = "relay_history", "relay_current"
    sources = controller["sources"]
    source_hashes = controller["source_body_sha256"]
    record_hashes = controller["source_record_sha256"]
    sections = []
    for position, source_id in enumerate([history_id] * 120 + [current_id], start=1):
        source = sources[source_id]
        body = source["body"]
        require(hashlib.sha256(body.encode()).hexdigest() == source_hashes[source_id]
                and panel.sha(source) == record_hashes[source_id], "source hash drift")
        sections.append({"position": position, "source_id": source_id,
                         "source_extent": "whole", "source_body_sha256": source_hashes[source_id],
                         "source_record_sha256": record_hashes[source_id], "body": body})
    exposed = actor["task"] + "\n" + "\n".join(item["body"] for item in sections)
    check = next(row["acceptance_check"] for row in controller["cases"]
                 if row["id"] == "long_source")
    require(all(clause.casefold() not in exposed.casefold()
                for clauses in check.values() for clause in clauses),
            "hidden check leaked into actor packet")
    return {"schema": "cairn.long-source-actor-packet/1", "workflow_id": binding["workflow_id"],
            "fixture_sha256": binding["fixture_sha256"],
            "actor_sha256": binding["actor_sha256"], "setup_sha256": binding["setup_sha256"],
            "task": actor["task"], "sections": sections}


def preparation_receipt(packet_bytes, binding):
    return {"schema": "cairn.long-source-preparation/1",
            "workflow_id": binding["workflow_id"], "fixture_sha256": binding["fixture_sha256"],
            "actor_packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
            "actor_packet_bytes": len(packet_bytes), "historical_whole": 120,
            "current_whole": 1, "evidence_status": "prepared_unread",
            "provider_called": False, "native_turn_join": "unimplemented",
            "independent_task_acceptance": "unassessed"}


def verify(export, workflow_id, output, disposable_root):
    root, export, output = Path(disposable_root), Path(export), Path(output)
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres.")
            and export.parent.parent == root and output.parent == root,
            "disposable packet paths required")
    private_dir(root)
    private_dir(output)
    private_dir(output / "actor")
    packet_path, receipt_path = output / "actor/long_source.json", output / "receipt.json"
    private_file(packet_path)
    private_file(receipt_path)
    controller, actor, binding = frozen_inputs(export, workflow_id)
    packet_bytes = packet_path.read_bytes()
    require(json.loads(packet_bytes, object_pairs_hook=panel.pairs_unique) ==
            actor_packet(controller, actor, binding), "actor packet source or extent drift")
    report = preparation_receipt(packet_bytes, binding)
    require(read_json(receipt_path) == report, "preparation receipt drift")
    return report


def stage(export, workflow_id, output, disposable_root):
    root = Path(disposable_root)
    require(root.parent == Path("/tmp") and root.name.startswith("cairn-postgres."),
            "disposable root required")
    private_dir(root)
    export = Path(export)
    require(export.parent.parent == root, "panel export must be under disposable root")
    output = Path(output)
    require(output.parent == root and output.name not in ("", ".", ".."),
            "packet output must be a new direct child of disposable root")
    controller, actor, binding = frozen_inputs(export, workflow_id)
    packet = actor_packet(controller, actor, binding)
    packet_bytes = (json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n").encode()
    receipt_data = preparation_receipt(packet_bytes, binding)
    output.mkdir(mode=0o700)
    try:
        actor_dir = output / "actor"
        actor_dir.mkdir(mode=0o700)
        packet_path = actor_dir / "long_source.json"
        descriptor = os.open(packet_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(packet_bytes)
        panel.write_new(output / "receipt.json", receipt_data)
        verify(export, workflow_id, output, root)
    except BaseException:
        shutil.rmtree(output)
        raise
    return receipt_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = stage(args.export, args.workflow_id, args.output,
                   os.environ["CAIRN_DISPOSABLE_TEST_ROOT"])
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()

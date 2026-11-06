#!/usr/bin/env python3
"""Actual WC05 operator browser journey with a synthetic subprocess provider.

Run with the UIPass Python environment against an immutable complete candidate.
No Cairn response, Store operation, authentication or feature gate is mocked.
The selected provider executable is synthetic; no model/network request occurs.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import work_coordination_ui_fixture as base


def advice_fixture():
    class AdviceFixture(base.Fixture):
        states = ("advice_workflow", "advice_empty", "advice_refusals", "advice_currentness", "advice_disabled", "advice_retries", "advice_conflicts", "advice_navigation", "advice_polling")

        def __init__(self, args):
            super().__init__(args)
            self.report["scope"] = "Real Cairn CLI/API/Store/browser and actual registered synthetic subprocess; no native/model/subscription claim."
            self.report["native_provider_generation"] = False
            self.report["model_usefulness_measured"] = False
            self.report["fixture_inputs"][".uipass-work-advice.yaml"] = hashlib.sha256((ROOT / ".uipass-work-advice.yaml").read_bytes()).hexdigest()
            self.case = {}
            self.provider_runs = {}

        def item_count(self, name):
            return 2

        def advice(self, *args, expected="OK"):
            if args and args[0] in ("profile", "control", "reconcile"):
                return self.host.command(["work", "advice", *args], expected=expected)
            return self.work("advice", *args, expected=expected)

        def invocation_rows(self, home=None):
            path = (home or self.provider_home) / "invocations.jsonl"
            return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

        def mode(self, mode, **values):
            base.dump(self.provider_home / "synthetic-fixture.json", dict(schema="cairn.synthetic-advice-fixture/1", mode=mode, **values))

        def seed(self, name):
            super().seed(name)
            self.guard_item_id = base.uid(name + ":item:1")
            self.work("features", "enable", "--feature", "work-advice/1")
            self.work("features", "enable", "--feature", "work-artifacts/1")
            self.provider_home = self.runtime / (name + "-synthetic-provider")
            self.provider_home.mkdir(mode=0o700)
            executable = self.provider_home / "synthetic-advice"
            source = ROOT / "tests/fixtures/work_ui/advice_provider.py"
            shutil.copyfile(source, executable)
            executable.chmod(0o500)
            self.provider_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
            self.provider_runs[name] = (self.provider_home, self.provider_sha)
            patch = hashlib.sha256(b"cairn-work-ui-synthetic-advice-provider-v1").hexdigest()
            self.mode("all")
            self.profile_name = "synthetic-ui-" + name
            registered = self.advice("profile", "register", "--profile", self.profile_name,
                "--executable", str(executable), "--binary-sha256", self.provider_sha,
                "--expected-upstream-commit", "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856",
                "--expected-patch-sha256", patch, "--codex-home", str(self.provider_home),
                "--model", "synthetic-work-ui-model")
            base.require(registered["profile"]["binary_sha256"] == self.provider_sha, "synthetic profile did not bind its executable")
            self.case = {"provider": dict(path=str(executable), sha256=self.provider_sha, patch_fixture_sha256=patch,
                claims="synthetic process boundary only", profile=self.profile_name)}
            self.source_body = "SELECTED_ADVICE_SOURCE_" + name + ' <img src=x onerror="window.sourceInjected=true">'
            self.source = self.host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
                kind="note", body="SUPERSEDED_ADVICE_SOURCE_" + name, claim_type="self", sensitivity="shareable",
                scope=dict(repo=self.collection, task_id="*", run_id="*"))))
            self.source = self.host.agent("sender", "revise", dict(request_id=str(uuid.uuid4()),
                record_id=self.source["record_id"], expected_version=self.source["version"],
                repo=self.collection, body=self.source_body))
            base.require(self.source["version"] == 2 and self.source["body"] == self.source_body,
                         "selected source was not actually revised to version two")
            self.private_source = self.host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
                kind="note", body="PRIVATE_ADVICE_SOURCE_NEVER_SENT", claim_type="self", sensitivity="local",
                scope=dict(repo=self.collection, task_id="*", run_id="*"))))
            self.revision = self.work("artifact", "register", "--kind", "report", "--url",
                "https://reports.fixture.invalid/advice/" + name, "--revision", "exact-v1", "--display-name", "Synthetic selected report")
            if name in ("advice_workflow", "advice_currentness"):
                self.work("progress", "add", "--project", self.slug, "--item", self.item_id,
                          "--text", "Operator baseline progress to restore through compensation")
            self.report.setdefault("synthetic_provider", {})[name] = self.case["provider"]

        def headers(self, page):
            response = page.request.get(self.url + "/api/auth/session")
            base.require(response.ok and response.json().get("authenticated") is True, "advice proof lacks a real operator session")
            return {"Origin": self.url, "X-CSRF-Token": response.json()["csrf_token"]}

        def open_item(self, page, item=None, keyboard=False):
            from playwright.sync_api import expect
            if keyboard:
                self.activate_card_by_keyboard(page, item or self.item_id)
            else:
                page.locator(f'.work-card[data-item-uuid="{item or self.item_id}"]').click()
            expect(page.locator("#advice-status")).not_to_have_text("Reading advice availability…")
            expect(page.locator("#advice-reload")).to_be_enabled()

        def settings(self, page, profile=True):
            from playwright.sync_api import expect
            controls = page.locator("#advice-control-settings")
            controls.evaluate("node => node.open = true")
            for selector in ("#advice-generation-enabled", "#advice-apply-enabled"):
                if not page.locator(selector).is_checked():
                    page.locator(selector).check()
            if page.locator("#advice-save-control").is_enabled():
                self.browser_write(page, "/api/work/advice/control", lambda: page.locator("#advice-save-control").click())
                expect(page.locator("#advice-reload")).to_be_enabled()
            policy = page.locator("#advice-policy-settings")
            policy.evaluate("node => node.open = true")
            for field in ("identifier", "title", "state", "blockers", "artifacts", "sources"):
                page.locator(f'#advice-policy-fields input[value="{field}"]').check()
            if page.locator("#advice-save-policy").is_enabled():
                self.browser_write(page, "/api/work/advice/policy", lambda: page.locator("#advice-save-policy").click())
            expect(page.locator("#advice-policy-status")).to_contain_text("Saved policy revision")
            if profile:
                page.locator("#advice-profile").select_option(self.profile_name)
                expect(page.locator("#advice-profile-info")).to_contain_text("codex-subscription")
            controls.evaluate("node => node.open = false")
            policy.evaluate("node => node.open = false")

        def inputs(self, page, source=None, candidate=True):
            from playwright.sync_api import expect
            source = source or self.source
            page.locator("#advice-source-record").fill(source["record_id"])
            page.locator("#advice-source-version").fill(str(source["version"]))
            page.locator("#advice-add-source").click()
            expect(page.locator("#advice-selected-sources")).to_contain_text(source["record_id"])
            if candidate:
                page.locator("#advice-candidate-revision").fill(self.revision["revision_id"])
                page.locator("#advice-add-candidate").click()
                expect(page.locator("#advice-selected-candidates")).to_contain_text(self.revision["revision_id"])

        def current_review(self, page):
            proposal = page.locator("#advice-proposal-select").input_value()
            base.require(proposal, "browser did not select its exact retained proposal")
            return self.advice("show", "--proposal", proposal)

        def generate(self, page, mode="all", **values):
            from playwright.sync_api import expect
            self.mode(mode, **values)
            before = self.invocation_rows()
            result = self.browser_write(page, "/api/work/advice/generate", lambda: page.locator("#advice-generate").press("Enter"))
            expect(page.locator("#advice-review")).to_be_visible()
            expect(page.locator("#advice-reload")).to_be_enabled()
            rows = self.invocation_rows()
            base.require(len(rows) == len(before) + 1, "generation did not make exactly one real provider subprocess invocation")
            review = self.current_review(page)
            base.require(review["proposal"]["proposal_uuid"] == result["proposal"]["proposal_uuid"], "browser selected a different/older proposal")
            base.require(review["input"]["payload_available"] is True
                and review["input"]["snapshot"] == rows[-1]["snapshot"], "review input differs from the actual provider input")
            base.require(review["job"]["input_payload_digest"] == rows[-1]["snapshot_sha256"], "retained input digest differs from actual canonical provider snapshot")
            expect(page.locator("#advice-currentness")).to_contain_text("current")
            page.locator("#advice-manifest").evaluate("node => node.open = true")
            expect(page.locator("#advice-manifest-sources")).to_contain_text(self.source_body)
            base.require(page.evaluate("window.sourceInjected !== true && window.adviceInjected !== true"), "untrusted advice/source text executed")
            return review

        def select_actions(self, page, review, kinds):
            for action in review["actions"]:
                checkbox = page.locator(f'.advice-action[data-action-id="{action["action_uuid"]}"] .advice-action-select')
                if action["status"] == "pending":
                    checkbox.set_checked(action["action_type"] in kinds)

        def assert_review_provenance(self, review):
            sources = review["input"]["snapshot"]["source_references"]
            base.require(len(sources) == 1, "review did not retain the one explicitly selected source")
            source = sources[0]
            expected_digest = hashlib.sha256(self.source_body.encode()).hexdigest()
            base.require(source["id"] == self.source["record_id"] and source["version"] == 2
                and source["body"] == self.source_body and source["body_sha256"] == expected_digest,
                "canonical input did not retain exact selected version-two source bytes")
            manifest_sources = review["manifest"]["sources"]
            base.require(len(manifest_sources) == 1, "manifest does not identify the exact selected source set")
            captured = manifest_sources[0]
            def timestamp(value):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                base.require(parsed.utcoffset() is not None, "source observation lacks a timezone")
                return parsed
            base.require(captured["record_id"] == source["id"] and captured["version"] == source["version"]
                and captured["body_sha256"] == expected_digest
                and timestamp(captured["observed_at"]) == timestamp(self.source["written_at"]),
                "manifest source identity, digest or observation time differs from the actual retained source")
            if "observed_at" in source:
                base.require(timestamp(source["observed_at"]) == timestamp(captured["observed_at"]),
                             "canonical source observation differs from the immutable manifest")
            for action in review["actions"]:
                selected = action["target_payload"].get("source_references", [])
                citations = action["review"]["source_references"]
                base.require([row["record_id"] for row in citations] == selected,
                             "review citations differ from the proposed action's exact source set")
                for cited in citations:
                    base.require(cited["record_id"] == source["id"] and cited["version"] == source["version"]
                        and cited["body_sha256"] == source["body_sha256"]
                        and timestamp(cited["observed_at"]) == timestamp(captured["observed_at"]),
                        "action citation invents a source version, digest or read-time observation")

        def assert_captured_before(self, page, review, before):
            from playwright.sync_api import expect
            labels = dict(annotation_id="Annotation", revision="Revision", kind="Kind", body="Text",
                          state="State", link_id="Link", revision_id="Artifact revision", role="Role")
            for action in review["actions"]:
                payload = action["target_payload"]
                if action["action_type"] == "annotate_progress":
                    matches = [row for row in before["annotations"].values()
                               if row["kind"] == "progress" and row["state"] == "active"]
                    fields = ("annotation_id", "revision", "kind", "body", "state")
                elif action["action_type"] == "annotate_blocker":
                    matches = [row for row in before["annotations"].values()
                               if row["annotation_id"] == payload.get("existing_blocker_id")]
                    fields = ("annotation_id", "revision", "kind", "body", "state")
                elif action["action_type"] == "link_artifact":
                    matches = [row for row in before["links"].values()
                               if row["revision_id"] == payload["artifact_revision_id"] and row["role"] == payload["role"]]
                    fields = ("link_id", "revision", "revision_id", "role", "state")
                else:
                    raise AssertionError("unknown action in captured-Before proof")
                base.require(len(matches) <= 1, "baseline target selection is ambiguous")
                expected = {field: matches[0][field] for field in fields} if matches else None
                captured = action["review"]["before"]
                base.require(captured is None if expected is None else isinstance(captured, dict)
                    and all(captured.get(field) == value for field, value in expected.items()),
                    "Before preview differs from the exact pre-generation target, including retained removed edges")
                rendered = page.locator(f'.advice-action[data-action-id="{action["action_uuid"]}"] .advice-diff > div').first
                if expected is None:
                    expect(rendered).to_contain_text("No existing target.")
                else:
                    actual_fields = rendered.locator("dl").evaluate("node => Object.fromEntries([...node.querySelectorAll('dt')].map(label => [label.textContent, label.nextElementSibling.textContent]))")
                    base.require(all(actual_fields.get(labels[field]) == str(value) for field, value in expected.items()),
                                 "rendered Before omitted or changed a typed captured target field")

        def assert_review_capture_unchanged(self, original, current):
            self.assert_review_provenance(current)
            base.require(current["manifest"]["sources"] == original["manifest"]["sources"]
                and {row["action_uuid"]: row["review"]["before"] for row in current["actions"]}
                    == {row["action_uuid"]: row["review"]["before"] for row in original["actions"]},
                "later review reconstructed captured source provenance or Before from current state")

        def apply_selected(self, page):
            from playwright.sync_api import expect
            result = self.browser_write(page, "/api/work/advice/apply", lambda: page.locator("#advice-apply").click())
            receipt = result["receipt"]
            expect(page.locator(f'.advice-application[data-request-id="{receipt["request_uuid"]}"]')).to_be_visible()
            return receipt

        def work_rows(self):
            return dict(annotations={row["annotation_id"]: row for row in self.detail()["annotations"] or []},
                links={row["link_id"]: row for row in self.work("artifact", "list", "--project", self.slug,
                    "--item", self.item_id, "--include-removed")["artifacts"] or []})

        def history_retains(self, kind, target, *revisions):
            target = str(uuid.UUID(target))
            unique = sorted(set(int(value) for value in revisions))
            base.require(all(value > 0 for value in unique), "nonpositive retained revision")
            table, key = ("work_annotation_history", "annotation_id") if kind == "annotation" else ("work_artifact_link_history", "link_id")
            count = self.host.sql(f"SELECT count(*) FROM cairn.{table} WHERE {key}='{target}' AND revision IN ({','.join(map(str, unique))})")
            base.require(int(count) == len(unique), "exact original/compensating history revisions were not retained")

        def assert_application(self, review, receipt, kinds, before):
            selected = {row["action_uuid"]: row for row in review["actions"] if row["action_type"] in kinds and row["status"] == "pending"}
            base.require(set(receipt["applied_action_uuids"]) == set(selected)
                and len(receipt["applied_action_uuids"]) == len(selected), "application receipt differs from the exact selected action UUID set")
            base.require(set(receipt["output_revisions"]) == set(selected), "receipt output revisions differ from selected actions")
            current = self.work_rows()
            for action_id, action in selected.items():
                output = receipt["output_revisions"][action_id]
                payload = action["target_payload"]
                expected_type = {"annotate_progress": "progress", "annotate_blocker": "blocker_created", "link_artifact": "artifact_linked"}[action["action_type"]]
                base.require(output.get("type") == expected_type, "receipt output type differs from the selected action's compensation obligation")
                if action["action_type"] == "link_artifact":
                    row = current["links"].get(output["link_id"])
                    base.require(row is not None and row["revision"] == output["revision"]
                        and row["revision_id"] == output["revision_id"] == payload["artifact_revision_id"]
                        and row["role"] == payload["role"] == "evidence" and row["state"] == "active"
                        and row["author"] == receipt["applied_by_channel"] and row["rationale"] == action["rationale"],
                        "accepted artifact effect differs from exact revision, role, rationale, attribution or receipt revision")
                    prior = before["links"].get(row["link_id"])
                    base.require(row["revision"] > (prior["revision"] if prior else 0), "artifact application did not create a new edge revision")
                    self.history_retains("link", row["link_id"], row["revision"])
                else:
                    row = current["annotations"].get(output["annotation_id"])
                    expected_kind = "progress" if action["action_type"] == "annotate_progress" else "blocker"
                    base.require(row is not None and row["annotation_id"] not in before["annotations"]
                        and row["revision"] == output["revision"] and row["kind"] == expected_kind
                        and row["body"] == payload["text"] and row["state"] == ("active" if expected_kind == "progress" else "open")
                        and row["author"] == receipt["applied_by_channel"]
                        and sorted(row["source_references"] or []) == sorted(payload.get("source_references", [])),
                        "accepted annotation differs from exact text, source attribution, state or receipt output revision")
                    self.history_retains("annotation", row["annotation_id"], row["revision"])
                    if expected_kind == "progress":
                        priors = [value for value in before["annotations"].values()
                                  if value["kind"] == "progress" and value["state"] == "active"]
                        base.require(len(priors) == 1, "workflow did not capture its one actual baseline selected progress")
                        prior = priors[0]
                        stamp = output.get("superseded_prior")
                        superseded = current["annotations"].get(prior["annotation_id"])
                        base.require(isinstance(stamp, dict) and stamp.get("annotation_id") == prior["annotation_id"]
                            and stamp.get("prior_revision") == prior["revision"]
                            and superseded is not None and superseded["state"] == "superseded"
                            and superseded["revision"] == stamp.get("superseded_revision")
                            and superseded["revision"] > prior["revision"]
                            and superseded["body"] == prior["body"]
                            and sorted(superseded["source_references"] or []) == sorted(prior["source_references"] or []),
                            "progress application omitted or misidentified its actual superseded baseline")
                        self.history_retains("annotation", prior["annotation_id"], prior["revision"], superseded["revision"])

        def assert_compensation(self, original, compensated, before, before_undo):
            base.require(compensated["request_uuid"] == original["request_uuid"]
                and compensated["receipt_uuid"] == original["receipt_uuid"] and compensated["undone"] is True,
                "compensation receipt does not identify the exact original application")
            current = self.work_rows()
            changed_annotations, changed_links = set(), set()
            for action_id, output in original["output_revisions"].items():
                compensation = compensated["compensating_revisions"][action_id]
                if output["type"] in ("progress", "blocker_created"):
                    target = output["annotation_id"]
                    changed_annotations.add(target)
                    row = current["annotations"].get(target)
                    base.require(row is not None and compensation["annotation_id"] == target
                        and row["revision"] == compensation["revision"] > output["revision"]
                        and row["state"] == ("superseded" if output["type"] == "progress" else "resolved"),
                        "Undo receipt left the exact created annotation active or omitted its new compensation revision")
                    self.history_retains("annotation", target, output["revision"], compensation["revision"])
                    if output["type"] == "progress":
                        priors = [value for value in before["annotations"].values()
                                  if value["kind"] == "progress" and value["state"] == "active"]
                        base.require(len(priors) == 1, "Undo lost the captured baseline selected progress")
                        old = priors[0]
                        prior = output.get("superseded_prior")
                        base.require(isinstance(prior, dict) and prior.get("annotation_id") == old["annotation_id"]
                            and prior.get("prior_revision") == old["revision"],
                            "Undo receipt omitted the actual baseline progress restoration obligation")
                        restored = current["annotations"].get(old["annotation_id"])
                        stamp = compensated["compensating_revisions"][action_id + "_restored_prior"]
                        base.require(restored is not None and restored["revision"] == stamp["revision"] > prior["superseded_revision"]
                            and stamp["annotation_id"] == old["annotation_id"]
                            and all(restored[field] == old[field] for field in ("kind", "body", "state"))
                            and sorted(restored["source_references"] or []) == sorted(old["source_references"] or []),
                            "Undo did not restore the exact prior selected progress as a new revision")
                        changed_annotations.add(old["annotation_id"])
                        self.history_retains("annotation", old["annotation_id"], old["revision"], restored["revision"])
                elif output["type"] == "artifact_linked":
                    target = output["link_id"]
                    changed_links.add(target)
                    row = current["links"].get(target)
                    old = before["links"].get(target)
                    base.require(row is not None and compensation["link_id"] == target
                        and row["revision"] == compensation["revision"] > output["revision"]
                        and row["revision_id"] == output["revision_id"] and row["state"] == (old["state"] if old else "removed"),
                        "Undo receipt left the exact new artifact edge active or failed to restore its prior state")
                    if old:
                        base.require(all(row[field] == old[field] for field in ("revision_id", "role", "rationale", "state")),
                                     "Undo changed the prior artifact edge metadata")
                    self.history_retains("link", target, output["revision"], compensation["revision"])
                else:
                    raise AssertionError("unexpected action type in create/subset compensation assertion")
            for target, row in before_undo["annotations"].items():
                if target not in changed_annotations:
                    base.require(current["annotations"].get(target) == row, "Undo changed an unrelated prior annotation")
            for target, row in before_undo["links"].items():
                if target not in changed_links:
                    base.require(current["links"].get(target) == row, "Undo changed an unrelated prior artifact edge")

        def undo_application(self, page, receipt):
            from playwright.sync_api import expect
            button = page.locator(f'.advice-application[data-request-id="{receipt["request_uuid"]}"] .advice-undo')
            result = self.browser_write(page, "/api/work/advice/undo", button.click)
            base.require(result["receipt"]["request_uuid"] == receipt["request_uuid"] and result["receipt"]["undone"] is True,
                         "Undo returned another application or no compensation")
            expect(page.locator(f'.advice-application[data-request-id="{receipt["request_uuid"]}"]')).to_contain_text("Application undone")
            return result["receipt"]

        def screenshot(self, page, name, selector="#item-advice"):
            page.locator(selector).scroll_into_view_if_needed()
            page.screenshot(path=str(self.output / (name + ".png")), full_page=True)

        def workflow(self, page, viewport):
            from playwright.sync_api import expect
            self.open_item(page, keyboard=viewport == "desktop")
            if viewport == "desktop":
                # First policy is created through the real UI. Missing policy
                # never becomes fabricated success or implicit shared fields.
                expect(page.locator("#advice-generate")).to_be_disabled()
                before = len(self.invocation_rows())
                refused = page.request.post(self.url + "/api/work/advice/generate", headers=self.headers(page), data=dict(
                    project=self.slug, item=self.item_id, profile=self.profile_name, request_id=str(uuid.uuid4())))
                base.require(not refused.ok and refused.json().get("code") == "POLICY_UNENFORCEABLE",
                             "generation without explicit field policy did not refuse with its policy reason")
                base.require(len(self.invocation_rows()) == before, "missing field policy launched the synthetic provider")
            self.settings(page)
            self.inputs(page)
            before_first = self.work_rows()
            old_edges = [row for row in before_first["links"].values()
                         if row["revision_id"] == self.revision["revision_id"] and row["role"] == "evidence"]
            base.require((not old_edges) if viewport == "desktop" else len(old_edges) == 1 and old_edges[0]["state"] == "removed",
                         "workflow did not exercise the intended new desktop edge and retained removed phone edge")
            review = self.generate(page)
            self.assert_review_provenance(review)
            self.assert_captured_before(page, review, before_first)
            base.require(self.work_rows() == before_first, "generation changed its captured work baseline")
            self.screenshot(page, "advice-review-" + viewport, "#advice-review-meta")
            self.screenshot(page, "advice-before-" + viewport, ".advice-action:first-child .advice-diff")
            self.select_actions(page, review, {"annotate_progress", "link_artifact"})
            first = self.apply_selected(page)
            self.assert_application(review, first, {"annotate_progress", "link_artifact"}, before_first)
            updated = self.current_review(page)
            self.assert_review_capture_unchanged(review, updated)
            base.require([action["action_type"] for action in updated["actions"] if action["status"] == "pending"] == ["annotate_blocker"],
                         "unselected blocker did not remain pending")
            before_second = self.work_rows()
            self.select_actions(page, updated, {"annotate_blocker"})
            second = self.apply_selected(page)
            self.assert_application(updated, second, {"annotate_blocker"}, before_second)
            self.screenshot(page, "advice-applications-" + viewport, ".advice-application:first-child")
            before_second_undo = self.work_rows()
            second_undo = self.undo_application(page, second)
            self.assert_compensation(second, second_undo, before_second, before_second_undo)
            before_first_undo = self.work_rows()
            first_undo = self.undo_application(page, first)
            self.assert_compensation(first, first_undo, before_first, before_first_undo)
            undone = self.current_review(page)
            base.require(len(undone["applications"]) == 2 and all(row["undone"] for row in undone["applications"]), "application history did not retain two separate compensations")
            blocker = self.work("blocker", "add", "--project", self.slug, "--item", self.item_id,
                                "--text", "Existing operator blocker for guarded reopening")
            page.locator("#advice-reload").click()
            expect(page.locator("#advice-reload")).to_be_enabled()
            before_resolution = self.work_rows()
            resolution = self.generate(page, "resolve", blocker_id=blocker["annotation_id"])
            self.assert_review_provenance(resolution)
            self.assert_captured_before(page, resolution, before_resolution)
            self.select_actions(page, resolution, {"annotate_blocker"})
            resolved = self.apply_selected(page)
            base.require(next(row for row in self.detail()["annotations"] if row["annotation_id"] == blocker["annotation_id"])["state"] == "resolved",
                         "accepted resolution did not resolve the exact existing blocker")
            self.undo_application(page, resolved)
            reopened = next(row for row in self.detail()["annotations"] if row["annotation_id"] == blocker["annotation_id"])
            base.require(reopened["state"] == "open" and reopened["body"] == blocker["body"] and reopened["revision"] > blocker["revision"],
                         "Undo did not reopen the prior exact blocker state as a new revision")
            rejected = self.generate(page)
            before_rows = self.host.sql("SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY annotation_id),'[]') FROM cairn.work_annotation a")
            self.browser_write(page, "/api/work/advice/reject", lambda: page.locator("#advice-reject").click())
            expect(page.locator("#advice-review-meta")).to_contain_text("rejected")
            base.require(self.host.sql("SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY annotation_id),'[]') FROM cairn.work_annotation a") == before_rows,
                         "proposal rejection changed work annotations")
            base.require(self.advice("show", "--proposal", rejected["proposal"]["proposal_uuid"])["proposal"]["status"] == "rejected", "reject did not persist")

        def refusal_cases(self, page, viewport="desktop"):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page, source=self.private_source)
            before = len(self.invocation_rows())
            with page.expect_response(lambda response: response.url.endswith("/api/work/advice/generate") and response.request.method == "POST") as pending:
                page.locator("#advice-generate").click()
            response = pending.value
            base.require(not response.ok and response.json().get("code") == "SOURCE_INELIGIBLE", "local source generation did not refuse for hosted eligibility")
            base.require(len(self.invocation_rows()) == before, "ineligible source reached provider subprocess")
            page.locator("#advice-selected-sources .advice-remove-input").click()
            self.inputs(page)
            for mode, code in (("malformed", "INVALID_PROPOSAL"), ("quota", "QUOTA_EXHAUSTED")):
                self.mode(mode); before = len(self.invocation_rows())
                with page.expect_response(lambda value: value.url.endswith("/api/work/advice/generate") and value.request.method == "POST") as pending:
                    page.locator("#advice-generate").click()
                response = pending.value
                base.require(not response.ok and response.json().get("code") == code, "provider refusal did not retain its intended typed reason")
                expect(page.locator("#advice-error")).to_contain_text(code)
                base.require(len(self.invocation_rows()) == before + 1, "output refusal repaired, retried, or skipped the actual invocation")
                page.locator("#advice-inspect-request").click()
                expect(page.locator("#advice-job-status")).to_contain_text(code)
                base.require(not self.detail()["annotations"], "refused generation mutated work metadata")
                # Change the selected source list only through explicit user
                # input so a distinct requested operation gets a distinct UUID.
                if mode == "malformed":
                    page.locator("#advice-selected-candidates .advice-remove-input").click()
            self.screenshot(page, "advice-refusal-" + viewport)

        def forget_source(self):
            grant = self.host.operator("bootstrap", dict(request_id=str(uuid.uuid4()), reason="Disposable advice source forgetting"))
            preview = self.host.operator("preview-delete", args=[self.source["record_id"]])
            self.host.operator("forget", dict(request_id=str(uuid.uuid4()), record_id=self.source["record_id"],
                expected_version=self.source["version"], grant_id=grant["grant_id"], preview_id=preview["preview_id"]))

        def currentness_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page)
            baseline = self.work_rows()
            review = self.generate(page)
            self.assert_review_provenance(review)
            self.assert_captured_before(page, review, baseline)
            self.work("progress", "add", "--project", self.slug, "--item", self.item_id, "--text", "Intervening selected progress")
            page.locator("#advice-source-record").fill("UNSAVED SOURCE DRAFT")
            page.locator("#advice-reload").click()
            expect(page.locator("#advice-currentness")).to_contain_text("stale")
            expect(page.locator("#advice-apply")).to_be_disabled()
            expect(page.locator("#advice-source-record")).to_have_value("UNSAVED SOURCE DRAFT")
            stale = self.current_review(page)
            self.assert_review_capture_unchanged(review, stale)
            self.assert_captured_before(page, stale, baseline)
            before = len(self.invocation_rows())
            self.forget_source()
            page.locator("#advice-reload").click()
            expect(page.locator("#advice-currentness")).to_contain_text("purged")
            expect(page.locator("#item-advice")).not_to_contain_text(self.source_body)
            expect(page.locator("#advice-review-meta")).to_contain_text(review["proposal"]["proposal_uuid"])
            base.require(len(self.invocation_rows()) == before, "review/currentness reads launched provider")
            self.screenshot(page, "advice-purged-desktop")

        def disabled_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page)
            review = self.generate(page)
            self.select_actions(page, review, {"annotate_progress"})
            receipt = self.apply_selected(page)
            page.locator("#advice-control-settings").evaluate("node => node.open = true")
            page.locator("#advice-generation-enabled").uncheck(); page.locator("#advice-apply-enabled").uncheck()
            self.browser_write(page, "/api/work/advice/control", lambda: page.locator("#advice-save-control").click())
            expect(page.locator("#advice-generate")).to_be_disabled(); expect(page.locator("#advice-apply")).to_be_disabled()
            expect(page.locator("#advice-review")).to_be_visible()
            self.undo_application(page, receipt)
            self.work("features", "disable", "--feature", "work-advice/1")
            page.locator("#advice-reload").click()
            expect(page.locator("#advice-review")).to_be_visible()
            expect(page.locator("#advice-status")).to_contain_text("disabled")
            self.screenshot(page, "advice-disabled-desktop")

        def retries_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page); self.mode("all")
            before = len(self.invocation_rows()); committed = []
            def lose_generation(route):
                response = route.fetch()
                base.require(response.ok, "the generation response selected for loss did not commit successfully")
                committed.append(dict(body=route.request.post_data_json, result=response.json()))
                route.abort("failed")
            page.route(self.url + "/api/work/advice/generate", lose_generation, times=1)
            page.locator("#advice-generate").click()
            expect(page.locator("#advice-error")).to_contain_text("Change not confirmed")
            base.require(len(committed) == 1 and len(self.invocation_rows()) == before + 1, "lost-response injection did not commit exactly one actual invocation")
            request_id = committed[0]["body"]["request_id"]
            expect(page.locator("#advice-request-id")).to_have_value(request_id)
            page.locator("#advice-generate").click()
            expect(page.locator("#advice-review")).to_be_visible()
            base.require(len(self.invocation_rows()) == before + 1, "identical generation retry invoked provider again")
            review = self.current_review(page)
            base.require(review["proposal"]["proposal_uuid"] == committed[0]["result"]["proposal"]["proposal_uuid"], "retry returned a different proposal")
            page.reload(); self.open_item(page)
            expect(page.locator("#advice-request-id")).to_have_value(request_id)
            page.locator("#advice-inspect-request").click()
            expect(page.locator("#advice-review-meta")).to_contain_text(request_id)
            base.require(len(self.invocation_rows()) == before + 1, "request inspection reran generation")
            # Fault only the real committed reply, not Cairn's mutation path.
            # An invalid same-proposal receipt must preserve the apply retry ID.
            review = self.current_review(page)
            self.select_actions(page, review, {"annotate_progress", "link_artifact"})
            altered = []
            def altered_receipt(route):
                response = route.fetch()
                base.require(response.ok, "receipt fault did not begin with an actual committed application")
                reply = response.json()
                altered.append(dict(request=route.request.post_data_json, receipt=reply["receipt"]))
                changed = json.loads(json.dumps(reply))
                changed["receipt"]["applied_action_uuids"] = [row["action_uuid"] for row in review["actions"]
                    if row["action_type"] in ("annotate_progress", "annotate_blocker")]
                route.fulfill(response=response, json=changed)
            page.route(self.url + "/api/work/advice/apply", altered_receipt, times=1)
            page.locator("#advice-apply").click()
            expect(page.locator("#advice-error")).to_contain_text("exact request, proposal and selected action set")
            base.require(len(altered) == 1, "corrupted application reply was not delivered")
            with page.expect_request(lambda request: request.url.endswith("/api/work/advice/apply")) as retry:
                receipt = self.apply_selected(page)
            base.require(retry.value.post_data_json["request_id"] == altered[0]["request"]["request_id"]
                and receipt["receipt_uuid"] == altered[0]["receipt"]["receipt_uuid"], "invalid receipt discarded the original apply retry identity")
            retained = self.current_review(page)
            base.require(len(retained["applications"]) == 1, "receipt retry created another application")
            self.screenshot(page, "advice-recovered-desktop")

        def conflict_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page)
            review = self.generate(page)
            self.select_actions(page, review, {"annotate_progress"})
            receipt = self.apply_selected(page)
            progress = next(row for row in self.detail()["annotations"] if row["kind"] == "progress" and row["state"] == "active")
            edited = self.work("progress", "edit", "--project", self.slug, "--item", self.item_id,
                "--annotation-id", progress["annotation_id"], "--expected-revision", str(progress["revision"]),
                "--text", "Operator correction after the model-assisted application")
            page.locator("#advice-reload").click()
            row = page.locator(f'.advice-application[data-request-id="{receipt["request_uuid"]}"]')
            expect(row).to_contain_text("UNDO_CONFLICT")
            expect(row.locator(".advice-undo")).to_be_disabled()
            response = page.request.post(self.url + "/api/work/advice/undo", headers=self.headers(page),
                data=dict(request_uuid=receipt["request_uuid"], request_id=str(uuid.uuid4())))
            base.require(not response.ok and response.json().get("code") == "UNDO_CONFLICT", "guarded Undo did not refuse intervening edits")
            current = next(value for value in self.detail()["annotations"] if value["annotation_id"] == edited["annotation_id"])
            base.require(current == edited, "Undo refusal changed the exact intervening edit")
            self.screenshot(page, "advice-undo-conflict-desktop")

        def navigation_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page)
            review = self.generate(page)
            page.locator("#advice-source-record").fill("RETAIN THIS UNSAVED SOURCE DRAFT")
            proposal = review["proposal"]["proposal_uuid"]
            address = self.url + "/api/work/advice/show?proposal=" + proposal
            held = {}
            def hold_real_review(route):
                response = route.fetch()
                base.require(response.ok, "delayed A review was not an actual successful current read")
                held.update(route=route, response=response, request=route.request)
            page.route(address, hold_real_review, times=1)
            try:
                page.locator("#advice-reload").click()
                deadline = time.monotonic() + 8
                while not held and time.monotonic() < deadline:
                    page.wait_for_timeout(50)
                base.require(bool(held), "the exact A review request was not captured")
                page.locator("#modal-close").click()
                self.open_item(page, self.guard_item_id)
                expect(page.locator("#advice-empty")).to_be_visible()
                with page.expect_event("requestfinished", predicate=lambda request: request == held["request"], timeout=8000):
                    held["route"].fulfill(response=held["response"])
                held["delivered"] = True
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                expect(page.locator("#advice-empty")).to_be_visible()
                expect(page.locator("#advice-review")).to_be_hidden()
                expect(page.locator("#item-advice")).not_to_contain_text(self.source_body)
                expect(page.locator("#modal-title")).to_contain_text("task 002")
                page.locator("#modal-close").click(); self.open_item(page)
                expect(page.locator("#advice-source-record")).to_have_value("RETAIN THIS UNSAVED SOURCE DRAFT")
                self.screenshot(page, "advice-navigation-draft-desktop")
            finally:
                if held and not held.get("delivered"):
                    held["route"].abort("failed")
                page.unroute(address, hold_real_review)

        def polling_case(self, page):
            from playwright.sync_api import expect
            self.open_item(page); self.settings(page); self.inputs(page)
            review = self.generate(page)
            self.mode("quota")
            held = {}
            address = self.url + "/api/work/advice/generate"
            def hold_actual_failure(route):
                response = route.fetch()
                base.require(not response.ok and response.json().get("code") == "QUOTA_EXHAUSTED",
                             "busy-poll case did not receive an actual typed failing POST")
                held.update(route=route, response=response, captured_at=time.monotonic())
            page.route(address, hold_actual_failure, times=1)
            try:
                page.locator("#advice-generate").click()
                deadline = time.monotonic() + 8
                while not held and time.monotonic() < deadline:
                    page.wait_for_timeout(50)
                base.require(bool(held), "failing POST was not captured")
                # Real wall time crosses the 30s read timer while the browser is
                # writing. Only response delivery is delayed; no clock is faked.
                page.wait_for_timeout(32000)
                held["route"].fulfill(response=held["response"]); held["delivered"] = True
                expect(page.locator("#advice-error")).to_contain_text("QUOTA_EXHAUSTED")
                page.locator("#advice-source-record").fill("POLL MUST PRESERVE THIS DRAFT")
                target = self.url + "/api/work/advice/show?proposal=" + review["proposal"]["proposal_uuid"]
                with page.expect_response(lambda response: response.url == target and response.request.method == "GET", timeout=40000) as reread:
                    self.forget_source()
                response = reread.value
                base.require(response.ok and response.json()["currentness"]["state"] == "purged",
                             "automatic review did not resume after the delayed failed POST")
                expect(page.locator("#advice-currentness")).to_contain_text("purged")
                expect(page.locator("#item-advice")).not_to_contain_text(self.source_body)
                expect(page.locator("#advice-source-record")).to_have_value("POLL MUST PRESERVE THIS DRAFT")
                self.report["busy_poll_elapsed_seconds"] = time.monotonic() - held["captured_at"]
                self.screenshot(page, "advice-poll-after-failure-desktop")
            finally:
                if held and not held.get("delivered"):
                    held["route"].abort("failed")
                page.unroute(address, hold_actual_failure)

        def uipass(self, name, storage):
            import yaml
            fixture = self.output / (name + "-fixture"); fixture.mkdir(mode=0o700)
            manifest = yaml.safe_load((ROOT / ".uipass-work-advice.yaml").read_text())
            manifest["surfaces"] = {name: manifest["surfaces"][name]}
            base.dump(fixture / "session.json", storage)
            for state in manifest["surfaces"][name]["states"].values():
                state["storage_state_file"] = "session.json"
                for action in state.get("actions", []):
                    if action.get("selector") == ".work-card >> nth=0": action["selector"] = f'.work-card[data-item-uuid="{self.item_id}"]'
            path = fixture / "manifest.yaml"; path.write_text(yaml.safe_dump(manifest, sort_keys=False))
            bundle = self.output / (name + "-uipass")
            command = ([shutil.which("uipass")] if shutil.which("uipass") else ["uv", "run", "--project", str(self.args.uipass_home.resolve()), "--frozen", "uipass"])
            result = subprocess.run([*command, "run", "--manifest", str(path), "--base-url", self.url, "--output", str(bundle)], capture_output=True, text=True, timeout=210)
            (self.output / (name + "-uipass.log")).write_text(result.stdout + result.stderr)
            verdict = json.loads((bundle / "verdict.json").read_text()) if (bundle / "verdict.json").exists() else None
            self.report["uipass"].append(dict(state=name, exit_code=result.returncode, bundle=bundle.name, verdict=verdict))
            base.require(result.returncode == 0, f"UIPass {name} exit {result.returncode}; inspect retained evidence")

        def run_state(self, browser, name):
            self.seed(name); self.start_web()
            with browser.new_context(viewport=dict(width=1440, height=1000)) as context:
                page = self.login(context); storage = context.storage_state(); before = self.coordination_state()
                page.set_default_timeout(8000)
                if name == "advice_workflow":
                    for viewport, size in (("desktop", dict(width=1440, height=1000)), ("phone", dict(width=390, height=844))):
                        with browser.new_context(viewport=size, storage_state=storage) as view:
                            ui = view.new_page(); ui.set_default_timeout(8000); ui.goto(self.url)
                            if not self.check("actual advice operator journey " + viewport, lambda: self.workflow(ui, viewport), ui):
                                self.report.setdefault("stopped_scenarios", []).append(dict(state=name, viewport=viewport,
                                    reason="First workflow failure retained; remaining journeys and UIPass were not run."))
                                return
                elif name == "advice_refusals":
                    for viewport, size in (("desktop", dict(width=1440, height=1000)), ("phone", dict(width=390, height=844))):
                        with browser.new_context(viewport=size, storage_state=storage) as view:
                            ui = view.new_page(); ui.set_default_timeout(8000); ui.goto(self.url)
                            self.check("actual advice refusals " + viewport, lambda: self.refusal_cases(ui, viewport), ui)
                elif name == "advice_empty":
                    self.open_item(page); self.settings(page); self.inputs(page)
                    self.check("valid no-suggestions generation", lambda: self.generate(page, "none"), page)
                else:
                    operation = {"advice_refusals": self.refusal_cases, "advice_currentness": self.currentness_case,
                                 "advice_disabled": self.disabled_case, "advice_retries": self.retries_case,
                                 "advice_conflicts": self.conflict_case, "advice_navigation": self.navigation_case,
                                 "advice_polling": self.polling_case}[name]
                    self.check(name, lambda: operation(page), page)
                if name == "advice_refusals":
                    self.report.setdefault("uipass_scope_exclusions", []).append(dict(state=name,
                        reason="Generation failures belong to this actual browser request; a new UIPass navigation cannot reproduce that transient error without another invocation. Inspect the retained failure screenshot."))
                else:
                    self.check("UIPass " + name, lambda: self.uipass(name, storage))
                self.check("advice preserves coordination " + name, lambda: base.require(self.coordination_state() == before,
                    "advice journey mutated sessions, inbox events or delivery state"))

        def close(self):
            # Finalize evidence before cleanup even after an early journey/setup failure.
            # A failed evidence check must not prevent owned-process/cluster cleanup.
            try:
                for name, (home, digest) in self.provider_runs.items():
                    def retain_provider(name=name, home=home, digest=digest):
                        rows = self.invocation_rows(home)
                        base.dump(self.output / (name + "-provider-invocations.json"), rows)
                        self.report.setdefault("provider_generation_counts", {})[name] = len(rows)
                        base.require(hashlib.sha256((home / "synthetic-advice").read_bytes()).hexdigest() == digest,
                                     "registered provider executable changed during browser proof")
                    self.check("provider evidence retained and executable unchanged " + name,
                               retain_provider, category="fixture")
            finally:
                super().close()

        def run(self):
            super().run()
            for name, digest in self.report["fixture_inputs"].items():
                base.require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "fixture changed during execution: " + name)

    return AdviceFixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", action="append", default=[])
    parser.add_argument("--web-overlay", type=Path)
    parser.add_argument("--retain-binary", action="store_true")
    parser.add_argument("--uipass-home", type=Path, default=Path.home() / "git/uipass")
    args = parser.parse_args(); args.scope = "advice"
    fixture = advice_fixture()(args)
    try:
        fixture.check("fixture setup and execution", fixture.run, category="fixture")
    finally:
        fixture.check("disposable cleanup", fixture.close, category="fixture")
    return fixture.finish()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run pinned legacy fixtures; selected-profile success is not release acceptance."""
import argparse
import ast
import base64
from collections import Counter
from dataclasses import asdict, dataclass
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'cairn.hybrid-release-baseline/1'
CLOCK = time.monotonic


@dataclass(frozen=True)
class Evidence:
    lane: str
    row_kind: str
    rel_ids: tuple
    rows: tuple
    limits: str


@dataclass(frozen=True)
class Suite:
    profile: str
    path: str
    sha256: str
    cases: dict


def case_inventory(names, evidence, overrides=None):
    overrides = overrides or {}
    return {name: overrides.get(name, evidence) for name in names.split()}


SUITES = (
    Suite(
        'legacy-closure',
        'scripts/test_installed_lifecycle_bundle.py',
        'd3c9067614baf538dc45bf37b293e763240333a185b0ef0a63ceae9f512b49a0',
        case_inventory("""
            InstalledLifecycleTests.test_coordination_installations_run_outside_checkout_for_every_harness
            InstalledLifecycleTests.test_coordination_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses
            InstalledLifecycleTests.test_explicit_memory_reinstall_preserves_config_and_refuses_enabled_binding
            InstalledLifecycleTests.test_installed_memory_refuses_changed_or_missing_helpers_before_state_write
            InstalledLifecycleTests.test_memory_installations_are_complete_for_every_supported_harness
            InstalledLifecycleTests.test_memory_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses
            InstalledLifecycleTests.test_shared_coordination_reinstall_refuses_another_enabled_account
        """, Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('shared:installation',), 'Installed help/hint/excluded entries and preservation/refusal only, not recall/capture/delivery effects.'), {
            'InstalledLifecycleTests.test_coordination_installations_run_outside_checkout_for_every_harness': Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:coordination', 'codex:coordination', 'opencode:coordination', 'agy:coordination', 'hermes:coordination'), 'Installed help/hint/excluded entries and preservation/refusal only, not recall/capture/delivery effects.'),
            'InstalledLifecycleTests.test_coordination_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses': Evidence('fake-boundary', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:coordination', 'codex:coordination', 'opencode:coordination', 'agy:coordination', 'hermes:coordination'), 'Actual disposable installer/filesystem preservation and refusal; no hook execution or native refresh.'),
            'InstalledLifecycleTests.test_explicit_memory_reinstall_preserves_config_and_refuses_enabled_binding': Evidence('fake-boundary', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:memory',), 'Actual disposable installer/filesystem preservation and refusal; no hook execution or native refresh.'),
            'InstalledLifecycleTests.test_installed_memory_refuses_changed_or_missing_helpers_before_state_write': Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:memory', 'codex:memory', 'opencode:memory', 'hermes:memory'), 'Installed help/hint/excluded entries and preservation/refusal only, not recall/capture/delivery effects.'),
            'InstalledLifecycleTests.test_memory_installations_are_complete_for_every_supported_harness': Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:memory', 'codex:memory', 'opencode:memory', 'hermes:memory'), 'Installed help/hint/excluded entries and preservation/refusal only, not recall/capture/delivery effects.'),
            'InstalledLifecycleTests.test_memory_reinstall_retains_custom_state_and_existing_ledger_for_all_harnesses': Evidence('fake-boundary', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:memory', 'codex:memory', 'opencode:memory', 'hermes:memory'), 'Actual disposable installer/filesystem preservation and refusal; no hook execution or native refresh.'),
            'InstalledLifecycleTests.test_shared_coordination_reinstall_refuses_another_enabled_account': Evidence('fake-boundary', 'closure-smoke', ('REL-BASE', 'REL-PIN', 'REL-ROLLBACK'), ('claude:coordination', 'codex:coordination', 'opencode:coordination', 'agy:coordination', 'hermes:coordination'), 'Actual disposable installer/filesystem preservation and refusal; no hook execution or native refresh.'),
        })),
    Suite(
        'legacy-closure',
        'integrations/lifecycle/test_lifecycle_bundle.py',
        'ee4cbbd05832dc717536b98c4491f25721e846ac9a6e91c84ac10c6e8d940966',
        case_inventory("""
            LifecycleBundleTests.test_copying_installed_bundle_preserves_checked_bytes_and_refuses_drift
            LifecycleBundleTests.test_every_lifecycle_source_is_in_the_installed_snapshot
            LifecycleBundleTests.test_installed_coordination_loads_every_native_queue_adapter
            LifecycleBundleTests.test_installed_entry_points_share_the_staged_hook_admission
            LifecycleBundleTests.test_installed_entry_refuses_incomplete_changed_and_nonregular_code
            LifecycleBundleTests.test_two_bundles_ignore_global_modules_and_keep_their_verified_helpers
        """, Evidence('source-only', 'closure-smoke', ('REL-BASE', 'REL-PIN'), ('shared:bundle',), 'Staged bundle/module identity assertions; no native callback refresh.'), {
            'LifecycleBundleTests.test_copying_installed_bundle_preserves_checked_bytes_and_refuses_drift': Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN'), ('shared:bundle',), 'Staged bundle/module identity assertions; no native callback refresh.'),
            'LifecycleBundleTests.test_installed_entry_refuses_incomplete_changed_and_nonregular_code': Evidence('installed-smoke', 'closure-smoke', ('REL-BASE', 'REL-PIN'), ('shared:bundle',), 'Staged bundle/module identity assertions; no native callback refresh.'),
        })),
    Suite(
        'legacy-closure',
        'scripts/test_install_agent_coordination.py',
        '5bab96d52c0a907b9974670338c98a264695211a0d144919da770a5c98ee6935',
        case_inventory("""
            BridgeOnlyIdleWakeConfig.test_a_bridge_only_binding_cannot_be_run_as_a_herdr_command
            BridgeOnlyIdleWakeConfig.test_only_opencode_with_native_delivery_accepts_true
            CompactInboxInstaller.test_active_pinned_recall_blocks_config_change_before_install
            CompactInboxInstaller.test_launcher_hides_installed_paths_and_preserves_arguments
            CompactInboxInstaller.test_notice_batch_requires_compact_native_binding
            CompactInboxInstaller.test_only_version_one_on_native_codex_or_claude_is_admitted
            IdleWakeupPreflight.test_claude_second_account_home_is_covered
            IdleWakeupPreflight.test_codex_needs_no_herdr_integration
            IdleWakeupPreflight.test_current_and_outdated_states
            IdleWakeupPreflight.test_hermes_and_agy_use_their_home_overrides
            IdleWakeupPreflight.test_install_failure_and_persistent_gap_report_manual_repair
            IdleWakeupPreflight.test_native_herdr_preserves_existing_hooks_in_an_isolated_account_home
            IdleWakeupPreflight.test_opencode_single_home_and_settings_mismatch
            IdleWakeupPreflight.test_reported_integration_outside_the_selected_home_refuses
            ScratchOpenCodeBinding.test_default_herdr_install_is_unchanged_for_the_owner_home
            ScratchOpenCodeBinding.test_installed_scratch_binding_selects_the_bridge_wake_without_herdr
            ScratchOpenCodeBinding.test_no_herdr_installs_a_usable_scratch_binding_and_touches_nothing_else
            ScratchOpenCodeBinding.test_no_herdr_is_limited_to_opencode_idle_wake_without_a_herdr_choice
            ScratchOpenCodeBinding.test_no_herdr_without_no_service_refuses_before_install_or_shared_watcher
            ScratchOpenCodeBinding.test_without_the_flag_a_scratch_directory_is_refused_and_points_to_it
        """, Evidence('fake-boundary', 'closure-smoke', ('REL-BASE', 'REL-IDENTITY', 'REL-HOOKS'), ('shared:installation',), 'Synthetic installer settings and host probes; real Herdr case uses an isolated account home.'), {
            'CompactInboxInstaller.test_only_version_one_on_native_codex_or_claude_is_admitted': Evidence('fake-boundary', 'unsupported-expectation', ('REL-BASE', 'REL-IDENTITY', 'REL-HOOKS'), ('shared:installation',), 'Synthetic installer settings and host probes; real Herdr case uses an isolated account home.'),
            'IdleWakeupPreflight.test_native_herdr_preserves_existing_hooks_in_an_isolated_account_home': Evidence('real-process', 'closure-smoke', ('REL-BASE', 'REL-IDENTITY', 'REL-HOOKS'), ('shared:installation',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Native Herdr integration only, in a disposable account home.'),
        })),
    Suite(
        'legacy-closure',
        'scripts/test_install_agent_coordination_timeout.py',
        '2af1de2aeb69672039962c51d71466d116d3d75c2ed928e26542aeb78af5f5c4',
        case_inventory("""
            CodexSetupDeadlineTests.test_blocked_setup_rpc_terminates_app_server_process
        """, Evidence('real-process', 'supported', ('REL-PROCESS',), ('codex:coordination',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Synthetic stalled setup, not native request cancellation.'), {
        })),
    Suite(
        'legacy-closure',
        'scripts/test_install_agent_coordination_active_turn.py',
        'dc62d773318658f5d5446072118fa6f59b177422397035d852055574dc3366b8',
        case_inventory("""
            ActiveTurnIntegrationTests.test_stalled_turn_cancel_and_namespace_descendant_cleanup
        """, Evidence('real-process', 'supported', ('REL-PROCESS',), ('codex:coordination',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Node stand-in and owned namespace cleanup; not public Store cancellation.'), {
        })),
    Suite(
        'legacy-memory',
        'scripts/test_claude_lifecycle.py',
        '4f93c793b3730de135ed5155d3491b35c768f6d26e3ddd461729a744b1d4af1a',
        case_inventory("""
            CandidateBudgetTests.test_memory_command_output_is_bounded_on_both_pipes
            CandidateBudgetTests.test_memory_command_reads_input_while_child_writes_output
            LifecycleTests.test_capture_updates_one_note_and_null_does_not_write
            LifecycleTests.test_disable_and_child_guard_do_not_read_transcript_or_memory
            LifecycleTests.test_failed_write_is_not_reported_as_saved
            LifecycleTests.test_invalid_model_output_never_writes
            LifecycleTests.test_opencode_identity_and_retained_context_use_the_shared_engine
            LifecycleTests.test_same_session_capture_cannot_race
            LifecycleTests.test_search_timeout_is_labelled_and_does_not_inject_or_write
            RecallCandidateTests.test_real_selector_timeout_keeps_required_context
        """, Evidence('fake-boundary', 'supported', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('claude:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'), {
            'CandidateBudgetTests.test_memory_command_output_is_bounded_on_both_pipes': Evidence('real-process', 'supported', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('claude:memory',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance.'),
            'CandidateBudgetTests.test_memory_command_reads_input_while_child_writes_output': Evidence('real-process', 'supported', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('claude:memory',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance.'),
            'LifecycleTests.test_disable_and_child_guard_do_not_read_transcript_or_memory': Evidence('fake-boundary', 'unsupported-expectation', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('claude:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'),
            'LifecycleTests.test_opencode_identity_and_retained_context_use_the_shared_engine': Evidence('fake-boundary', 'supported', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('opencode:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'),
            'RecallCandidateTests.test_real_selector_timeout_keeps_required_context': Evidence('real-process', 'supported', ('REL-BASE', 'REL-STREAM', 'REL-PROCESS'), ('claude:memory',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance.'),
        })),
    Suite(
        'legacy-memory',
        'scripts/test_codex_lifecycle.py',
        '0ed57dbe391babaca4bec8e27edfaf041b64613a4dacfde2cd22e2ed032b6800',
        case_inventory("""
            CodexHookTests.test_failed_capture_does_not_advance_the_marker
            CodexHookTests.test_session_start_recall_uses_codex_task_scope
            CodexHookTests.test_stop_captures_only_after_enough_new_dialogue
            CodexHookTests.test_stop_continuation_and_other_harnesses_do_not_capture
            CodexHookTests.test_wakes_do_not_advance_capture_position_or_trigger_stop_capture
        """, Evidence('fake-boundary', 'supported', ('REL-BASE', 'REL-STREAM'), ('codex:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'), {
            'CodexHookTests.test_stop_continuation_and_other_harnesses_do_not_capture': Evidence('fake-boundary', 'unsupported-expectation', ('REL-BASE', 'REL-STREAM'), ('codex:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'),
            'CodexHookTests.test_wakes_do_not_advance_capture_position_or_trigger_stop_capture': Evidence('fake-boundary', 'unsupported-expectation', ('REL-BASE', 'REL-STREAM'), ('codex:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'),
        })),
    Suite(
        'legacy-memory',
        'scripts/test_hermes_lifecycle.py',
        'f9bfa9972b974b966984e8752234f09af54b09276dd7356692b371aa486bfdf8',
        case_inventory("""
            HermesBoundaryTests.test_profile_guard_prevents_same_session_label_crossing_homes
            HermesBoundaryTests.test_request_copies_do_not_accumulate_or_edit_original_history
            HermesBoundaryTests.test_shutdown_releases_registrations_and_session_switch_keeps_identity
            HermesBoundaryTests.test_timeout_kills_engine_and_selector_child
        """, Evidence('fake-boundary', 'supported', ('REL-BASE', 'REL-HERMES'), ('hermes:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'), {
            'HermesBoundaryTests.test_profile_guard_prevents_same_session_label_crossing_homes': Evidence('fake-boundary', 'unsupported-expectation', ('REL-BASE', 'REL-HERMES'), ('hermes:memory',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'),
            'HermesBoundaryTests.test_timeout_kills_engine_and_selector_child': Evidence('real-process', 'supported', ('REL-BASE', 'REL-HERMES'), ('hermes:memory',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance.'),
        })),
    Suite(
        'legacy-memory',
        'scripts/test_claude_inbox_recall.py',
        '2912f544d44a38d6d10a6bef27bf8a1f4a1965a23861fa3dfcea5c5544cafc03',
        case_inventory("""
            ClaudeInboxRecallTests.test_both_hook_orders_hold_activation_decision_across_manifest_flip
            ClaudeInboxRecallTests.test_missing_and_changed_prompt_owner_refuse_before_source
            ClaudeInboxRecallTests.test_pin_drift_after_enabled_latch_refuses_without_source_reads
        """, Evidence('fake-boundary', 'supported', ('REL-STREAM', 'REL-IDENTITY'), ('claude:memory', 'claude:coordination'), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'), {
        })),
    Suite(
        'legacy-memory',
        'scripts/test_inbox_recall_bridge.py',
        '7133f88fe4f8d14b41c8a46f9b4dd1d9ebaabced70606fc53c4cd229e16a1949',
        case_inventory("""
            InboxRecallBridgeTests.test_bound_prompt_recalls_while_stop_selects_and_capture_preserves_grant
        """, Evidence('fake-boundary', 'supported', ('REL-STREAM', 'REL-IDENTITY'), ('codex:memory', 'codex:coordination'), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance.'), {
        })),
    Suite(
        'legacy-coordination',
        'scripts/test_inbox_recovery.py',
        '224605b00dfce70416c918bacc249fd419fc26f5cac925126eb6ddf6ee224e14',
        case_inventory("""
            InboxRecovery.test_acknowledging_after_turn_end_reminder_completes_cleanly
            InboxRecovery.test_active_delivery_refusal_frees_the_reason
            InboxRecovery.test_codex_user_prompt_claim_pins_exact_turn_and_rejects_takeover
            InboxRecovery.test_committed_command_replays_through_the_api_and_a_different_body_is_refused
            InboxRecovery.test_committed_compact_settlement_does_not_reopen_forgotten_source
            InboxRecovery.test_committed_compact_settlement_refuses_forgotten_payload
            InboxRecovery.test_compact_capability_rollback_keeps_issued_handle_settleable
            InboxRecovery.test_compact_handle_reads_exact_source_and_journals_explicit_settlement
            InboxRecovery.test_compact_handle_refuses_wrong_session_expiry_forgetting_and_restore
            InboxRecovery.test_compact_handle_rejects_rebound_context_before_api_access
            InboxRecovery.test_compact_ignored_settlement_keeps_reason_code
            InboxRecovery.test_completion_is_durable_before_it_is_sent
            InboxRecovery.test_context_routes_every_command_through_the_journal
            InboxRecovery.test_definitive_refusal_is_final_and_reconciliation_proceeds
            InboxRecovery.test_existing_and_uncertain_admissions_remain_legacy
            InboxRecovery.test_finish_reply_both_intents_are_durable_before_completion
            InboxRecovery.test_finish_reply_completion_refusal_never_publishes
            InboxRecovery.test_finish_reply_does_not_close_on_mismatched_success_response
            InboxRecovery.test_finish_reply_lost_completion_replays_then_publishes
            InboxRecovery.test_finish_reply_recovers_after_completion_before_reply_and_lost_publish
            InboxRecovery.test_finish_reply_refusal_reports_committed_completion_without_closure
            InboxRecovery.test_finish_reply_uses_committed_result_and_independent_identities
            InboxRecovery.test_finish_reply_watcher_materializes_reply_after_completion_crash
            InboxRecovery.test_journals_of_other_conversations_are_not_replayed
            InboxRecovery.test_lapsed_lease_keeps_the_hold_without_raising
            InboxRecovery.test_leave_waits_for_a_reply_journaled_after_reconciliation
            InboxRecovery.test_leave_waits_for_an_uncertain_command
            InboxRecovery.test_legacy_attempt_only_reminder_marker_stays_suppressed
            InboxRecovery.test_legacy_committed_journal_of_unknown_kind_is_kept
            InboxRecovery.test_legacy_committed_request_waits_for_its_reply
            InboxRecovery.test_legacy_notice_journal_is_final
            InboxRecovery.test_legacy_reminder_marker_allows_new_source_failure_instruction_once
            InboxRecovery.test_lost_completion_is_replayed_identically_before_reconciliation
            InboxRecovery.test_lost_new_claim_recovers_persisted_policy_and_overwrites_stale_marker
            InboxRecovery.test_matching_invalid_policy_refuses_context
            InboxRecovery.test_new_admission_pins_result_scope_before_claim
            InboxRecovery.test_notice_batch_unavailable_source_can_settle_failed_explicitly
            InboxRecovery.test_notice_batch_uses_new_handle_and_separate_settlements
            InboxRecovery.test_old_watcher_cleanup_marker_does_not_scope_another_legacy_admission
            InboxRecovery.test_only_requests_cue_memory_after_exact_task_source_read
            InboxRecovery.test_opted_in_notice_inlines_checked_source_with_short_settlement_handle
            InboxRecovery.test_oversized_compact_source_requires_read_and_failed_settlement_is_idempotent
            InboxRecovery.test_policy_cannot_bind_a_different_attempt_or_session
            InboxRecovery.test_possibly_committed_reconciliation_keeps_its_reason_and_uuid
            InboxRecovery.test_refused_command_keeps_its_identity
            InboxRecovery.test_reminder_revision_suppresses_identical_content_and_surfaces_changed_command
            InboxRecovery.test_reply_journal_survives_a_watcher_restart_and_older_state
            InboxRecovery.test_reply_journaled_after_reconciliation_is_replayed_at_stop
            InboxRecovery.test_reply_journaled_after_reconciliation_is_replayed_by_the_watcher
            InboxRecovery.test_reply_needs_the_committed_result_and_is_replayed_after_completion
            InboxRecovery.test_scoped_journal_survives_lost_reply_and_watcher_replay
            InboxRecovery.test_stale_lease_turn_end_never_emits_actionable_reminder
            InboxRecovery.test_unacknowledged_turn_end_reminder_surfaces_exact_ack_and_avoids_looping
        """, Evidence('fake-boundary', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance. Lost responses/restarts are scripted, not abrupt database/process crash acceptance.'), {
            'InboxRecovery.test_acknowledging_after_turn_end_reminder_completes_cleanly': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_codex_user_prompt_claim_pins_exact_turn_and_rejects_takeover': Evidence('fake-boundary', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'codex:issued-inbox', 'claude:recovery', 'codex:recovery'), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance. Lost responses/restarts are scripted, not abrupt database/process crash acceptance.'),
            'InboxRecovery.test_committed_command_replays_through_the_api_and_a_different_body_is_refused': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_committed_compact_settlement_does_not_reopen_forgotten_source': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_committed_compact_settlement_refuses_forgotten_payload': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_compact_capability_rollback_keeps_issued_handle_settleable': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_compact_handle_reads_exact_source_and_journals_explicit_settlement': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'codex:issued-inbox', 'claude:recovery', 'codex:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_compact_handle_refuses_wrong_session_expiry_forgetting_and_restore': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_compact_handle_rejects_rebound_context_before_api_access': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_compact_ignored_settlement_keeps_reason_code': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_completion_is_durable_before_it_is_sent': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_definitive_refusal_is_final_and_reconciliation_proceeds': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_both_intents_are_durable_before_completion': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_completion_refusal_never_publishes': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_does_not_close_on_mismatched_success_response': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_lost_completion_replays_then_publishes': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_recovers_after_completion_before_reply_and_lost_publish': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_refusal_reports_committed_completion_without_closure': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_finish_reply_uses_committed_result_and_independent_identities': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_leave_waits_for_an_uncertain_command': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_legacy_committed_request_waits_for_its_reply': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_lost_completion_is_replayed_identically_before_reconciliation': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_new_admission_pins_result_scope_before_claim': Evidence('fake-boundary', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'codex:issued-inbox', 'claude:recovery', 'codex:recovery'), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance. Lost responses/restarts are scripted, not abrupt database/process crash acceptance.'),
            'InboxRecovery.test_notice_batch_unavailable_source_can_settle_failed_explicitly': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_notice_batch_uses_new_handle_and_separate_settlements': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_oversized_compact_source_requires_read_and_failed_settlement_is_idempotent': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_refused_command_keeps_its_identity': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_reply_needs_the_committed_result_and_is_replayed_after_completion': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
            'InboxRecovery.test_scoped_journal_survives_lost_reply_and_watcher_replay': Evidence('real-process', 'supported', ('REL-STREAM', 'REL-PROCESS'), ('claude:issued-inbox', 'claude:recovery'), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Journal/API outcomes are scripted; restart/crash names do not prove abrupt process death.'),
        })),
    Suite(
        'legacy-coordination',
        'integrations/lifecycle/test_native_session.py',
        'd1d41372735ff299fec80244bcc04e835322e47a50a870148f6542b3ff499db5',
        case_inventory("""
            TransitionTableTests.test_admit_intends_then_claims_or_forgets
            TransitionTableTests.test_batch_advance_final_refusal_drops_intent_and_transport_doubt_keeps_it
            TransitionTableTests.test_end_turn_names_completion_only_when_the_journal_proves_it
            TransitionTableTests.test_latches_and_attempt_refresh_persist_once
            TransitionTableTests.test_open_session_locks_and_loads_the_record
            TransitionTableTests.test_recover_replays_batch_advance_before_claiming
            TransitionTableTests.test_release_reconciles_under_one_saved_close_then_clears
            TransitionTableTests.test_renew_marks_a_lapsed_lease_and_keeps_the_hold
            TransitionTableTests.test_retire_leaves_releases_and_retires_or_refuses_while_uncertain
            TransitionTableTests.test_settle_records_the_outcome_for_every_recovery_shape
        """, Evidence('fake-boundary', 'supported', ('REL-IDENTITY', 'REL-PROCESS'), ('codex:coordination', 'codex:recovery'), 'In-process MemoryHost transition table; no native host/API execution.'), {
        })),
    Suite(
        'legacy-coordination',
        'integrations/lifecycle/test_unix_peer_transport.py',
        '1cbc130943d9c2391ab22660cdf66eb2a14d95ef515c8609d444932dd13c4f29',
        case_inventory("""
            UnixPeerTransportTests.test_bounded_peer_socket_and_line_limits
            UnixPeerTransportTests.test_open_verified_socket_and_json_line_exchange
            UnixPeerTransportTests.test_open_verified_socket_rejects_mismatched_peer
            UnixPeerTransportTests.test_proc_stat_and_process_liveness
            UnixPeerTransportTests.test_stale_socket_cleanup_and_private_bind
            UnixPeerTransportTests.test_wake_transport_registry_and_dispatch
        """, Evidence('source-only', 'supported', ('REL-IDENTITY', 'REL-PROCESS'), ('shared:peer',), 'Actual same-process Unix sockets/UID checks; no independent peer process or installed wake.'), {
        })),
    Suite(
        'legacy-coordination',
        'scripts/test_activation_contention.py',
        'be2ebd69a02654a4f163582d6cc2864f4fe5cefcb23a167827b1d4c87e119cf5',
        case_inventory("""
            ActivationContentionTests.test_persistent_contention_refuses_without_reads_or_accounting_changes
            ActivationContentionTests.test_same_wake_hooks_share_activation_despite_overlapping_fsync
            ActivationContentionTests.test_slow_concurrent_activation_writer_does_not_veto_wake
        """, Evidence('fake-boundary', 'supported', ('REL-PROCESS', 'REL-STREAM'), ('claude:coordination',), 'Source assertions with synthetic API/model or host boundary; not installed-effectful or live acceptance. Real flock; full crash/drain/barrier qualification absent.'), {
            'ActivationContentionTests.test_same_wake_hooks_share_activation_despite_overlapping_fsync': Evidence('real-process', 'supported', ('REL-PROCESS', 'REL-STREAM'), ('claude:coordination',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Concurrent actual hook child and real fsync/flock; no full writer-crash campaign.'),
            'ActivationContentionTests.test_slow_concurrent_activation_writer_does_not_veto_wake': Evidence('real-process', 'supported', ('REL-PROCESS', 'REL-STREAM'), ('claude:coordination',), 'Actual subprocess/OS effects asserted by this fixture, with synthetic peers/API/model selectors; not native-account or production acceptance. Concurrent actual hook child and real fsync/flock; no full writer-crash campaign.'),
        })),
    Suite(
        'legacy-coordination',
        'scripts/test_idle_wakeup.py',
        '83975cebd94029fc3af92057e46cb69fe5233775813a91a9da153d31908084a8',
        case_inventory("""
            IdleWakeup.test_busy_blocked_unknown_or_other_session_never_get_prompted
            IdleWakeup.test_claude_channel_uncertain_is_retained_without_fallback
            IdleWakeup.test_codex_without_native_queue_never_falls_back_even_with_matching_rollout
            IdleWakeup.test_interrupt_restores_presence_without_touching_inbox_work
        """, Evidence('fake-boundary', 'unsupported-expectation', ('REL-IDENTITY', 'REL-PROCESS'), ('shared:wake',), 'Synthetic host/queue wake guard expectations; no successful natural native wake cycle.'), {
            'IdleWakeup.test_busy_blocked_unknown_or_other_session_never_get_prompted': Evidence('fake-boundary', 'unsupported-expectation', ('REL-IDENTITY', 'REL-PROCESS'), ('claude:wake', 'codex:wake', 'opencode:wake', 'agy:wake', 'hermes:wake'), 'Synthetic host/queue wake guard expectations; no successful natural native wake cycle.'),
            'IdleWakeup.test_claude_channel_uncertain_is_retained_without_fallback': Evidence('fake-boundary', 'unsupported-expectation', ('REL-IDENTITY', 'REL-PROCESS'), ('claude:wake',), 'Synthetic host/queue wake guard expectations; no successful natural native wake cycle.'),
            'IdleWakeup.test_codex_without_native_queue_never_falls_back_even_with_matching_rollout': Evidence('fake-boundary', 'unsupported-expectation', ('REL-IDENTITY', 'REL-PROCESS'), ('codex:wake',), 'Synthetic host/queue wake guard expectations; no successful natural native wake cycle.'),
            'IdleWakeup.test_interrupt_restores_presence_without_touching_inbox_work': Evidence('fake-boundary', 'supported', ('REL-IDENTITY', 'REL-PROCESS'), ('codex:coordination',), 'Synthetic host/queue wake guard expectations; no successful natural native wake cycle. Interrupt updates presence; it is not cancellation cleanup.'),
        })),
    Suite(
        'legacy-coordination',
        'scripts/test_install_wake_service.py',
        'e1617dc7b8a1f6e0c0fc05824597b4ddbdc3fed046b041e951a66fbccb4e7bbd',
        case_inventory("""
            InstallWakeServiceTests.test_concurrent_installs_do_not_collide_on_temporary_files
            InstallWakeServiceTests.test_installed_name_matches_go_case_and_duplicate_field_selection
            InstallWakeServiceTests.test_invalid_name_traversal_rejected_without_external_writes
            InstallWakeServiceTests.test_same_unit_activation_is_serialized
            InstallWakeServiceTests.test_stop_and_replace_failures_clean_up_and_preserve_config
            InstallWakeServiceTests.test_validates_exact_persisted_temporary_bytes
            InstallWakeServiceTests.test_validation_failure_cleans_up_and_does_not_persist
        """, Evidence('fake-boundary', 'closure-smoke', ('REL-PIN', 'REL-PROCESS'), ('shared:coordination',), 'Installer command/systemd boundaries scripted; no production service start or transition acceptance.'), {
        })),
)


UNVERIFIED = (
    'R-01: supported cancellation and the explicitly unselected native/adapter fixtures',
    'R-01: hook timings absent from subprocess.run observations, including Popen-only calls',
    'R-01: session-correlated hook identity, refresh and natural-cycle gaps in the private inventory',
    'REL-FRESH: complete installed-effectful matrix; closure smoke is insufficient',
    'REL-COMPAT: /3 invalid-code exit-1/no-native-veto is expected, not observed here',
    'REL-DRAIN: complete unit/barrier and runtime transition qualification',
    'REL-SEMANTIC: actual model, CPU/kernel class, S-04/S-04R and parity qualification',
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stream_record(raw):
    if raw is None:
        return {'captured': False}
    encoding = 'bytes' if isinstance(raw, bytes) else 'utf-8/surrogatepass returned text'
    encoded = raw if isinstance(raw, bytes) else raw.encode('utf-8', 'surrogatepass')
    return dict(captured=True, encoding=encoding, bytes=len(encoded),
                sha256=digest(encoded), base64=base64.b64encode(encoded).decode('ascii'))


def observe_run(run, observations, current_case):
    """Observe returned streams and wall time without changing the subprocess call."""
    def observed(*args, **kwargs):
        started = CLOCK()
        command = args[0] if args else kwargs.get('args', [])
        argv = [str(p) for p in command] if isinstance(command, (list, tuple)) else str(command)
        record = {'case_id': current_case(), 'argv': argv,
                  'timing_kind': 'subprocess.run wall time; not pure hook/model latency'}
        try:
            completed = run(*args, **kwargs)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            record.update(error_class=type(error).__name__, seconds=CLOCK() - started)
            observations.append(record)
            raise
        record.update(returncode=completed.returncode, seconds=CLOCK() - started,
                      stdout=stream_record(completed.stdout), stderr=stream_record(completed.stderr))
        observations.append(record)
        return completed
    return observed


def case_id(test):
    if isinstance(test, unittest.TestCase) and hasattr(test, '_testMethodName'):
        return type(test).__name__ + '.' + test._testMethodName
    return test.id()


class CaseResults(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.started = {}
        self.outcomes = {}
        self.active_case = None

    def startTest(self, test):
        super().startTest(test)
        self.active_case = case_id(test)
        self.started[id(test)] = CLOCK()
        self.outcomes[id(test)] = 'incomplete'

    def mark(self, test, outcome):
        self.outcomes[id(test)] = outcome
        if id(test) not in self.started:
            self.records.append(dict(id=case_id(test), outcome=outcome, seconds=0))

    def stopTest(self, test):
        self.records.append(dict(id=case_id(test), outcome=self.outcomes[id(test)],
                                 seconds=CLOCK() - self.started[id(test)]))
        self.active_case = None
        super().stopTest(test)

    def addSuccess(self, test):
        self.mark(test, 'passed')
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.mark(test, 'failed')
        super().addFailure(test, error)

    def addError(self, test, error):
        self.mark(test, 'error')
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.mark(test, 'skipped')
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, error):
        self.mark(test, 'expected_failure')
        super().addExpectedFailure(test, error)

    def addUnexpectedSuccess(self, test):
        self.mark(test, 'unexpected_success')
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.mark(test, 'failed_subtest')
        super().addSubTest(test, subtest, error)


def child_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--import-root', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--case', action='append', required=True)
    args = parser.parse_args(argv)
    started = CLOCK()
    raw = args.suite.read_bytes()
    if digest(raw) != args.sha256:
        args.report.write_text(json.dumps({'error': 'suite_hash_drift', 'cases': []}))
        return 2
    sys.path.insert(0, str(args.import_root))
    sys.path.insert(1, str(ROOT))
    spec = importlib.util.spec_from_file_location('cairn_release_fixture', args.suite)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Execute the captured, checked fixture bytes, including modules without a main guard.
    try:
        exec(compile(raw, str(args.suite), 'exec'), module.__dict__)
    except ImportError as error:
        args.report.write_text(json.dumps({'missing_prerequisite': error.name, 'cases': []}))
        return 2
    suite = unittest.TestLoader().loadTestsFromNames(args.case, module)
    setup_seconds = CLOCK() - started
    observations = []
    result_holder = []

    def result_factory(*parameters, **options):
        result = CaseResults(*parameters, **options)
        result_holder.append(result)
        return result

    original_run = subprocess.run
    subprocess.run = observe_run(original_run, observations,
                                 lambda: result_holder[0].active_case if result_holder else None)
    try:
        result = unittest.TextTestRunner(verbosity=2, resultclass=result_factory).run(suite)
    finally:
        subprocess.run = original_run
    report = dict(cases=result.records, tests_run=result.testsRun,
                  import_and_loader_seconds=setup_seconds,
                  suite_seconds=CLOCK() - started, subprocess_observations=observations,
                  timing_limits='test seconds include fixture setup/teardown; Popen-only and mocked calls are not timed')
    args.report.write_text(json.dumps(report, ensure_ascii=True) + '\n')
    return 0 if result.wasSuccessful() else 1


def fixture_environment(scratch):
    home = scratch / 'home'
    home.mkdir(parents=True)
    environment = dict(PATH=os.environ.get('PATH', os.defpath), HOME=str(home),
                       LANG='C.UTF-8', TZ='UTC', PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(scratch))
    for name in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'HERMES_HOME', 'XDG_CONFIG_HOME',
                 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
        directory = home / name.lower()
        directory.mkdir()
        environment[name] = str(directory)
    return environment


def prerequisites(suite):
    tools = {
        'scripts/test_installed_lifecycle_bundle.py': ('bwrap',),
        'scripts/test_install_agent_coordination.py': ('herdr',),
        'scripts/test_install_agent_coordination_active_turn.py': ('bwrap', '/usr/bin/node'),
    }.get(suite.path, ())
    missing = [tool for tool in tools if not shutil.which(tool)]
    if suite.path == 'scripts/test_installed_lifecycle_bundle.py' and importlib.util.find_spec('yaml') is None:
        missing.append('Python package PyYAML')
    return missing


def validate_report(suite, report, returncode):
    if not isinstance(report, dict) or not isinstance(report.get('cases'), list):
        return 'malformed report'
    records = report['cases']
    if not records or not suite.cases:
        return 'zero cases ran'
    if any(not isinstance(record, dict) or not isinstance(record.get('id'), str) for record in records):
        return 'malformed per-ID result'
    if Counter(record['id'] for record in records) != Counter(suite.cases.keys()):
        return 'executed ID multiset differs from selection'
    if type(report.get('tests_run')) is not int or report['tests_run'] != len(suite.cases):
        return 'unittest run count differs from selection'
    if any(record.get('outcome') != 'passed' for record in records):
        return 'required case did not pass (skip and expected failure are missing coverage)'
    if returncode != 0:
        return 'nonzero child exit'
    return None


def stop_child_group(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return child.communicate()


def run_child(command, environment, timeout):
    started = CLOCK()
    try:
        child = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as error:
        return dict(status='missing_coverage', missing=[type(error).__name__])
    timed_out = False
    try:
        try:
            stdout, stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout, stderr = stop_child_group(child)
    finally:
        if child.poll() is None:
            stop_child_group(child)
    record = dict(wall_seconds=CLOCK() - started, returncode=child.returncode,
                  stdout=stream_record(stdout), stderr=stream_record(stderr))
    if timed_out:
        return dict(record, status='failed', error='child timeout; owned process group killed and joined')
    return record


def run_suite(suite, scratch, timeout=180):
    source = (ROOT / suite.path).resolve()
    record = dict(profile=suite.profile, path=suite.path, expected_sha256=suite.sha256,
                  expected_count=len(suite.cases), expected_cases={k: asdict(v) for k, v in suite.cases.items()})
    missing = prerequisites(suite)
    if not source.is_file() or digest(source.read_bytes()) != suite.sha256:
        missing.append('missing fixture or suite hash drift')
    if missing:
        return dict(record, status='missing_coverage', missing=missing)
    tree = ast.parse(source.read_text())
    defined = sorted(c.name + '.' + m.name for c in tree.body if isinstance(c, ast.ClassDef)
                     for m in c.body if isinstance(m, ast.FunctionDef) and m.name.startswith('test_'))
    record['unselected_cases'] = [name for name in defined if name not in suite.cases]
    record['unselected_status'] = 'not run by R-01; not evidence for these paths'
    report_path = scratch / 'child-report.json'
    environment = fixture_environment(scratch)
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '_suite',
               '--suite', str(source), '--import-root', str(source.parent),
               '--sha256', suite.sha256, '--report', str(report_path)]
    for name in suite.cases:
        command.extend(('--case', name))
    record.update(command=command, environment=environment, timeout_seconds=timeout)
    record.update(run_child(command, environment, timeout))
    if 'status' in record:
        return record
    try:
        if report_path.stat().st_size > 32 * 1024 * 1024:
            return dict(record, status='failed', error='child report exceeds 32 MiB')
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as error:
        return dict(record, status='failed', error='missing or malformed report: ' + type(error).__name__)
    if isinstance(report, dict) and (report.get('missing_prerequisite') or report.get('error') == 'suite_hash_drift'):
        return dict(record, status='missing_coverage', missing=[report.get('missing_prerequisite') or 'suite_hash_drift'], observation=report)
    failure = validate_report(suite, report, record['returncode'])
    return dict(record, status='failed' if failure else 'passed', error=failure, observation=report)


def source_identity():
    try:
        revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        revision = None
    paths = [Path(__file__), ROOT / 'scripts/test_hybrid_release_baseline.py']
    paths += sorted((ROOT / 'integrations/lifecycle').glob('*.py'))
    return dict(git_commit=revision, files={str(p.relative_to(ROOT)): digest(p.read_bytes())
                                         for p in paths if p.is_file()})


def coverage_summary(runs):
    lanes, kinds = Counter(), Counter()
    for run in runs:
        if run['status'] != 'passed':
            continue
        for record in run['observation']['cases']:
            evidence = run['expected_cases'][record['id']]
            lanes[evidence['lane']] += 1
            kinds[evidence['row_kind']] += 1
    return dict(validated_tests_by_lane=dict(lanes), validated_tests_by_row_kind=dict(kinds),
                passed_supported_expectation_tests=kinds['supported'],
                scope='Validated fixture-test counts, not installed/live operation cycles')


def tool_identities():
    identities = {}
    for name in ('bwrap', 'herdr', 'node', 'git'):
        executable = shutil.which(name)
        identities[name] = None if executable is None else dict(
            path=executable, sha256=digest(Path(executable).read_bytes()))
    return identities


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '_suite':
        return child_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    profiles = sorted({suite.profile for suite in SUITES})
    parser.add_argument('--profile', choices=[*profiles, 'legacy-all'], required=True)
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--output', type=Path, help='new JSON file outside the checkout; stdout always contains JSON')
    args = parser.parse_args(argv)
    if args.samples < 1:
        parser.error('--samples must be positive')
    if args.output and (ROOT == args.output.resolve() or ROOT in args.output.resolve().parents):
        parser.error('--output must be outside the checkout')
    if args.output and args.output.exists():
        parser.error('--output already exists; preserve earlier samples')
    selected = [suite for suite in SUITES if args.profile in (suite.profile, 'legacy-all')]
    started = CLOCK()
    report = dict(schema=SCHEMA, observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  source=source_identity(), python=dict(executable=sys.executable, version=sys.version),
                  tool_identities=tool_identities(),
                  platform=platform.uname()._asdict(), cpu_count=os.cpu_count(),
                  selected_profile=args.profile, samples=args.samples,
                  profile_dispositions={name: ('selected' if args.profile in (name, 'legacy-all')
                                               else 'not selected; unverified') for name in profiles},
                  build=dict(status='not required by these model-free Python fixtures'),
                  unverified=list(UNVERIFIED), release_acceptance=False, runs=[])
    for sample in range(1, args.samples + 1):
        for suite in selected:
            with tempfile.TemporaryDirectory(prefix='cairn-release-baseline-') as directory:
                outcome = run_suite(suite, Path(directory))
            report['runs'].append(dict(outcome, sample=sample))
    report['missing_coverage'] = [] if report['source']['git_commit'] else ['Git source identity unavailable']
    report['coverage'] = coverage_summary(report['runs'])
    report['selected_profiles_passed'] = not report['missing_coverage'] and bool(report['runs']) and all(row['status'] == 'passed' for row in report['runs'])
    report['full_runner_wall_seconds'] = CLOCK() - started
    report['timing_conditions'] = 'Sequential suites; concurrent machine activity uncontrolled; no improvement claim'
    encoded = json.dumps(report, indent=2, ensure_ascii=True) + '\n'
    if args.output:
        with args.output.open('x') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(encoded)
    print(encoded, end='')
    return 0 if report['selected_profiles_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

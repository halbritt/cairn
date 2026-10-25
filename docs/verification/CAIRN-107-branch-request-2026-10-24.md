# CAIRN-107 branch-request boundary, 2026-10-24

The bounded fixture in
`scripts/test_trial_candidate_run_bridge.py::test_branch_request_fixture_pins_the_manual_boundary_before_runner_bridge`
starts with the standing synthetic candidate profile. It invokes the real
candidate request and preparation code, then the existing bridge and weekly
runner with the fixture's reviewed inputs and inert fake executor. The queued
draft contains exact CURRENT/CAND refs and the 126-attempt plan. The runner
receipt is checked for that same candidate request ID.

The fixture also records the missing automation edge. The candidate entry has
no runner profile or bridge selection; `candidate.launch_status()` and the
draft's launch field both return `provider_execution_unavailable`. The test's
own setup manually supplies review artifacts, weekly runner profile, and
bridge selection before invoking the runner. Its fake executor does not start
actors, providers, an operational database, or a live campaign.

Therefore the fixture proves the source-side request and the manually bridged
synthetic runner path, while pinpointing why it does not prove
`branch_request_without_manual_setup`. The campaign completion checker
correctly retains `recorded_automation_absent`. No approval, admission, or
root-issued selection is synthesized by the branch request.

## Remaining acceptance boundary

An approved automation mechanism must connect a standing branch-request
profile to a runner request without per-run operator assembly, while retaining
the independent review, root-authorized capacity/selection, usage admission,
and provider-limit stop. A synthetic fixture cannot attest to that live
automation. CAIRN-107 also still requires two consecutive complete live
weekly receipts without provider-limit losses, independent blind grades for
every changed pair, disagreement routing, and reported agreement. This fixture
does not close weekly or campaign acceptance.

# Cairn observation through native build admission

The integration test now connects the real Cairn service to the actual Striatum
Driver, producing both its memory Evidence and its consuming Change Set through
normal submission admission. It passed under a disposable database and delegated
cgroups. [Source checkpoint](https://github.com/halbritt/striatum-next/commit/b99eb6196209e1f055ecb2d22f164d95a3d51ef1).

This closes a gap between the earlier [input](native-build-input-2026-09-08.md)
and [host](native-host-2026-09-08.md) component tests. The repository's accepted
contracts and installed runtime remain unchanged. The test uses proposed
contracts, mechanical fixture planning/checks and a shell that emits a prepared
Change Set; it does not establish model-task benefit or Principal acceptance.

## Observed chain

1. Save a shareable procedure in an isolated Cairn service.
2. Acquire its exact package through the trusted observation request frontend.
3. Have the Driver produce and admit the ECR from the retained capture.
4. Produce a Work Packet naming that ECR, with its usual packet and base pins.
5. Dispatch to a separate shell backend declaring stdin transport and required
   invocation evidence. The actual LLM supervisor binds and claims the receipt.
6. Check the memory in the child's stdin, submit the prepared Change Set and
   admit it through the Driver and fixture's mechanical checks.
7. Compare the retained input version and consuming run with the admitted facts,
   and read Cairn's matching binding, launch claim and process outcome.

The test injects no folded artifact versions. Its starting catalog and planning
producer are fixtures; this distinction matters when interpreting its successful
admission. The shell does not infer a repair from memory.

## Verification

The focused Driver race run includes the native chain, observation issuance,
input resolution, the ordinary Work Graph build frontier and slash-bearing
packet identities. It passed in 33.703 seconds. Formatting, compilation and
accepted Decision source-pin lint also passed. [Metadata](native-chain-2026-09-08.json)
retains the commands and exact logs. This checkpoint adds tests and documentation;
production implementation is unchanged from the host checkpoint.

The first connected fixture reused a local-tool backend with no prompt transport.
Admission refused that mismatch. A separate fixture backend now declares stdin
and invocation evidence; no admission rule was weakened to make the test pass.

Routine retrieval also exposed two earlier Cairn notes whose status paragraphs
still described native consumption as unimplemented. Their existing records were
revised to reference the checked host source and preserve the distinction between
branch implementation and accepted enablement. Both revisions were read back.

## Remaining evidence

A real task must exercise the native path with prospective task checks and a
bounded comparison against no optional memory and the same direct material where
valid. The existing repair-task fixtures are candidates for that experiment.
Prepare and accept the owning contract changes through Striatum's existing
process before enabling the installed native path. Full Cairn usefulness remains
unestablished by this shell test.

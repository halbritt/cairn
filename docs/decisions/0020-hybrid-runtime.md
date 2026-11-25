# 0020 — Go lifecycle runtime and a Rust semantic worker

Status: accepted, 2026-11-06 (commit `0ef7fce7`). Implementation is in progress
under CAIRN-301.

## Question

Live host behavior was split across the Go `cairn` binary, two Python
subsystems (the lifecycle integrations and the semantic scripts) and small
host adapters. Which runtime should own it?

## Decision

Two binaries plus the adapters a host requires in-process.

1. Go owns the operational plane: storage, migrations, compilation, the local
   API, MCP facades, the runner, wakeups and the lifecycle logic that Python
   held. `cairn` keeps building without cgo.
2. Rust owns the isolated CPU inference worker, `cairn-semantic-worker`, behind
   the existing JSON-line boundary.
3. Host adapters (OpenCode, Hermes) stay thin and call the Go binary.

## Options not taken

- Rewrite the Go store and API contract in Rust.
- Link ONNX bindings into the Go binary through cgo.

## Where the detail is

The [consolidation plan](../plans/hybrid-runtime-consolidation.md) holds the
architecture, and the [implementation plan](../plans/hybrid-runtime-implementation.md)
and [campaign status](../plans/hybrid-runtime-campaign-status.md) hold the
sequence and remaining work. Staging, qualification, production cutover and
retirement are separate release gates.

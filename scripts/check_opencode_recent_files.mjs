// Run with Node 24+. Exercises the shipped plugin without a host or model.
import assert from "node:assert/strict"
import plugin from "../integrations/opencode/recent-files.ts"

const hooks = await plugin({ worktree: "/work" })
const read = (path, sessionID = "one", type = "file") => hooks["tool.execute.after"](
  { tool: "read", sessionID }, { metadata: { display: { type, path } } })
const search = async (args = {}, sessionID = "one", tool = "cairn_search") => {
  const output = { args: structuredClone(args) }
  await hooks["tool.execute.before"]({ tool, sessionID }, output)
  return output.args
}
const refs = (...names) => names.map(name => ({ kind: "file", name }))

assert.deepEqual(await search({ query: "guidance" }), { query: "guidance" })
await read("/work/core/currentness.go")
assert.deepEqual(await search(), { entities: refs("core/currentness.go") })
assert.deepEqual(await search({}, "two"), {})
for (const args of [{ entities: [] }, { entities: refs("explicit.go") }, { browse: true },
  { request_id: "retained-request" }, { offset: 1 }]) {
  assert.deepEqual(await search(args), args)
}
assert.deepEqual(await search({ offset: 0 }), { offset: 0, entities: refs("core/currentness.go") })
assert.deepEqual(await search({ body: "chosen note" }, "one", "cairn_remember"), { body: "chosen note" })
const snapshot = await search()
await read("/work/second.go")
assert.deepEqual(snapshot, { entities: refs("core/currentness.go") })
assert.deepEqual(await search(snapshot), snapshot)
for (const path of ["/outside/file.go", "/workmate/file.go", "/work", "/work/a:b", "/work/a\\b", "/work/a\n", "/work/\ud800", "relative.go", "/work/" + "a".repeat(513)]) {
  await read(path, "invalid")
}
await read("/work/directory", "invalid", "directory")
await hooks["tool.execute.after"]({ tool: "read", sessionID: "invalid" }, undefined)
assert.deepEqual(await search({}, "invalid"), {})
for (let i = 0; i < 17; i++) await read(`/work/file-${i}.go`, "bounded")
const bounded = (await search({}, "bounded")).entities
assert.equal(bounded.length, 16)
assert(!bounded.some(ref => ref.name === "file-0.go"))
const originalNow = Date.now
let now = originalNow()
Date.now = () => now
try {
  await read("/work/old.go", "expiry")
  now += 10 * 60 * 1000
  await read("/work/fresh.go", "expiry")
  now += 5 * 60 * 1000
  assert.deepEqual(await search({}, "expiry"), { entities: refs("fresh.go") })
  now += 15 * 60 * 1000
  assert.deepEqual(await search({}, "expiry"), {})
} finally { Date.now = originalNow }
for (let i = 0; i < 129; i++) await read("/work/one.go", `session-${i}`)
assert.deepEqual(await search({}, "session-0"), {})
assert.deepEqual(await search({}, "session-128"), { entities: refs("one.go") })
console.log("Recent-file hints preserve explicit intent, session isolation, capture, snapshots, path bounds and expiration")

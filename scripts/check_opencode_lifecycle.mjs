// Native plugin boundary behavior; synthetic host/engine, no memory or model.
import assert from "node:assert/strict"
import { mkdtemp, mkdir, readFile, writeFile, copyFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

const root = await mkdtemp(join(tmpdir(), "cairn-plugin-lifecycle-"))
try {
  await mkdir(join(root, "plugins"))
  await copyFile(new URL("../integrations/opencode/lifecycle.ts", import.meta.url), join(root, "plugins/lifecycle.ts"))
  const log = join(root, "events.jsonl")
  await writeFile(join(root, "engine.mjs"), `
    import { appendFileSync } from "node:fs";
    let input = ""; for await (const data of process.stdin) input += data;
    const event = JSON.parse(input);
    appendFileSync(${JSON.stringify(log)}, JSON.stringify(event) + "\\n");
    const recall = ["SessionStart", "UserPromptSubmit"].includes(event.hook_event_name);
    if (event.session_id === "ses_mandatory" && recall) {
      const view = event.prompt === "optional" ? {selected:[], index:[{record_id:"optional", version:1}], expanded:{selection:{record:{body:"Retained optional lesson"}}}} : {selected:[{record:{record_id:"required", version:1, body:"Required instruction " + "x".repeat(3000)}, mandatory:true}], index:[]};
      console.log(JSON.stringify({hookSpecificOutput:{additionalContext:'Cairn lifecycle memory:\\n' + JSON.stringify(view)}}));
      process.exit(0);
    }
    const text = 'Cairn lifecycle memory:\\n' + JSON.stringify({selected:[], index:[{record_id:"note", version:1}], expanded:{selection:{record:{body:"Useful PostgreSQL lesson"}}}});
    console.log(JSON.stringify(recall && !event.retained_record_ids.includes("note") ? {hookSpecificOutput:{additionalContext:text}} : {}));
  `)
  await writeFile(join(root, "cairn-lifecycle.json"), JSON.stringify({ python: process.execPath, script: join(root, "engine.mjs"), engine_config: "unused" }))
  const plugin = (await import(pathToFileURL(join(root, "plugins/lifecycle.ts")))).default
  const owner = (id = "one", sessionID = "ses_owner") => ({ info: { role: "user", id, sessionID }, parts: [{ type: "text", text: "Continue PostgreSQL migration." }] })
  const client = { session: {
    get: async ({ path }) => ({ data: { parentID: path.id === "ses_child" ? "ses_owner" : undefined } }),
    messages: async () => ({ data: [owner(), { info: { role: "assistant", summary: true }, parts: [{ type: "text", text: "PRIVATE summary" }] },
      { info: { role: "assistant" }, parts: [{ type: "reasoning", text: "PRIVATE reasoning" }, { type: "tool", state: { output: "PRIVATE tool" } },
        { type: "text", synthetic: true, text: "PRIVATE synthetic" }, { type: "text", text: "Checks pass; deploy next." }] }] }),
  } }
  const hooks = await plugin({ client, directory: root, worktree: root })
  const transform = async (messages) => { const output = { messages: structuredClone(messages) }; await hooks["experimental.chat.messages.transform"]({}, output); return output }
  const count = value => JSON.stringify(value).split("Useful PostgreSQL lesson").length - 1
  assert.equal(count(await transform([owner()])), 1)
  assert.equal(count(await transform([owner()])), 1)
  assert.equal(count(await transform([owner(), owner("two")])), 1)
  let events = (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse)
  assert.equal(events.length, 2)
  assert.deepEqual(events[1].retained_record_ids, ["note"])
  assert.equal(count(await transform([owner("child", "ses_child")])), 0)
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "ses_owner" }, { metadata: { display: { type: "file", path: join(root, "store.go") } } })
  await hooks["experimental.session.compacting"]({ sessionID: "ses_owner" }, { context: [] })
  assert.equal(count(await transform([owner()])), 0, "Compaction request must not acquire memory")
  assert.equal(count(await transform([owner("after-compact")])), 1)
  const idle = hooks.event({ event: { type: "session.idle", properties: { sessionID: "ses_owner" } } })
  await hooks.dispose()
  await idle
  events = (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse)
  const captures = events.filter(e => e.messages)
  assert.equal(captures.length, 2)
  assert(!JSON.stringify(captures).includes("PRIVATE"))
  assert(captures.every(e => e.messages.some(m => m.text === "Checks pass; deploy next.")))
  assert(events.some(e => e.tool_input?.file_path === join(root, "store.go")))
  const mandatoryHooks = await plugin({ client, directory: root, worktree: root })
  const mandatoryTransform = async (messages) => { const output = { messages: structuredClone(messages) }; await mandatoryHooks["experimental.chat.messages.transform"]({}, output); return output }
  const repeated = [owner("optional", "ses_mandatory")]
  repeated[0].parts[0].text = "optional"
  await mandatoryTransform(repeated)
  for (let i = 0; i < 8; i++) {
    repeated.push(owner("required-" + i, "ses_mandatory"))
    const rendered = JSON.stringify(await mandatoryTransform(repeated))
    assert.equal(rendered.split("Required instruction").length - 1, 1, "Mandatory-only recall must replace its prior version")
    assert.equal(rendered.split("Retained optional lesson").length - 1, 1, "Repeated mandatory context must not evict unrelated memory")
  }
  events = (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse)
  assert(events.at(-1).retained_record_ids.includes("required"))
  await mandatoryHooks.dispose()
  const before = events.length
  await writeFile(join(root, ".cairn-no-memory"), "")
  const disabled = await plugin({ client, directory: root, worktree: root })
  assert.equal(count(await (async () => { const output = { messages: [owner()] }; await disabled["experimental.chat.messages.transform"]({}, output); return output })()), 0)
  assert.equal((await readFile(log, "utf8")).trim().split("\n").length, before)
  await disabled.dispose()
  await rm(join(root, ".cairn-no-memory"))
  const warnings = []
  const origWarn = console.warn
  console.warn = msg => warnings.push(msg)
  try {
    await writeFile(join(root, "engine-err.mjs"), 'console.error("Cairn lifecycle: specific error diagnostic; use native tools or an explicit handoff."); process.exit(1);')
    await writeFile(join(root, "cairn-lifecycle.json"), JSON.stringify({ python: process.execPath, script: join(root, "engine-err.mjs"), engine_config: "unused" }))
    const errPlugin = (await import(pathToFileURL(join(root, "plugins/lifecycle.ts")) + "?v=err")).default
    const errHooks = await errPlugin({ client, directory: root, worktree: root })
    await errHooks["experimental.chat.messages.transform"]({}, { messages: [owner("err", "ses_err")] })
    await errHooks.dispose()
    assert(warnings.some(w => w.includes("specific error diagnostic")), "warn must preserve stderr diagnostics")
  } finally {
    console.warn = origWarn
  }
  console.log("OpenCode lifecycle retains request context, isolates child/compaction traffic, filters capture, flushes idle work, reports diagnostics and honors opt-out")
} finally { await rm(root, { recursive: true, force: true }) }

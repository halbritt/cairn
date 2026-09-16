// Real Python coordinator/API with the installed OpenCode plugin interface.
import assert from "node:assert/strict"
import { mkdir, copyFile, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

const [root, python, script, config] = process.argv.slice(2)
await mkdir(join(root, "plugins"), { recursive: true })
await copyFile(new URL("../integrations/opencode/coordination.ts", import.meta.url), join(root, "plugins/coordination.ts"))
await writeFile(join(root, "cairn-coordination.json"), JSON.stringify({ python, script, config }))
const plugin = (await import(pathToFileURL(join(root, "plugins/coordination.ts")))).default
const hooks = await plugin({ directory: root })
const owner = { info: { role: "user", id: "msg_probe", sessionID: "ses_coordination", model: { providerID: "fixture", modelID: "probe" } },
  parts: [{ type: "text", text: "PRIVATE PROMPT NOT FOR DIRECTORY" }] }
for (let i = 0; i < 2; i++) {
  const request = { messages: structuredClone([owner]) }
  await hooks["experimental.chat.messages.transform"]({}, request)
  assert(request.messages[0].parts.some(p => p.synthetic && p.text.includes("Cairn session agent-")))
  assert.equal(owner.parts.length, 1, "Plugin mutated retained history")
}
await hooks.event({ event: { type: "session.idle", properties: { sessionID: owner.info.sessionID } } })
await hooks.dispose()
console.log("OpenCode adapter injects native identity on request copies and leaves on disposal")

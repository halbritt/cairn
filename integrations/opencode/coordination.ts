// Native conversation identity/presence; no transcript or prompt capture.
import type { Plugin } from "@opencode-ai/plugin"
import type { Part } from "@opencode-ai/sdk"
import { execFile } from "node:child_process"
import { readFile } from "node:fs/promises"
import { randomUUID } from "node:crypto"

const plugin: Plugin = async ({ directory }) => {
  const config = JSON.parse(await readFile(new URL("../cairn-coordination.json", import.meta.url), "utf8"))
  const sessions = new Map<string, { turn: string; context: string }>()
  const jobs = new Map<string, Promise<void>>()
  let closing = false
  function invoke(id: string, event: string, model = ""): Promise<any> {
    return new Promise((resolve, reject) => {
      const child = execFile(config.python, [config.script, "hook", "--config", config.config],
        { encoding: "utf8", timeout: 15000, maxBuffer: 32768 }, (error, stdout) => {
          if (error) return reject(new Error("Cairn session presence unavailable"))
          try { resolve(JSON.parse(stdout)) } catch { reject(new Error("Invalid Cairn presence response")) }
        })
      child.stdin?.on("error", reject)
      child.stdin?.end(JSON.stringify({ session_id: id, cwd: directory, host_pid: process.pid, hook_event_name: event, model }))
    })
  }
  function enqueue(id: string, work: () => Promise<void>): Promise<void> {
    const job = (jobs.get(id) ?? Promise.resolve()).then(work).catch(error => console.warn(String(error)))
    jobs.set(id, job)
    void job.finally(() => { if (jobs.get(id) === job) jobs.delete(id) })
    return job
  }
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      if (closing || !output.messages.length) return
      const owner = [...output.messages].reverse().find(m => m.info.role === "user" && m.parts.some(p => p.type === "text" && !p.synthetic && !p.ignored))
      if (!owner || owner.info.role !== "user") return
      const info = owner.info
      await enqueue(info.sessionID, async () => {
        let state = sessions.get(info.sessionID)
        if (!state || state.turn !== info.id) {
          const model = info.model ? `${info.model.providerID}/${info.model.modelID}` : ""
          const result = await invoke(info.sessionID, "TurnStart", model)
          state = { turn: info.id, context: result.hookSpecificOutput?.additionalContext ?? "" }
          sessions.set(info.sessionID, state)
        }
        if (state.context && !owner.parts.some(p => p.type === "text" && p.text === state.context))
          owner.parts.push({ type: "text", text: state.context, synthetic: true,
            id: "prt_" + randomUUID().replaceAll("-", ""), sessionID: info.sessionID, messageID: info.id } as Part)
      })
    },
    event: async ({ event }) => {
      if (event.type === "session.idle" && sessions.has(event.properties.sessionID) && !closing)
        await enqueue(event.properties.sessionID, async () => { await invoke(event.properties.sessionID, "TurnEnd") })
      if (event.type === "session.deleted" && sessions.has(event.properties.info.id)) {
        const id = event.properties.info.id
        await enqueue(id, async () => { await invoke(id, "SessionEnd"); sessions.delete(id) })
      }
    },
    dispose: async () => {
      closing = true
      await Promise.all([...jobs.values()])
      await Promise.all([...sessions.keys()].map(id => enqueue(id, async () => { await invoke(id, "SessionEnd") })))
      sessions.clear()
    },
  }
}
export default plugin

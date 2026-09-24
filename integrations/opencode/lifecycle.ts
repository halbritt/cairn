// Authorized lifecycle memory; ordinary Cairn tools keep their own permissions.
import type { Plugin } from "@opencode-ai/plugin"
import type { Part } from "@opencode-ai/sdk"
import { execFile } from "node:child_process"
import { readFile, access } from "node:fs/promises"
import { join } from "node:path"
import { randomUUID } from "node:crypto"

type Block = { text: string; ids: string[] }
// skills: routed skill instructions per owner message, retained apart from the memory budget.
type Session = { started: boolean; turn?: string; blocks: Map<string, Block>; skills: Map<string, string> }
const budget = 12000
const plugin: Plugin = async ({ client, directory, worktree }) => {
  const config = JSON.parse(await readFile(new URL("../cairn-lifecycle.json", import.meta.url), "utf8"))
  const sessions = new Map<string, Session>()
  const pending = new Map<string, Promise<void>>()
  const compacting = new Set<string>()
  let closing = false
  async function disabled() {
    if (process.env.CAIRN_LIFECYCLE_DISABLED === "1" || process.env.CAIRN_LIFECYCLE_CHILD === "1") return true
    for (const root of [directory, worktree]) {
      try { await access(join(root, ".cairn-no-memory")); return true }
      catch (error: any) { if (error.code !== "ENOENT") throw error }
    }
    return false
  }
  function warn(error?: any) {
    if (error?.message && error.message.startsWith("Cairn lifecycle: ")) {
      console.warn(error.message)
    } else {
      console.warn("Cairn lifecycle: operation failed" + (error?.message ? ": " + error.message : "") + "; use native memory tools or an explicit handoff.")
    }
  }
  async function invoke(id: string, event: object): Promise<any> {
    if (await disabled()) return {}
    return new Promise((resolve, reject) => {
      const child = execFile(config.python, [config.script, "--config", config.engine_config],
        { encoding: "utf8", timeout: 150000, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
          if (error) return reject(new Error(stderr?.trim() || error.message || "Cairn lifecycle command failed"))
          try { resolve(JSON.parse(stdout)) } catch { reject(new Error("Invalid lifecycle response")) }
        })
      child.stdin?.on("error", reject)
      child.stdin?.end(JSON.stringify({ session_id: id, cwd: directory, ...event }))
    })
  }
  function enqueue(id: string, job: () => Promise<void>): Promise<void> {
    const next = (pending.get(id) ?? Promise.resolve()).then(job).catch(warn)
    pending.set(id, next)
    void next.finally(() => { if (pending.get(id) === next) pending.delete(id) })
    return next
  }
  async function session(id: string): Promise<Session | undefined> {
    const known = sessions.get(id)
    if (known) return known
    if (sessions.size >= 128 || closing || await disabled()) return
    const response = await client.session.get({ path: { id }, signal: AbortSignal.timeout(5000) })
    if (response.error || !response.data) throw new Error("Host session unavailable")
    if (response.data.parentID) return // only owner-facing sessions
    const state = { started: false, blocks: new Map<string, Block>(), skills: new Map<string, string>() }
    sessions.set(id, state)
    return state
  }
  function text(parts: any[]) {
    return parts.filter(p => p.type === "text" && !p.synthetic && !p.ignored).map(p => p.text).join("\n")
  }
  async function capture(id: string, event: string) {
    if (await disabled() || !await session(id)) return
    const response = await client.session.messages({ path: { id }, query: { limit: 64 }, signal: AbortSignal.timeout(5000) })
    if (response.error || !Array.isArray(response.data)) throw new Error("Host dialogue unavailable")
    const messages = response.data.filter(m => !('summary' in m.info && m.info.summary === true))
      .map(m => ({ role: m.info.role, text: text(m.parts) })).filter(m => m.text.trim())
    // Bound dialogue before serializing the subprocess input. The engine applies
    // its own UTF-8/JSON budget and never receives tool payloads or reasoning.
    const bounded: typeof messages = []
    let room = 24000
    for (const message of messages.reverse()) {
      if (!room) break
      const bytes = Buffer.from(message.text)
      const value = bytes.length <= room ? message.text : "[excerpt truncated]\n" + bytes.subarray(0, room).toString("utf8")
      bounded.unshift({ role: message.role, text: value })
      room -= Math.min(bytes.length, room)
    }
    await invoke(id, { hook_event_name: event, messages: bounded })
  }
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      const owner = [...output.messages].reverse().find(m => m.info.role === "user" && text(m.parts).trim())
      if (!owner || await disabled()) return
      const id = owner.info.sessionID
      if (compacting.delete(id)) return
      await enqueue(id, async () => {
        const state = await session(id)
        if (!state) return
        const visible = new Set(output.messages.map(m => m.info.id))
        for (const key of state.blocks.keys()) if (!visible.has(key)) state.blocks.delete(key)
        for (const key of state.skills.keys()) if (!visible.has(key)) state.skills.delete(key)
        if (state.turn !== owner.info.id) {
          const result = await invoke(id, {
            hook_event_name: state.started ? "UserPromptSubmit" : "SessionStart", source: "startup",
            prompt: text(owner.parts), retained_record_ids: [...state.blocks.values()].flatMap(b => b.ids),
          })
          state.started = true
          state.turn = owner.info.id
          const skill = result.cairn_skill?.text
          if (typeof skill === "string" && skill) state.skills.set(owner.info.id, skill)
          const context = result.hookSpecificOutput?.additionalContext
          if (context) {
            const start = context.indexOf('{"selected":')
            const view = JSON.parse(context.slice(start))
            const ids = [...new Set<string>([
              ...(view.index ?? []).map((e: any) => e.record_id),
              ...(view.selected ?? []).map((e: any) => e.record.record_id),
            ])]
            // Replace old versions, and keep one combined request budget.
            for (const [key, block] of state.blocks) if (block.ids.some(i => ids.includes(i))) state.blocks.delete(key)
            while (state.blocks.size && Buffer.byteLength(context) + [...state.blocks.values()].reduce((n, b) => n + Buffer.byteLength(b.text), 0) > budget)
              state.blocks.delete(state.blocks.keys().next().value!)
            if (Buffer.byteLength(context) > budget) throw new Error("Memory context exceeds budget")
            state.blocks.set(owner.info.id, { text: context, ids })
          }
        }
        // Transform the request copy, not persisted messages or title requests.
        for (const message of output.messages) {
          for (const extra of [state.skills.get(message.info.id), state.blocks.get(message.info.id)?.text])
            if (extra && !message.parts.some(p => p.type === "text" && p.text === extra))
              message.parts.push({ type: "text", text: extra, synthetic: true,
                id: "prt_" + randomUUID().replaceAll("-", ""), sessionID: id, messageID: message.info.id } as Part)
        }
      })
    },
    "tool.execute.after": async (input, output) => {
      if (!["read", "edit", "write"].includes(input.tool)) return
      const display = output.metadata?.display
      const path = display?.type === "file" ? display.path : input.tool !== "read" ? input.args?.filePath : undefined
      if (typeof path !== "string") return
      await enqueue(input.sessionID, async () => {
        if (await session(input.sessionID)) await invoke(input.sessionID, {
          hook_event_name: "PostToolUse", tool_name: input.tool[0].toUpperCase() + input.tool.slice(1), tool_input: { file_path: path },
        })
      })
    },
    "experimental.session.compacting": async ({ sessionID }) => {
      await enqueue(sessionID, () => capture(sessionID, "PreCompact"))
      compacting.add(sessionID)
      const state = sessions.get(sessionID)
      if (state) { state.started = false; state.turn = undefined; state.blocks.clear() }
    },
    event: async ({ event }) => {
      if (event.type === "message.part.updated") {
        const part = event.properties.part
        if (part.type === "tool" && part.state.status === "error" && sessions.has(part.sessionID))
          await enqueue(part.sessionID, () => invoke(part.sessionID, {
            hook_event_name: "PostToolUseFailure", error: part.state.status === "error" ? part.state.error.slice(0, 4096) : "",
          }))
      }
      if (event.type === "session.error" && event.properties.sessionID) compacting.delete(event.properties.sessionID)
      if (event.type === "session.idle" && sessions.has(event.properties.sessionID) && !closing)
        await enqueue(event.properties.sessionID, () => capture(event.properties.sessionID, "SessionEnd"))
      if (event.type === "session.deleted") sessions.delete(event.properties.info.id)
    },
    dispose: async () => {
      closing = true
      await Promise.all([...pending.values()])
      sessions.clear()
    },
  }
}
export default plugin

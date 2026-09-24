// Native conversation identity/presence and atomic idle-only admission bridge.
import type { Plugin } from "@opencode-ai/plugin"
import type { Part } from "@opencode-ai/sdk"
import { execFile } from "node:child_process"
import { readFile } from "node:fs/promises"
import { existsSync, unlinkSync, chmodSync } from "node:fs"
import { randomUUID } from "node:crypto"
import net from "node:net"

const MAX_PAYLOAD_BYTES = 65536
const MAX_CONCURRENT_CLIENTS = 4

interface PluginInstance {
  id: string
  directory: string
  client: any
  sessions: Map<string, { turn: string; context: string }>
  admissions: Map<string, Admission>
  disposed: boolean
}

interface Admission {
  requestID: string
  deliveryID: string
  text: string
  settled: Promise<boolean>
  resolve: (accepted: boolean) => void
}

interface SharedBridge {
  server: net.Server
  path: string
  instances: Map<string, PluginInstance>
  bound: boolean
}

let activeBridge: SharedBridge | null = null

function getActiveInstance(sessionId?: string): PluginInstance | null {
  if (!activeBridge) return null
  const live = [...activeBridge.instances.values()].filter(i => !i.disposed)
  if (live.length === 0) return null
  if (sessionId) {
    const owner = live.find(i => i.sessions.has(sessionId) || i.admissions.has(sessionId))
    if (owner) return owner
  }
  // An unknown session in a multi-instance process has no safe owner.
  return live.length === 1 ? live[0] : null
}

const plugin: Plugin = async ({ directory, client }) => {
  let config: any = null
  const envConfigPath = process.env.CAIRN_COORDINATION_CONFIG
  if (envConfigPath) {
    try {
      config = JSON.parse(await readFile(envConfigPath, "utf8"))
    } catch (err: any) {
      throw new Error(`Failed to load CAIRN_COORDINATION_CONFIG from ${envConfigPath}: ${err?.message}`)
    }
  } else {
    try {
      config = JSON.parse(await readFile(new URL("../cairn-coordination.json", import.meta.url), "utf8"))
    } catch {
      try {
        config = JSON.parse(await readFile(new URL("../../cairn-coordination.json", import.meta.url), "utf8"))
      } catch {
        throw new Error("Cairn coordination config not found")
      }
    }
  }
  const sessions = new Map<string, { turn: string; context: string }>()
  const admissions = new Map<string, Admission>()
  const jobs = new Map<string, Promise<void>>()
  let closing = false

  const instanceId = randomUUID()
  const instance: PluginInstance = {
    id: instanceId,
    directory,
    client,
    sessions,
    admissions,
    disposed: false,
  }

  function invoke(id: string, event: string, model = "", turn_id = "", admission?: Admission): Promise<any> {
    return new Promise((resolve, reject) => {
      const child = execFile(config.python, [config.script, "hook", "--config", config.config],
        { encoding: "utf8", timeout: 15000, maxBuffer: 32768 }, (error, stdout) => {
          if (error) return reject(new Error("Cairn session presence unavailable"))
          try { resolve(JSON.parse(stdout)) } catch { reject(new Error("Invalid Cairn presence response")) }
        })
      child.stdin?.on("error", reject)
      child.stdin?.end(JSON.stringify({
        session_id: id,
        cwd: directory,
        host_pid: process.pid,
        hook_event_name: event,
        model,
        turn_id: turn_id || undefined,
        request_id: admission?.requestID,
        delivery_id: admission?.deliveryID,
        prompt: admission?.text,
      }))
    })
  }

  function enqueue(id: string, work: () => Promise<void>): Promise<void> {
    const job = (jobs.get(id) ?? Promise.resolve()).then(work).catch(error => console.warn(String(error)))
    jobs.set(id, job)
    void job.finally(() => { if (jobs.get(id) === job) jobs.delete(id) })
    return job
  }

  // Private local bridge for native submission without touching TUI composer
  const bridgePath = `/tmp/cairn-opencode-${process.pid}.sock`

  const disposeInstance = () => {
    if (instance.disposed) return
    instance.disposed = true
    process.removeListener("exit", disposeInstance)
    if (activeBridge) {
      activeBridge.instances.delete(instanceId)
      if (activeBridge.instances.size === 0) {
        const { server, path, bound } = activeBridge
        activeBridge = null
        try { server.close() } catch {}
        if (bound) {
          try { if (existsSync(path)) unlinkSync(path) } catch {}
        }
      }
    }
  }
  process.on("exit", disposeInstance)

  if (!activeBridge) {
    const bridgeServer = net.createServer((socket) => {
      socket.setTimeout(4000)
      socket.on("timeout", () => {
        socket.destroy()
      })
      socket.on("error", () => {
        // Socket transmission error handled gracefully
      })

      let buffer = ""
      socket.on("data", async (chunk) => {
        if (buffer.length + chunk.length > MAX_PAYLOAD_BYTES) {
          try {
            socket.write(JSON.stringify({ id: null, error: { code: -32600, message: "PAYLOAD_TOO_LARGE: exceeds 64KB limit" } }) + "\n")
          } catch {}
          socket.destroy()
          return
        }
        buffer += chunk.toString("utf8")

        let lineEnd = buffer.indexOf("\n")
        while (lineEnd !== -1) {
          const line = buffer.slice(0, lineEnd).trim()
          buffer = buffer.slice(lineEnd + 1)
          lineEnd = buffer.indexOf("\n")
          if (!line) continue

          let request: any
          try {
            request = JSON.parse(line)
          } catch {
            socket.write(JSON.stringify({ id: null, error: { code: -32700, message: "Parse error" } }) + "\n")
            continue
          }

          // Strict envelope validation: must be a non-null, non-array object
          if (!request || typeof request !== "object" || Array.isArray(request)) {
            socket.write(JSON.stringify({ id: null, error: { code: -32600, message: "Invalid Request: envelope must be a JSON object" } }) + "\n")
            continue
          }

          const id = (request.id !== undefined && request.id !== null) ? request.id : null
          const method = typeof request.method === "string" ? request.method : ""
          const params = (request.params && typeof request.params === "object" && !Array.isArray(request.params))
            ? request.params
            : {}

          const currentInstance = getActiveInstance(params.session_id)
          if (!currentInstance) {
            socket.write(JSON.stringify({ id, error: { code: -32000, message: "No active plugin instance available" } }) + "\n")
            continue
          }
          const instClient = currentInstance.client

          try {
            if (method === "session/capabilities") {
              const session_id = params.session_id
              if (typeof session_id !== "string" || !session_id.trim()) {
                socket.write(JSON.stringify({ id, error: { code: -32602, message: "session_id must be a nonempty string" } }) + "\n")
                continue
              }
              socket.write(JSON.stringify({ id, result: {
                session_id,
                prompt_idle: typeof instClient?.session?.promptIdle === "function",
              } }) + "\n")
              continue
            } else if (method === "session/prompt_idle") {
              const { session_id, text, delivery_id, request_id, expected_session_id } = params
              if ([session_id, text, delivery_id, request_id].some(value => typeof value !== "string" || !value.trim())) {
                socket.write(JSON.stringify({ id, error: { code: -32602, message: "session_id, text, delivery_id, and request_id must be nonempty strings" } }) + "\n")
                continue
              }
              if (!expected_session_id || typeof expected_session_id !== "string" || !expected_session_id.trim()) {
                socket.write(JSON.stringify({ id, error: { code: -32602, message: "expected_session_id required as nonempty string" } }) + "\n")
                continue
              }
              if (expected_session_id !== session_id) {
                socket.write(JSON.stringify({ id, error: { code: -32001, message: "SESSION_MISMATCH: target session does not match expected" } }) + "\n")
                continue
              }

              // A patched native server is required; the old promptAsync route
              // does not reserve ownership atomically with its idle check.
              if (!instClient?.session?.promptIdle) {
                socket.write(JSON.stringify({
                  id,
                  error: {
                    code: -32004,
                    message: "UNSUPPORTED_CONTROL: native prompt_idle capability unavailable"
                  }
                }) + "\n")
                continue
              }

              // Verify session existence via SDK and inspect typed response
              if (instClient?.session?.get) {
                const getRes = await instClient.session.get({ path: { id: session_id } })
                if (getRes.error || !getRes.data) {
                  const errMsg = (getRes.error as any)?.message || "session not found"
                  socket.write(JSON.stringify({ id, error: { code: -32002, message: `SESSION_NOT_FOUND: ${errMsg}` } }) + "\n")
                  continue
                }
              }

              if (currentInstance.admissions.has(session_id)) {
                socket.write(JSON.stringify({ id, error: { code: -32600, message: "BUSY: another Cairn admission is pending" } }) + "\n")
                continue
              }
              // Register before the native call: its model hook may run before
              // the HTTP response. The hook waits for the admission result.
              let resolve!: (accepted: boolean) => void
              const settled = new Promise<boolean>(done => { resolve = done })
              const admission: Admission = { requestID: request_id, deliveryID: delivery_id, text, settled, resolve }
              currentInstance.admissions.set(session_id, admission)
              let promptRes: any
              try {
                promptRes = await instClient.session.promptIdle({
                  path: { id: session_id },
                  body: { requestID: request_id, parts: [{ type: "text", text }] },
                })
              } catch (err) {
                admission.resolve(false)
                currentInstance.admissions.delete(session_id)
                throw err
              }
              if (promptRes?.error) {
                admission.resolve(false)
                currentInstance.admissions.delete(session_id)
                // A successful session.get above means a 404 here is the
                // unpatched route, not a missing session.
                const missingRoute = promptRes.response?.status === 404
                const errMsg = (promptRes.error as any)?.message || "prompt_idle outcome unknown"
                socket.write(JSON.stringify({
                  id,
                  error: {
                    code: missingRoute ? -32004 : -32000,
                    message: missingRoute ? `UNSUPPORTED_CONTROL: prompt_idle route missing (${errMsg})` :
                      `PROMPT_OUTCOME_UNCERTAIN: ${errMsg}`
                  }
                }) + "\n")
                continue
              }

              const outcome = promptRes?.data?.status
              if (outcome !== "accepted") {
                admission.resolve(false)
                currentInstance.admissions.delete(session_id)
                const busy = outcome === "busy"
                const refused = outcome === "conflict"
                const prior = ["completed", "cancelled", "failed"].includes(outcome)
                socket.write(JSON.stringify({ id, error: {
                  code: busy ? -32600 : refused ? -32003 : prior ? -32005 : -32000,
                  message: busy ? "BUSY: native idle admission refused" :
                    refused ? `CONFLICT: native idle admission returned ${outcome}` :
                    prior ? `ALREADY_ADMITTED: native request is ${outcome}` :
                    "PROMPT_OUTCOME_UNCERTAIN: invalid native admission response",
                } }) + "\n")
                continue
              }
              admission.resolve(true)

              socket.write(JSON.stringify({
                id,
                result: {
                  queued: true,
                  session_id,
                  queued_id: delivery_id,
                  request_id,
                  started: true
                }
              }) + "\n")
            } else if (method === "session/abort") {
              // OpenCode SDK's session.abort lacks atomic turn-fencing; an in-flight check-then-act
              // race can kill newer owner work. Retain strict refusal (-32004 UNSUPPORTED_CONTROL)
              // pending native atomic turn admission/cancel support.
              socket.write(JSON.stringify({
                id,
                error: {
                  code: -32004,
                  message: "UNSUPPORTED_CONTROL: native session abort is disabled to protect newer owner work; requires atomic turn fencing"
                }
              }) + "\n")
              continue
            } else if (method === "session/status") {
              const { session_id } = params
              let status = "idle"
              if (instClient?.session?.status) {
                const statusRes = await instClient.session.status()
                if (statusRes.error) {
                  socket.write(JSON.stringify({ id, error: { code: -32000, message: "STATUS_FAILED" } }) + "\n")
                  continue
                }
                const statusData = (statusRes.data as any) || statusRes
                status = statusData?.[session_id]?.type || "idle"
              }
              const state = currentInstance.sessions.get(session_id)
              socket.write(JSON.stringify({
                id,
                result: { session_id, status, current_turn: state?.turn || "" }
              }) + "\n")
            } else {
              socket.write(JSON.stringify({ id, error: { code: -32601, message: `Method ${method} not found` } }) + "\n")
            }
          } catch (err: any) {
            socket.write(JSON.stringify({ id, error: { code: -32000, message: err?.message || "Internal error" } }) + "\n")
          }
        }
      })
    })

    bridgeServer.maxConnections = MAX_CONCURRENT_CLIENTS

    bridgeServer.on("error", (err: any) => {
      console.warn(`OpenCode bridge server error: ${err?.message || err}`)
      if (activeBridge) activeBridge.bound = false
    })

    const startListening = () => {
      try {
        bridgeServer.listen(bridgePath, () => {
          if (activeBridge) activeBridge.bound = true
          try {
            chmodSync(bridgePath, 0o600)
          } catch {}
        })
      } catch (err: any) {
        console.warn(`Failed to bind OpenCode bridge socket: ${err?.message}`)
      }
    }

    activeBridge = {
      server: bridgeServer,
      path: bridgePath,
      instances: new Map([[instanceId, instance]]),
      bound: false,
    }

    if (existsSync(bridgePath)) {
      const probe = net.connect(bridgePath)
      probe.on("connect", () => {
        probe.destroy()
        console.warn(`Another OpenCode bridge is active at ${bridgePath}; skipping bind`)
      })
      probe.on("error", () => {
        try { unlinkSync(bridgePath) } catch {}
        startListening()
      })
    } else {
      startListening()
    }
  } else {
    activeBridge.instances.set(instanceId, instance)
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
          const admission = admissions.get(info.sessionID)
          const matched = admission && owner.parts.some(p => p.type === "text" && p.text === admission.text && !p.synthetic && !p.ignored)
            ? admission : undefined
          const accepted = matched ? await matched.settled : false
          const result = await invoke(info.sessionID, "TurnStart", model, info.id, accepted ? matched : undefined)
          if (accepted && admissions.get(info.sessionID) === matched) admissions.delete(info.sessionID)
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
        await enqueue(event.properties.sessionID, async () => {
          const session = sessions.get(event.properties.sessionID)
          await invoke(event.properties.sessionID, "TurnEnd", "", session?.turn)
          admissions.delete(event.properties.sessionID)
        })
      if (event.type === "session.deleted" && sessions.has(event.properties.info.id)) {
        const id = event.properties.info.id
        await enqueue(id, async () => { await invoke(id, "SessionEnd", "", sessions.get(id)?.turn); sessions.delete(id); admissions.delete(id) })
      }
    },
    dispose: async () => {
      if (instance.disposed) return
      closing = true
      disposeInstance()
      await Promise.all([...jobs.values()])
      await Promise.all([...sessions.keys()].map(id => enqueue(id, async () => { await invoke(id, "SessionEnd", "", sessions.get(id)?.turn) })))
      sessions.clear()
      admissions.clear()
    },
  }
}
export default plugin

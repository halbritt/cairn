// Optional .opencode/plugins/cairn-recent-files.ts. Observes names, never contents.
import type { Plugin } from "@opencode-ai/plugin"
import { isAbsolute, relative, sep } from "node:path"

const maxFiles = 16
const maxSessions = 128
const lifetime = 15 * 60 * 1000

const plugin: Plugin = async ({ worktree }) => {
  // Instance-local, session-separated, and never persisted. Expiration is checked
  // on hook activity; an idle instance retains only this bounded map until exit.
  const sessions = new Map<string, Map<string, number>>()
  function prune(now: number) {
    for (const [id, files] of sessions) {
      for (const [name, readAt] of files) if (now - readAt >= lifetime) files.delete(name)
      if (files.size === 0) sessions.delete(id)
    }
  }
  return {
    "tool.execute.after": async (input, output) => {
      if (input.tool !== "read") return
      const now = Date.now()
      prune(now)
      const display = output?.metadata?.display
      if (display?.type !== "file" || typeof display.path !== "string" || !isAbsolute(display.path)) return
      // Without a project worktree, OpenCode can supply the filesystem root.
      if (!isAbsolute(worktree) || relative(worktree, sep) === "") return
      const name = relative(worktree, display.path).split(sep).join("/")
      if (!name || name === ".." || name.startsWith("../") || isAbsolute(name) ||
          name.trim() !== name || Buffer.byteLength(name, "utf8") > 512 || /[\\:\p{Cc}\uD800-\uDFFF]/u.test(name)) return
      const id = input.sessionID
      if (!id || Buffer.byteLength(id, "utf8") > 240) return
      const files = sessions.get(id) ?? new Map<string, number>()
      files.delete(name)
      files.set(name, now)
      while (files.size > maxFiles) files.delete(files.keys().next().value!)
      sessions.delete(id)
      sessions.set(id, files)
      while (sessions.size > maxSessions) sessions.delete(sessions.keys().next().value!)
    },
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "cairn_search") return
      prune(Date.now())
      const args = output.args
      // Explicit arguments, retained requests and later pages must not acquire
      // new intent as the agent reads more files. [] explicitly disables hints.
      if (!args || typeof args !== "object" || args.entities !== undefined || args.browse ||
          args.request_id !== undefined || (args.offset !== undefined && args.offset !== 0)) return
      const files = sessions.get(input.sessionID)
      if (files?.size) args.entities = [...files.keys()].map(name => ({ kind: "file", name }))
    },
  }
}

export default plugin

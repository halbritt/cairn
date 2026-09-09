// Install as .opencode/tools/cairn.ts; connection settings belong in .opencode/cairn.json.
import { tool, type ToolContext } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { readFile } from "node:fs/promises"
import { isAbsolute } from "node:path"
import type { ZodRawShape } from "zod"

const z = tool.schema
function validatedTool<Args extends ZodRawShape>(definition: Parameters<typeof tool<Args>>[0]) {
  const schema = z.object(definition.args).strict()
  return tool({ ...definition, async execute(args, context) {
    // OpenCode's model-facing schema does not validate the execution arguments.
    const parsed = schema.safeParse(args)
    if (!parsed.success) throw new Error("INVALID_REQUEST: invalid arguments for Cairn tool")
    return definition.execute(parsed.data, context)
  } })
}

const absolutePath = z.string().refine(isAbsolute, "Use an absolute installation path")
const settingsSchema = z.object({
  executable: absolutePath,
  socket: absolutePath,
  token_file: absolutePath,
  repo: z.string().min(1).refine(value => value !== "*"),
  tokens: z.number().int().min(256).max(1000000).default(32000),
  context: z.object({
    revision: z.string().optional(),
    workspace_sha256: z.string().optional(),
    task_class: z.string().optional(),
    binding: z.string().optional(),
    capability: z.string().optional(),
  }).strict().optional(),
}).strict()
type Settings = ReturnType<typeof settingsSchema.parse>

async function settings(name: string, context: ToolContext) {
  const config = settingsSchema.parse(JSON.parse(await readFile(new URL("../cairn.json", import.meta.url), "utf8")))
  await context.ask({ permission: "cairn_" + name, patterns: [config.repo], always: [config.repo], metadata: {} })
  return config
}

function call(config: Settings, context: ToolContext, args: string[], input?: unknown): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const child = execFile(config.executable, ["agent", "--socket", config.socket, "--token-file", config.token_file, ...args],
      { encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024, signal: context.abort }, (error, stdout) => {
        let response
        try { response = JSON.parse(stdout) } catch {
          reject(new Error("Cairn CLI failed without a valid response; check the local installation and connection"))
          return
        }
        if (error || response.schema !== "cairn.response/1" || response.ok !== true) {
          reject(new Error(response.message ?? "Cairn CLI request failed"))
          return
        }
        resolve(response.data)
      })
    child.stdin?.on("error", () => reject(new Error("Cairn CLI input pipe failed")))
    child.stdin?.end(input === undefined ? undefined : JSON.stringify(input))
  })
}

function render(value: unknown, config: Settings) {
  const text = JSON.stringify(value)
  if (Buffer.byteLength(text, "utf8") > config.tokens) {
    throw new Error("BUDGET_REFUSED: tool result exceeds configured memory room; retry the same request with sufficient room")
  }
  return text
}

const pullArgs = {
  request_id: z.string().uuid(),
  receipt_id: z.string().uuid(),
  handle: z.string().uuid(),
}
const searchView = z.object({
  schema: z.literal("cairn.agent-search/1"),
  index: z.array(z.object({ pull_arguments: z.object(pullArgs), pull_command: z.string() }).passthrough()),
}).passthrough()
const writeResult = z.object({ record_id: z.string().uuid(), version: z.number().int().positive() })

export const search = validatedTool({
  description: "Search repository memory in this OpenCode session with a query, or set browse=true without a query to inspect available topics. Browsing is bounded by the same budget, ordered by scope and recency, and is not a complete inventory or relevance ranking. Read mandatory selected context and pull relevant index entries using their complete pull_arguments. A notes are fallible; verify before applying them. Search records exposure, not proven use.",
  args: { query: z.string().optional(), browse: z.boolean().optional(), offset: z.number().int().min(0).max(10000).optional().describe("Continue browsing with browse.next_offset from the previous result; default 0. Pages read current state."), request_id: z.string().uuid().optional() },
  async execute(args, context) {
    const query = args.query ?? ""
    if ((args.browse && query !== "") || (!args.browse && query.trim() === "")) {
      throw new Error("INVALID_REQUEST: search requires a nonempty query or browse=true without a query")
    }
    if (!args.browse && (args.offset ?? 0) !== 0) throw new Error("INVALID_REQUEST: offset requires browse=true")
    const config = await settings("search", context)
    const session = context.sessionID
    if (!session || session === "*" || Buffer.byteLength(session) > 240 || /[\s\p{Cc}]/u.test(session)) {
      throw new Error("Cairn requires a valid native OpenCode session ID")
    }
    const command = ["search", "--repo", config.repo, "--task", "opencode/" + session, "--run", session, "--tokens", String(config.tokens)]
    if (args.request_id) command.push("--request-id", args.request_id)
    for (const [key, value] of Object.entries(config.context ?? {})) {
      if (value !== undefined) command.push("--" + key.replaceAll("_", "-"), value)
    }
    if (args.browse) command.push("--browse", "--offset", String(args.offset ?? 0))
    else command.push("--", query)
    const view = searchView.parse(await call(config, context, command))
    // Native callers need the structured arguments, not a shell invocation.
    const index = view.index.map(({ pull_command, ...entry }) => entry)
    return render({ ...view, schema: "cairn.opencode-search/1", index }, config)
  },
})

export const pull = validatedTool({
  description: "Pull the full memory body using an index entry's complete pull_arguments. Reuse them for retries. STALE_HANDLE requires a fresh search. Shares the original receipt's expansion budget.",
  args: pullArgs,
  async execute(args, context) {
    const config = await settings("pull", context)
    return render(await call(config, context, ["expand"], args), config)
  },
})

export const pull_evidence = validatedTool({
  description: "Pull evidence attached to an expanded memory. Use its evidence ID and expected SHA256 with the original receipt and handle and a stable request UUID. Shares the expansion budget.",
  args: { ...pullArgs, evidence_id: z.string().uuid(), expected_sha256: z.string().regex(/^[a-f0-9]{64}$/) },
  async execute(args, context) {
    const config = await settings("pull_evidence", context)
    return render(await call(config, context, ["expand-evidence"], args), config)
  },
})

export const remember = validatedTool({
  description: "Save explicitly selected reusable repository knowledge as ordinary A testimony across tasks and sessions. Include source and verification context; never raw sessions or secrets. Reuse the request UUID for retries. shareable permits hosted delivery; local is the default.",
  args: { request_id: z.string().uuid(), body: z.string().min(1), kind: z.string().optional().describe("Defaults to note"), shareable: z.boolean().optional() },
  async execute(args, context) {
    const config = await settings("remember", context)
    const result = await call(config, context, ["create"], { request_id: args.request_id, draft: {
      kind: args.kind ?? "note", body: args.body, scope: { repo: config.repo, task_id: "*", run_id: "*" },
      sensitivity: args.shareable ? "shareable" : "local", claim_type: "self",
    } })
    return render({ ...writeResult.parse(result), request_id: args.request_id }, config)
  },
})

export const edit = validatedTool({
  description: "Revise a pulled active A note. Supply body to change only text while preserving all stored metadata, or draft for a complete replacement, never both. Supply its ID, expected version, and a new request UUID. Preserve scope, sensitivity, pins, relations and attribution. Reuse exact arguments for retries; VERSION_CONFLICT needs fresh search/pull and reconciliation. Returns identifiers without echoing the body.",
  args: { request_id: z.string().uuid(), record_id: z.string().uuid(), expected_version: z.number().int().positive(), body: z.string().min(1).optional(), draft: z.record(z.string(), z.unknown()).optional() },
  async execute(args, context) {
    if ((args.body === undefined) === (args.draft === undefined)) throw new Error("INVALID_REQUEST: supply exactly one of body or draft")
    const config = await settings("edit", context)
    if (args.body !== undefined) {
      const result = await call(config, context, ["revise"], { request_id: args.request_id, record_id: args.record_id, expected_version: args.expected_version, repo: config.repo, body: args.body })
      return render({ ...writeResult.parse(result), request_id: args.request_id }, config)
    }
    if ((args.draft?.scope as { repo?: unknown } | undefined)?.repo !== config.repo) {
      throw new Error("AUTHORITY_DENIED: edit draft must use the configured repository")
    }
    const result = await call(config, context, ["edit"], args)
    return render({ ...writeResult.parse(result), request_id: args.request_id }, config)
  },
})

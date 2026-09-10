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

const contextFields = {
  revision: z.string().optional(), workspace_sha256: z.string().optional(),
  task_class: z.string().optional(), task_phase: z.string().optional(),
  binding_id: z.string().optional(), capability_id: z.string().optional(),
}

const entities = z.array(z.object({ kind: z.enum(["file", "symbol"]), name: z.string().min(1) }).strict()).max(16).optional().describe("Explicit file or symbol associations. File names are canonical repository-relative paths; symbols are qualified labels. Names are case-sensitive; no alias or rename resolution. Fallible relevance metadata, never authority or observed workspace state.")

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
    task_phase: z.string().optional(),
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
  const command = ["agent", "--socket", config.socket, "--token-file", config.token_file, ...args]
  // Process arguments replace lone UTF-16 surrogates before Cairn can inspect them.
  if ([config.executable, ...command].some(value => /[\uD800-\uDFFF]/u.test(value))) {
    return Promise.reject(new Error("INVALID_REQUEST: CLI arguments require well-formed Unicode"))
  }
  return new Promise((resolve, reject) => {
    const child = execFile(config.executable, command,
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
  args: { advisory_conflicts: z.boolean().optional().describe("Opt in to qualified competing advisory positions. All must be eligible together; otherwise the group is omitted. Pulling a marked position returns its complete competing positions under the shared budget. Repeat on retries and later pages. Does not resolve disagreement or change authority."), entities: entities.describe("Optional explicit file/symbol hints. With the recent-files plugin, omission on a fresh first search uses recent successful file reads; [] disables that behavior. Copy returned query_entities on retries and later pages. Names are fallible relevance metadata, not authority."), context: z.object(contextFields).strict().optional().describe("Context declared for this search only. May fill fields the host left unset; conflicting configured values are refused. Observe actual task state first. Does not certify execution or change repository/session scope. Repeat the same context on later pages."), error_signature_sha256: z.string().regex(/^[a-fA-F0-9]{64}$/).optional().describe("SHA-256 of a known failure signature. Prefers an eligible exact lesson version linked by a shareable operator review. May replace query text; cannot browse. Not proof of failure or correctness."), kinds: z.array(z.enum(["note", "observation", "claim", "lesson", "procedure", "decision", "preference", "instruction"])).max(8).optional().describe("Select any listed optional record label; empty means all. Required instructions always apply. Labels do not establish authority."), query: z.string().optional().describe("Words describing the memory needed. ASCII double quotes prefer exact case-sensitive text in a note; other lexical matches remain available."), semantic: z.boolean().optional().describe("Optional semantic discovery for vocabulary mismatch; no browsing. Similarity is not confidence. Unavailable backends return labelled lexical fallback."), browse: z.boolean().optional(), offset: z.number().int().min(0).max(10000).optional().describe("Set 0 to start ranked pagination, then pass page.next_offset with the same query, semantic mode, kinds and scope. Browsing uses browse.next_offset. Pages read current state and each has its own budget."), request_id: z.string().uuid().optional() },
  async execute(args, context) {
    const query = args.query ?? ""
    if (args.semantic && args.browse) throw new Error("INVALID_REQUEST: semantic discovery cannot be combined with browsing")
    if ((args.browse && (query !== "" || args.error_signature_sha256 || args.entities?.length)) || (!args.browse && query.trim() === "" && !args.error_signature_sha256 && !args.entities?.length)) {
      throw new Error("INVALID_REQUEST: search requires a query, entities, error_signature_sha256, or browse=true without search hints")
    }
    const config = await settings("search", context)
    const session = context.sessionID
    if (!session || session === "*" || Buffer.byteLength(session) > 240 || /[\s\p{Cc}]/u.test(session)) {
      throw new Error("Cairn requires a valid native OpenCode session ID")
    }
    const command = ["search", "--repo", config.repo, "--task", "opencode/" + session, "--run", session, "--tokens", String(config.tokens)]
    if (args.request_id) command.push("--request-id", args.request_id)
    const declared: Record<string, string | undefined> = { ...args.context }
    for (const [key, value] of Object.entries(config.context ?? {})) {
      if (!value) continue
      const field = key === "binding" ? "binding_id" : key === "capability" ? "capability_id" : key
      if (declared[field] && declared[field] !== value) {
        throw new Error("INVALID_REQUEST: context." + field + " conflicts with configured context")
      }
      declared[field] = value
    }
    for (const [key, value] of Object.entries(declared)) {
      const flag = key === "binding_id" ? "binding" : key === "capability_id" ? "capability" : key.replaceAll("_", "-")
      if (value !== undefined) command.push("--" + flag, value)
    }
    for (const entity of args.entities ?? []) command.push("--entity-" + entity.kind, entity.name)
    for (const kind of args.kinds ?? []) command.push("--kind", kind)
    if (args.error_signature_sha256) command.push("--error-signature-sha256", args.error_signature_sha256)
    if (args.advisory_conflicts) command.push("--advisory-conflicts")
    if (args.semantic) command.push("--semantic")
    if (args.browse) command.push("--browse", "--offset", String(args.offset ?? 0))
    else {
      if (args.offset !== undefined) command.push("--offset", String(args.offset))
      command.push("--", query)
    }
    const view = searchView.parse(await call(config, context, command))
    // Native callers need the structured arguments, not a shell invocation.
    const index = view.index.map(({ pull_command, ...entry }) => entry)
    // Presentation metadata for repeatable retries/pages, not part of the seal.
    return render({ ...view, schema: "cairn.opencode-search/1", query_entities: args.entities ?? [], index }, config)
  },
})

export const history = validatedTool({
  description: "Inspect retained versions of a known record for comparison. Omit version for newest-first metadata; follow next_before_version as before_version. Supply a positive version for one exact body, without nonzero paging fields. Historical text and class do not establish current eligibility or authority; pull the current note before editing. The authenticated profile controls repository and destination; forgotten or excluded payloads refuse. No request UUID or expansion handle is needed. This read has its own output budget and does not spend index expansion credits; budget combined context across calls.",
  args: { record_id: z.string().uuid(), version: z.number().int().min(0).max(2147483647).optional(),
    before_version: z.number().int().min(0).max(2147483647).optional(),
    limit: z.number().int().min(0).max(100).optional().describe("Metadata page size; omitted or zero means 20") },
  async execute(args, context) {
    const config = await settings("history", context)
    return render(await call(config, context, ["history"], { ...args, repo: config.repo }), config)
  },
})

export const pull = validatedTool({
  description: "Pull a memory body using its complete pull_arguments. Optional span selects byte offset and maximum length for a partial A/B source; selected bytes and hashes appear in span, with record.body empty. Copy an index entry's summary_span into span to read its exact preview source bytes without omission markers. Instructions and marked competing positions require a whole pull. A marked pull returns the requested selection plus competing positions; read all of them. Use a new request UUID for a different range. STALE_HANDLE requires a fresh search. Shares the original receipt's expansion budget. Read the complete note before replacing its body.",
  args: { ...pullArgs, span: z.object({ offset: z.number().int().min(0).max(65535), length: z.number().int().min(1).max(65536) }).strict().optional() },
  async execute(args, context) {
    const config = await settings("pull", context)
    return render(await call(config, context, ["expand"], args), config)
  },
})

export const pull_evidence = validatedTool({
  description: "Pull evidence attached to an expanded memory. Use its evidence ID and full-object expected SHA256 with the original receipt and handle. Optional span selects byte offset and maximum length, clipped at EOF; selected bytes and their checksum appear in span. Reuse the request UUID only for identical retries. Shares the expansion budget.",
  args: { ...pullArgs, evidence_id: z.string().uuid(), expected_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    span: z.object({ offset: z.number().int().min(0).max(1048575), length: z.number().int().min(1).max(1048576) }).strict().optional() },
  async execute(args, context) {
    const config = await settings("pull_evidence", context)
    return render(await call(config, context, ["expand-evidence"], args), config)
  },
})

export const remember = validatedTool({
  description: "Save explicitly selected reusable repository knowledge as ordinary A testimony across tasks and sessions. Include source and verification context; never raw sessions or secrets. Reuse the request UUID for retries. shareable permits hosted delivery; local is the default.",
  args: { entities, request_id: z.string().uuid(), body: z.string().min(1), kind: z.string().optional().describe("Defaults to note"), shareable: z.boolean().optional(),
    pins: z.object({
      ...contextFields,
      valid_from: z.string().optional(), valid_until: z.string().optional(),
    }).strict().optional().describe("Explicit applicability restrictions; all must match. Omit for unpinned guidance. Never inherited from search context. Edits cannot change pins."),
  },
  async execute(args, context) {
    const config = await settings("remember", context)
    const result = await call(config, context, ["create"], { request_id: args.request_id, draft: {
      kind: args.kind ?? "note", body: args.body, scope: { repo: config.repo, task_id: "*", run_id: "*" },
      sensitivity: args.shareable ? "shareable" : "local", claim_type: "self", pins: args.pins, entities: args.entities,
    } })
    return render({ ...writeResult.parse(result), request_id: args.request_id }, config)
  },
})

export const edit = validatedTool({
  description: "Revise a pulled active A note. Supply exactly one of body (text only), draft (complete replacement), or evidence_citations (replace source references; [] clears them). Text edits preserve citations; earlier versions retain their sources. Citations require captured source IDs and full-source digests and remain testimony, not qualification. Supply its ID, expected version, and a new request UUID. Preserve scope, sensitivity, pins, entities, relations and attribution except deliberately changed associations. Reuse exact arguments for retries; VERSION_CONFLICT needs fresh search/pull and reconciliation. Returns identifiers without echoing the body.",
  args: { request_id: z.string().uuid(), record_id: z.string().uuid(), expected_version: z.number().int().positive(), body: z.string().min(1).optional(), draft: z.record(z.string(), z.unknown()).optional(),
    evidence_citations: z.array(z.object({ evidence_id: z.string().uuid(), expected_sha256: z.string().regex(/^[a-f0-9]{64}$/),
      spans: z.array(z.object({ offset: z.number().int().min(0), length: z.number().int().positive() }).strict()).max(32).optional(),
    }).strict()).max(32).optional(),
  },
  async execute(args, context) {
    if ([args.body, args.draft, args.evidence_citations].filter(value => value !== undefined).length !== 1) throw new Error("INVALID_REQUEST: supply exactly one of body, draft or evidence_citations")
    const config = await settings("edit", context)
    if (args.evidence_citations !== undefined) {
      const result = await call(config, context, ["cite"], { request_id: args.request_id, record_id: args.record_id, expected_version: args.expected_version, repo: config.repo, evidence_citations: args.evidence_citations })
      return render({ ...writeResult.parse(result), request_id: args.request_id }, config)
    }
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

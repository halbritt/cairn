// Bridge fixture that runs the actual OpenCode coordination plugin with mock SDK responses.
import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

// Configure fake coordination JSON for the plugin
const configDir = path.join("/tmp", `cairn-test-opencode-${process.pid}`);
fs.mkdirSync(configDir, { recursive: true });
const configPath = path.join(configDir, "cairn-coordination.json");
const hookPath = path.join(configDir, "hook.mjs");
if (process.env.OPENCODE_FIXTURE_HOOK_CAPTURE) {
  fs.writeFileSync(hookPath, `
import fs from "node:fs";
let input = "";
for await (const chunk of process.stdin) input += chunk;
fs.appendFileSync(process.env.OPENCODE_FIXTURE_HOOK_CAPTURE, input + "\\n");
const event = JSON.parse(input).hook_event_name;
const capture = process.env.OPENCODE_FIXTURE_TOOL_CAPTURE;
if (capture === "fail" && event === "ToolStart") process.exit(1);
process.stdout.write(JSON.stringify({ hookSpecificOutput: { additionalContext: "" },
  ...(capture && event === "TurnStart" ? { cairn: { tool_capture: true } } : {}) }));
`);
}
fs.writeFileSync(
  configPath,
  JSON.stringify({ python: process.execPath,
    script: process.env.OPENCODE_FIXTURE_HOOK_CAPTURE ? hookPath : "/bin/true", config: "/dev/null" })
);
process.env.CAIRN_COORDINATION_CONFIG = configPath;

const mode = process.env.OPENCODE_FIXTURE_MODE || "normal";
// Modes:
// 'normal': atomic promptIdle accepts
// 'busy': atomic promptIdle refuses with busy
// 'conflict': requestID is already bound to different native work
// 'completed'/'cancelled'/'failed': idempotent request already ran
// 'sdk_error': promptIdle returns an SDK error
// 'missing_api': client lacks promptIdle
// OPENCODE_FIXTURE_CANCEL selects cancelRequest behaviour (absent: no route):
// 'cancelled', 'not_active', 'route_missing' (404), 'bad_request' (400),
// 'server_error' (500), 'invalid', 'slow' (settles after 5s), 'throw'.
// OPENCODE_FIXTURE_TURN records that owner turn for ses_test at startup.
// OPENCODE_FIXTURE_TOOL_RESULT: after an admitted turn, run the shell.env and
// tool hooks, then an owner turn, and write what they observed to this file.
// OPENCODE_FIXTURE_TOOL_CAPTURE: '1' makes TurnStart advertise tool capture;
// 'fail' also advertises it but fails every ToolStart hook call.
const cancelMode = process.env.OPENCODE_FIXTURE_CANCEL

let client = null;
if (mode !== "missing_api") {
  client = {
    session: {
      get: async ({ path: { id } }) => {
        if (mode === "not_found") return { error: { message: "session not found" } };
        return { data: { id, title: "Test Session" } };
      },
      promptIdle: async ({ path: { id }, body }) => {
        if (process.env.OPENCODE_FIXTURE_CAPTURE) {
          fs.appendFileSync(process.env.OPENCODE_FIXTURE_CAPTURE, JSON.stringify({ session_id: id, body }) + "\n");
        }
        if (mode === "native_schema") {
          // The isolated native server has no sessions. A valid payload reaches
          // its session lookup (404); a bad native message ID fails schema (400).
          const response = await fetch(`${process.env.OPENCODE_FIXTURE_URL}/session/${id}/prompt_idle`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          return { error: { message: `HTTP ${response.status}: ${await response.text()}` }, response: { status: response.status } };
        }
        if (mode === "sdk_error") return { error: { message: "HTTP 500 Internal Server Error" } };
        if (mode === "busy") return { data: { status: "busy" } };
        if (mode === "conflict") return { data: { status: "conflict" } };
        if (["completed", "cancelled", "failed"].includes(mode)) return { data: { status: mode } };
        if (process.env.OPENCODE_FIXTURE_HOOK_CAPTURE) {
          // Run the hook before the bridge has consumed the HTTP result. It
          // must wait for atomic admission before claiming the request.
          queueMicrotask(async () => {
            const info = { role: "user", sessionID: id, id: "msg_native_one" };
            const parts = body.parts.map(part => ({ ...part, synthetic: false, ignored: false }));
            await hooks["experimental.chat.messages.transform"]({}, { messages: [{ info, parts }] });
            const toolResult = process.env.OPENCODE_FIXTURE_TOOL_RESULT;
            const shellEnv = async () => {
              const output = { env: {} };
              await hooks["shell.env"]({ cwd: "/tmp", sessionID: id, callID: "call_one" }, output);
              return output.env;
            };
            const run = async (name, extra) => {
              try {
                await hooks[name]({ tool: "bash", sessionID: id, callID: "call_one", ...extra }, extra.output);
                return "ok";
              } catch (err) { return String(err?.message || err); }
            };
            const observed = {};
            if (toolResult) {
              observed.exclusive_env = await shellEnv();
              observed.before = await run("tool.execute.before", { output: { args: {} } });
              observed.after = observed.before === "ok"
                ? await run("tool.execute.after", { args: {}, output: { title: "", output: "", metadata: {} } })
                : "skipped";
            }
            await hooks.event({ event: { type: "session.idle", properties: { sessionID: id } } });
            if (toolResult) {
              observed.after_idle_env = await shellEnv();
              const owner = { role: "user", sessionID: id, id: "msg_owner_two" };
              await hooks["experimental.chat.messages.transform"]({}, { messages: [{ info: owner,
                parts: [{ type: "text", text: "owner prompt", synthetic: false, ignored: false }] }] });
              observed.owner_env = await shellEnv();
              observed.owner_before = await run("tool.execute.before", { output: { args: {} } });
              fs.writeFileSync(toolResult, JSON.stringify(observed));
            }
          });
        }
        return { data: { status: "accepted" } };
      },
    },
  };
  if (cancelMode) {
    client.session.cancelRequest = async ({ path: { id }, body }) => {
      if (process.env.OPENCODE_FIXTURE_CAPTURE) {
        fs.appendFileSync(process.env.OPENCODE_FIXTURE_CAPTURE, JSON.stringify({ cancel: { session_id: id, body } }) + "\n");
      }
      if (cancelMode === "throw") throw new Error("socket hang up");
      if (cancelMode === "slow") await new Promise(done => setTimeout(done, 5000));
      if (cancelMode === "not_active") return { data: { cancelled: false } };
      if (cancelMode === "invalid") return { data: {} };
      const status = { route_missing: 404, bad_request: 400, server_error: 500 }[cancelMode];
      if (status) return { error: { message: `HTTP ${status}` }, response: { status } };
      return { data: { cancelled: true } };
    };
  }
}

// Dynamically import coordination plugin
const pluginModule = await import(path.join(root, "integrations/opencode/coordination.ts"));
const plugin = pluginModule.default;

let hooks = await plugin({
  directory: "/tmp",
  client,
  project: { id: "proj_test" },
  worktree: "/tmp",
  serverUrl: new URL("http://localhost"),
  $: null,
});

if (process.env.OPENCODE_FIXTURE_TURN) {
  const info = { role: "user", sessionID: "ses_test", id: process.env.OPENCODE_FIXTURE_TURN };
  await hooks["experimental.chat.messages.transform"]({}, { messages: [{ info,
    parts: [{ type: "text", text: "owner turn", synthetic: false, ignored: false }] }] });
}

// Signal ready
console.log(`READY:${process.pid}`);

// Keep process alive until terminated
process.on("SIGTERM", async () => {
  if (hooks.dispose) await hooks.dispose();
  process.exit(0);
});

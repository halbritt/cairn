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
fs.writeFileSync(
  configPath,
  JSON.stringify({ python: process.execPath, script: "/bin/true", config: "/dev/null" })
);
process.env.CAIRN_COORDINATION_CONFIG = configPath;

const mode = process.env.OPENCODE_FIXTURE_MODE || "normal";
// Modes:
// 'normal': get succeeds, status idle, promptAsync succeeds
// 'busy': get succeeds, status busy
// 'sdk_error': get fails or promptAsync returns { error: { message: "HTTP 500" } }
// 'missing_api': client lacks promptAsync

let client = null;
if (mode !== "missing_api") {
  client = {
    session: {
      get: async ({ path: { id } }) => {
        if (mode === "not_found") return { error: { message: "session not found" } };
        return { data: { id, title: "Test Session" } };
      },
      status: async () => {
        if (mode === "busy") return { data: { "ses_test": { type: "busy" } } };
        return { data: { "ses_test": { type: "idle" } } };
      },
      cancelRequest: async ({ body }) => ({ data: { cancelled: body.requestID === "req_owned" } }),
      promptIdle: async ({ path: { id }, body }) => {
        if (mode === "sdk_error") return { error: { message: "HTTP 500 Internal Server Error" } };
        if (mode === "not_found") return { response: { status: 404 }, error: { message: "session not found" } };
        return { data: { status: mode === "busy" ? "busy" : "accepted" } };
      },
    },
  };
}

// Dynamically import coordination plugin
const pluginModule = await import(path.join(root, "integrations/opencode/coordination.ts"));
const plugin = pluginModule.default;

const hooks = await plugin({
  directory: "/tmp",
  client,
  project: { id: "proj_test" },
  worktree: "/tmp",
  serverUrl: new URL("http://localhost"),
  $: null,
});

// Signal ready
console.log(`READY:${process.pid}`);

// Keep process alive until terminated
process.on("SIGTERM", async () => {
  if (hooks.dispose) await hooks.dispose();
  process.exit(0);
});

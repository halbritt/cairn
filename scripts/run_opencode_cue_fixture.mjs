// Real plugin and Python cached-state hook; no SDK or API service is available.
import plugin from "../integrations/opencode/coordination.ts";

let sdkCalls = 0;
const client = new Proxy({}, { get() { sdkCalls++; throw new Error("Unexpected SDK call"); } });
const hooks = await plugin({ directory: process.env.CUE_WORKSPACE, client });
console.log(`READY:${process.pid}`);
try {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const calls = JSON.parse(input);
  await hooks["experimental.chat.messages.transform"]({}, { messages: [{
    info: { role: "user", sessionID: "ses_fixture", id: "msg_owner" },
    parts: [{ type: "text", text: "fixture owner task", synthetic: false, ignored: false }],
  }] });
  const complete = async ({ session, output }, index) => {
    await hooks["tool.execute.after"]({
      sessionID: session, tool: "fixture", callID: `call_${index}`, args: {},
    }, output);
  };
  if (process.env.CUE_DISPOSE) {
    const pending = complete(calls[0], 0);
    await hooks.dispose();
    await pending;
  } else if (process.env.CUE_CONCURRENT) await Promise.all(calls.map(complete));
  else for (const [index, call] of calls.entries()) await complete(call, index);
  console.log(JSON.stringify({ outputs: calls.map(call => call.output), sdkCalls }));
} finally {
  await hooks.dispose();
}

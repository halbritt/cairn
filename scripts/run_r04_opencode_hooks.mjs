// Actual staged plugins and receipt-checked Go/Source; host dialogue is the fixture.
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'

const [home, directory] = process.argv.slice(2)
const owner = { info: { role: 'user', id: 'msg_wiring', sessionID: 'ses_wiring' },
  parts: [{ type: 'text', text: 'Repair lifecycle recall implementation.' }] }
const client = { session: {
  get: async () => ({ data: {} }),
  // Courtesy capture reaches real Source without invoking a model provider.
  messages: async () => ({ data: [{ ...owner, parts: [{ type: 'text', text: 'Thanks!' }] },
    { info: { role: 'assistant' }, parts: [{ type: 'text', text: "You're welcome!" }] }] }),
} }
const memory = (await import(pathToFileURL(join(home, 'plugins/cairn-lifecycle.ts')))).default
const memoryHooks = await memory({ client, directory, worktree: directory })
const output = { messages: structuredClone([owner]) }
await memoryHooks['experimental.chat.messages.transform']({}, output)
assert.deepEqual(owner.parts, [{ type: 'text', text: 'Repair lifecycle recall implementation.' }])
await memoryHooks['tool.execute.after']({ tool: 'read', sessionID: 'ses_wiring' },
  { metadata: { display: { type: 'file', path: join(directory, 'store.go') } } })
await memoryHooks['experimental.session.compacting']({ sessionID: 'ses_wiring' }, { context: [] })
await memoryHooks.event({ event: { type: 'session.idle', properties: { sessionID: 'ses_wiring' } } })
await memoryHooks.dispose()
const coordination = (await import(pathToFileURL(join(home, 'plugins/cairn-coordination.ts')))).default
const coordHooks = await coordination({ client, directory })
const coordinated = { messages: structuredClone([owner]) }
await coordHooks['experimental.chat.messages.transform']({}, coordinated)
assert(coordinated.messages[0].parts.some(p => p.synthetic && p.text.includes('Cairn session agent-')))
await coordHooks.event({ event: { type: 'session.idle', properties: { sessionID: 'ses_wiring' } } })
await coordHooks.dispose()
console.log(JSON.stringify({ memory: true, coordination: true }))

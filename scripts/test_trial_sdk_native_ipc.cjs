'use strict';
// Ordinary owned-process tests only. No SDK, provider, native CLI, or actor.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const [addon, mode, socketText, pidfdText, pidText, uidText, gidText] = process.argv.slice(2);
const native = require(addon);
const socket = Number(socketText), pidfd = Number(pidfdText);
const pid = Number(pidText), uid = Number(uidText), gid = Number(gidText);
const scratch = new Uint8Array(512), reply = new Uint8Array(544);
const request = new Uint8Array(32).fill(0x51);
const gateByte = new Uint8Array(1);
let peer, closed = false, primary = null, cleanup = null;
let detail = {};
function emit(value) { fs.writeSync(1, JSON.stringify(value) + '\n'); }
function gate() { assert.equal(fs.readSync(0, gateByte, 0, 1, null), 1); }
function fds() {
  return fs.readdirSync('/proc/self/fd').map(Number).filter(fd => {
    try { fs.fstatSync(fd); return true; }
    catch (e) { if (e.code === 'EBADF') return false; throw e; }
  }).sort((a, b) => a - b);
}
function flags(fd) {
  const b = Buffer.alloc(512);
  const file = fs.openSync('/proc/self/fdinfo/' + fd, 'r');
  let n;
  try { n = fs.readSync(file, b, 0, b.length, 0); }
  finally { fs.closeSync(file); }
  assert(n < b.length);
  const line = b.subarray(0, n).toString('ascii').split('\n').find(x => x.startsWith('flags:\t'));
  assert(line);
  return parseInt(line.slice(7), 8);
}
function refuses(fn, code) {
  assert.throws(fn, e => e && e.code === code && e.message === code);
}
function terminal(code) {
  refuses(() => native.receive(peer, reply), code);
  assert(reply.every(b => b === 0));
  refuses(() => native.sendRecord(peer, request), 'ipc_peer_unusable');
}
try {
  assert.equal(typeof native.now(), 'bigint');
  refuses(() => native.now(1), 'ipc_argument');
  if (mode !== 'unprotected') assert.equal(native.protect(), process.pid);
  const before = fds();
  const deadline = native.now() + 2000000000n;
  if (['wrong-pid', 'stream', 'unprotected'].includes(mode)) {
    const wanted = mode === 'wrong-pid' ? process.pid : pid;
    const code = mode === 'wrong-pid' ? 'ipc_pidfd_identity'
      : mode === 'stream' ? 'ipc_setup_descriptor' : 'ipc_setup_boundary';
    refuses(() => native.openPeer(socket, pidfd, wanted, uid, gid, deadline, scratch), code);
    assert.deepEqual(fds(), before);
    assert(scratch.every(b => b === 0));
  } else {
    scratch.fill(0x7f);
    peer = native.openPeer(socket, pidfd, pid, uid, gid, deadline, scratch);
    assert(scratch.every(b => b === 0));
    const added = fds().filter(fd => !before.includes(fd));
    assert.equal(added.length, 2);
    for (const fd of added) assert(flags(fd) & 0o2000000);
    detail.owned_duplicates = 2;
    detail.duplicates_cloexec = true;
  }
  emit({phase: 'ready', mode, pid: process.pid});
  gate();
  if (mode === 'api') {
    refuses(() => native.protect(1), 'ipc_argument');
    refuses(() => native.closePeer({}), 'ipc_peer_argument');
    refuses(() => native.receive(peer, new Uint8Array(31)), 'ipc_argument');
    refuses(() => native.sendRecord(peer, new Uint16Array(32)), 'ipc_argument');
    refuses(() => native.sendRecord(peer, new DataView(new ArrayBuffer(32))), 'ipc_argument');
    refuses(() => native.sendRecord(peer, new Uint8Array(new SharedArrayBuffer(32))), 'ipc_argument');
    const detached = new Uint8Array(32);
    structuredClone(detached.buffer, {transfer: [detached.buffer]});
    refuses(() => native.sendRecord(peer, detached), 'ipc_argument');
    const backing = new ArrayBuffer(1088);
    refuses(() => native.exchange(peer, new Uint8Array(backing, 0, 32),
      new Uint8Array(backing, 544, 544), native.now() + 1000000n), 'ipc_argument');
    for (const invalid of [-1, 1.5, NaN, Infinity, 2 ** 40, '1']) {
      refuses(() => native.openPeer(invalid, pidfd, pid, uid, gid, deadline, scratch), 'ipc_argument');
    }
    refuses(() => native.openPeer(socket, pidfd, pid, uid, gid, 1, scratch), 'ipc_argument');
    assert.equal(native.receive(peer, reply), -1);
  } else if (mode === 'roundtrip') {
    assert.equal(native.receive(peer, reply), -1);
    const n = native.exchange(peer, request, reply, native.now() + 500000000n);
    assert.equal(n, 544);
    assert(reply.every(b => b === 0x52));
    const last = new Uint8Array(33).fill(0x53);
    assert.equal(native.sendRecord(peer, last), 33);
  } else if (mode === 'valid32' || mode === 'valid544') {
    const n = Number(mode.slice(5));
    reply.fill(0);
    assert.equal(native.receive(peer, reply), n);
    assert(reply.subarray(0, n).every(b => b === 0x62));
    assert(reply.subarray(n).every(b => b === 0));
  } else if (['rights1', 'rights32', 'oversize', 'empty', 'wrong-sender'].includes(mode)) {
    const held = fds();
    reply.fill(0x7f);
    terminal('ipc_record_refused');
    assert.deepEqual(fds(), held);
    detail.received_fds_retained = 0;
  } else if (mode === 'peer-exit') {
    refuses(() => native.receive(peer, reply), 'ipc_receive_boundary');
    refuses(() => native.sendRecord(peer, request), 'ipc_peer_unusable');
  } else if (mode === 'timeout-reply' || mode === 'timeout-send') {
    if (mode === 'timeout-send') {
      let count = 0;
      const fill = new Uint8Array(544).fill(0x44);
      while (count < 1024) {
        const n = native.sendRecord(peer, fill);
        if (n === -1) break;
        assert.equal(n, 544); ++count;
      }
      assert(count > 0 && count < 1024);
      detail.fill_records = count;
      emit({phase: 'saturated', count});
    }
    const limit = native.now() + 40000000n;
    refuses(() => native.exchange(peer, request, reply, limit), 'ipc_exchange_unknown');
    assert(native.now() >= limit);
    refuses(() => native.sendRecord(peer, request), 'ipc_peer_unusable');
    detail.deadline_observed = true;
  } else if (mode === 'originals-closed') {
    fs.closeSync(socket); fs.closeSync(pidfd);
    assert.equal(native.receive(peer, reply), 32);
    assert(reply.subarray(0, 32).every(b => b === 0x62));
  }
  if (peer) {
    native.closePeer(peer); closed = true;
    refuses(() => native.closePeer(peer), 'ipc_peer_closed');
    refuses(() => native.sendRecord(peer, request), 'ipc_peer_unusable');
    const expected = before.filter(fd => mode !== 'originals-closed' || (fd !== socket && fd !== pidfd));
    assert.deepEqual(fds(), expected);
  }
} catch (e) {
  primary = /^[A-Za-z0-9_]{1,80}$/.test(e.code || '') ? e.code : 'test_failure';
} finally {
  if (peer && !closed) {
    try { native.closePeer(peer); closed = true; }
    catch (e) { cleanup = /^[A-Za-z0-9_]{1,80}$/.test(e.code || '') ? e.code : 'cleanup_unknown'; }
  }
}
emit({phase: 'result', mode, ok: !primary && !cleanup, primary, cleanup, closed, detail});
process.exitCode = primary || cleanup ? 1 : 0;

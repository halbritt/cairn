'use strict';
// Fixed ordinary-host cases; no SDK, actor, provider, native CLI or arbitrary command.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const [binary, mode, fdText, pidfdText, pidText, uidText, gidText, extraText] = process.argv.slice(2);
const native = require(binary);
const fd = Number(fdText), pidfd = Number(pidfdText), pid = Number(pidText);
const uid = Number(uidText), gid = Number(gidText);
const out = value => fs.writeSync(1, JSON.stringify(value) + '\n');
const fds = () => fs.readdirSync('/proc/self/fd').map(Number).filter(fd => {
  try { fs.fstatSync(fd); return true; } catch (e) { if (e.code === 'EBADF') return false; throw e; }
}).sort((a, b) => a-b);
const refuses = (fn, code) => assert.throws(fn, e => e.code === code && e.message === code);
const rejected = (promise, code) => assert.rejects(promise, e => e.code === code && e.message === code);
const turn = () => new Promise(resolve => setImmediate(resolve));
const tick = () => new Promise(resolve => setTimeout(() => { out({phase:'tick'}); resolve(); }, 20));
let peer, other, closed = false;
let before;
async function run() {
  native.protect();
  before = fds();
  const deadline = native.now() + 1500000000n;
  const make = (socket=fd) => native.openPeer(socket, pidfd, pid, uid, gid, deadline, new Uint8Array(512));
  peer = make();
  const buffer = new Uint8Array(544), record = new Uint8Array(32).fill(0x51);
  const wait = (mask=1, end=native.now()+600000000n) => native.waitPeer(peer, mask, end);
  if (mode === 'readable') {
    assert.equal(native.receive(peer, buffer), -1);
    const pending = wait();
    out({phase:'armed'});
    await tick();
    assert.equal(await pending, 1);
    assert.equal(native.receive(peer, buffer), 32);
    assert(buffer.subarray(0,32).every(x=>x===0x62));
    assert.equal(native.receive(peer, buffer), -1);
    assert.equal(native.cancelWait(peer), false);
  } else if (mode === 'writable') {
    let count = 0;
    const full = new Uint8Array(544).fill(0x44);
    while (count < 1024 && native.sendRecord(peer, full) !== -1) ++count;
    assert(count > 0 && count < 1024);
    const pending = wait(2);
    out({phase:'armed', count});
    await tick();
    assert.equal(await pending, 2);
    assert.equal(native.sendRecord(peer, record), 32);
    out({phase:'written'});
  } else if (mode === 'cancel-rearm') {
    const first = rejected(wait(), 'ipc_wait_cancelled');
    refuses(()=>wait(), 'ipc_wait_armed');
    refuses(()=>native.receive(peer,buffer), 'ipc_wait_armed');
    refuses(()=>native.sendRecord(peer,record), 'ipc_wait_armed');
    refuses(()=>native.exchange(peer,record,buffer,native.now()+1000000n), 'ipc_wait_armed');
    assert.equal(native.cancelWait(peer), true);
    const second = wait();
    out({phase:'armed'});
    await tick();
    await first;
    assert.equal(await second, 1);
    assert.equal(native.receive(peer,buffer),32);
    assert.equal(native.cancelWait(peer),false);
  } else if (mode === 'close-armed') {
    const pending = rejected(wait(), 'ipc_wait_closed');
    native.closePeer(peer); closed = true;
    // Reuse freed numeric FD slots while old uv_close callbacks are outstanding.
    const a = fs.openSync('/dev/null','r'), b = fs.openSync('/dev/null','r');
    await pending;
    await turn(); await turn();
    assert.equal(native.cancelWait(peer),false);
    refuses(()=>wait(), 'ipc_peer_unusable');
    fs.closeSync(a); fs.closeSync(b);
  } else if (mode === 'timeout') {
    const end = native.now()+40000000n;
    await rejected(wait(1,end),'ipc_wait_timeout');
    assert(native.now()>=end);
    refuses(()=>native.sendRecord(peer,record),'ipc_peer_unusable');
  } else if (mode === 'deadline') {
    for (const invalid of [0,4,-1,1.5,'1',null]) refuses(()=>wait(invalid),'ipc_argument');
    refuses(()=>native.waitPeer(peer,1,1),'ipc_argument');
    refuses(()=>wait(1,deadline+1n),'ipc_wait_deadline');
    refuses(()=>native.receive(peer,buffer),'ipc_receive_boundary');
  } else if (mode === 'capacity') {
    const pending=[];
    for(let i=0;i<16;i++) {
      pending.push(rejected(wait(),'ipc_wait_cancelled'));
      assert.equal(native.cancelWait(peer),true);
    }
    refuses(()=>wait(),'ipc_wait_capacity');
    await Promise.all(pending); await turn(); await turn();
    const last = rejected(wait(),'ipc_wait_cancelled'); native.cancelWait(peer); await last;
    const many=[peer];
    for(let i=1;i<8;i++) many.push(make());
    refuses(()=>make(),'ipc_peer_capacity');
    for(const p of many.slice(1)) native.closePeer(p);
  } else if (mode === 'dual') {
    other=make(Number(extraText));
    const pending=wait();
    assert.equal(native.sendRecord(other,record),32);
    out({phase:'armed'});
    await tick();
    assert.equal(await pending,1);
    assert.equal(native.receive(peer,buffer),32);
    native.closePeer(other); other=null;
  } else if (mode === 'peer-exit') {
    const pending=rejected(wait(),'ipc_peer_lifetime_unknown');
    out({phase:'armed'});
    await tick();
    await pending;
    refuses(()=>native.sendRecord(peer,record),'ipc_peer_unusable');
  } else if (mode === 'gc-retention') {
    const pending=wait(); peer=null;
    global.gc();
    out({phase:'armed'});
    await tick();
    assert.equal(await pending,1);
    await turn(); await turn(); global.gc(); await turn(); await turn();
    closed=true;
  } else if (mode === 'teardown') {
    // A Worker termination interrupts this live wait; only env cleanup may retire it.
    native.waitPeer(peer,1,deadline).catch(()=>{});
    require('node:worker_threads').parentPort.postMessage({phase:'armed'});
    return;
  } else throw new Error('fixed_case');
  if (!closed) { native.closePeer(peer); closed=true; }
  await turn(); await turn();
  assert.deepEqual(fds(),before);
  out({phase:'result', mode, ok:true, fd_before:before.length, fd_after:fds().length});
}
run().catch(e=>{
  try { if(other)native.closePeer(other); if(peer&&!closed)native.closePeer(peer); }catch(_){}
  out({phase:'failure', mode, code: /^[a-zA-Z0-9_]{1,80}$/.test(e.code||'')?e.code:'test_failure'});
  process.exitCode=1;
});

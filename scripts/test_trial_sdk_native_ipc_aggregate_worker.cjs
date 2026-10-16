'use strict';
const {parentPort,workerData}=require('node:worker_threads');
const assert=require('node:assert/strict');
const [binary,fd,pidfd,pid,uid,gid,mode]=workerData;
const native=require(binary);
native.protect();
const open=()=>native.openPeer(fd,pidfd,pid,uid,gid,native.now()+800000000n,new Uint8Array(512));
if(mode==='capacity') {
  assert.throws(open,e=>e.code==='ipc_peer_capacity');
  parentPort.postMessage('capacity_refused');
} else if(mode==='armed') {
  const peer=open();native.waitPeer(peer,1,native.now()+600000000n).catch(()=>{});
  parentPort.postMessage('armed');
} else if(mode==='openclose') {
  const peer=open();native.closePeer(peer);
  parentPort.postMessage('opened_closed');
} else throw new Error('fixed_case');

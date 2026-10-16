'use strict';
// Fixed public-marker ordinary socket cases; no SDK/provider/native CLI.
const fs=require('node:fs'),assert=require('node:assert/strict');
const [binary,mode,fdText,pidfdText,pidText,uidText,gidText]=process.argv.slice(2);
const native=require(binary);
const fd=Number(fdText),pidfd=Number(pidfdText),pid=Number(pidText),uid=Number(uidText),gid=Number(gidText);
const out=x=>fs.writeSync(1,JSON.stringify(x)+'\n');
const refuses=(fn,code)=>assert.throws(fn,e=>e.code===code && e.message===code);
const fds=()=>fs.readdirSync('/proc/self/fd').map(Number).filter(fd=>{
 try{fs.fstatSync(fd);return true;}catch(e){if(e.code==='EBADF')return false;throw e;}
}).sort((a,b)=>a-b);
let peer,closed=false;
try {
 native.protect();const before=fds();
 peer=native.openPeer(fd,pidfd,pid,uid,gid,native.now()+1000000000n,new Uint8Array(512));
 const held=fds();
 const size=mode==='exchange32'?32:mode==='exchange544'?544:33;
 const storage=new Uint8Array(size+16).fill(0xa5),reply=storage.subarray(8,8+size);
 reply.fill(0x7f);
 const request=new Uint8Array(32).fill(0x51);
 out({phase:'ready',mode,pid:process.pid});
 assert.equal(fs.readSync(0,new Uint8Array(1),0,1,null),1);
 const isReceive=['receive33','receive-over33','rights33','wrong-sender33'].includes(mode);
 const invalid=['exchange-over33','receive-over33','rights33','wrong-sender33'].includes(mode);
 if(mode==='bounds') {
   for(const n of [31,545]) {
     const bad=new Uint8Array(n).fill(0xa5);
     refuses(()=>native.receive(peer,bad),'ipc_argument');
     refuses(()=>native.exchange(peer,request,bad,native.now()+10000000n),'ipc_argument');
     refuses(()=>native.sendRecord(peer,bad),'ipc_argument');
     assert(bad.every(x=>x===0xa5));
   }
   const same=new ArrayBuffer(65);
   refuses(()=>native.exchange(peer,new Uint8Array(same,0,32),new Uint8Array(same,32,33),native.now()+10000000n),'ipc_argument');
   assert.equal(native.receive(peer,reply),-1);
   assert(reply.every(x=>x===0x7f));
 } else if(mode==='timeout33') {
   const end=native.now()+40000000n;
   refuses(()=>native.exchange(peer,request,reply,end),'ipc_exchange_unknown');
   assert(native.now()>=end);
   refuses(()=>native.sendRecord(peer,request),'ipc_peer_unusable');
 } else if(invalid) {
   const read=()=>isReceive?native.receive(peer,reply):native.exchange(peer,request,reply,native.now()+400000000n);
   refuses(read,'ipc_record_refused');
   assert(reply.every(x=>x===0));
   refuses(()=>native.sendRecord(peer,request),'ipc_peer_unusable');
   assert.deepEqual(fds(),held);
 } else {
   const n=isReceive?native.receive(peer,reply):native.exchange(peer,request,reply,native.now()+400000000n);
   assert.equal(n,size);assert(reply.every(x=>x===0x52));
 }
 assert(storage.subarray(0,8).every(x=>x===0xa5));
 assert(storage.subarray(8+size).every(x=>x===0xa5));
 native.closePeer(peer);closed=true;
 assert.deepEqual(fds(),before);
 out({phase:'result',mode,ok:true,capacity:size,canaries:true,fd_before:before.length,fd_after:fds().length});
} catch(e) {
 try{if(peer&&!closed)native.closePeer(peer);}catch(_){}
 out({phase:'failure',mode,code:/^[a-zA-Z0-9_]{1,80}$/.test(e.code||'')?e.code:'test_failure'});
 process.exitCode=1;
}

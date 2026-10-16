'use strict';
const {Worker}=require('node:worker_threads');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const [binary,,...texts]=process.argv.slice(2);
const [fd,pidfd,pid,uid,gid]=texts.map(Number);
const native=require(binary);
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const fds=()=>fs.readdirSync('/proc/self/fd').map(Number).filter(fd=>{
 try{fs.fstatSync(fd);return true;}catch(e){if(e.code==='EBADF')return false;throw e;}
}).sort((a,b)=>a-b);
const worker=mode=>new Worker(path.join(__dirname,'test_trial_sdk_native_ipc_aggregate_worker.cjs'),{workerData:[binary,fd,pidfd,pid,uid,gid,mode]});
const result=w=>new Promise((resolve,reject)=>{w.once('message',resolve);w.once('error',reject);});
const exit=w=>new Promise((resolve,reject)=>{w.once('exit',resolve);w.once('error',reject);});
(async()=>{
 native.protect();
 const warm=new Worker('0',{eval:true});await exit(warm);
 const before=fds();
 let peers=[];
 for(let i=0;i<8;i++)peers.push(native.openPeer(fd,pidfd,pid,uid,gid,native.now()+1200000000n,new Uint8Array(512)));
 const blocked=worker('capacity');const joined=exit(blocked);
 assert.equal(await result(blocked),'capacity_refused');assert.equal(await joined,0);
 for(const peer of peers)native.closePeer(peer);
 peers=null;global.gc();await turn();await turn();
 const armed=worker('armed');assert.equal(await result(armed),'armed');
 assert.equal(await armed.terminate(),1);
 const after=worker('openclose');const done=exit(after);
 assert.equal(await result(after),'opened_closed');assert.equal(await done,0);
 await turn();await turn();assert.deepEqual(fds(),before);
 fs.writeSync(1,JSON.stringify({phase:'result',mode:'aggregate',ok:true,shared_peer_cap:8,worker_termination_code:1,fd_before:before.length,fd_after:fds().length})+'\n');
})().catch(()=>{fs.writeSync(1,JSON.stringify({phase:'failure',mode:'aggregate',code:'test_failure'})+'\n');process.exitCode=1;});

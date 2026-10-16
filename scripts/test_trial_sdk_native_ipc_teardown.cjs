'use strict';
const {Worker}=require('node:worker_threads');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const args=process.argv.slice(2);
const fds=()=>fs.readdirSync('/proc/self/fd').map(Number).filter(fd=>{
 try{fs.fstatSync(fd);return true;}catch(e){if(e.code==='EBADF')return false;throw e;}
}).sort((a,b)=>a-b);
(async()=>{
 // Warm the existing Node Worker infrastructure before the FD preservation check.
 const warm=new Worker('0',{eval:true}); await new Promise((resolve,reject)=>{warm.once('exit',resolve);warm.once('error',reject);});
 const before=fds();
 const worker=new Worker(require('node:path').join(__dirname,'test_trial_sdk_native_ipc_readiness.cjs'),{argv:[args[0],'teardown',...args.slice(2)]});
 await new Promise((resolve,reject)=>{worker.once('message',m=>m.phase==='armed'?resolve():reject(new Error('worker_phase')));worker.once('error',reject);});
 const code=await worker.terminate();
 assert.equal(code,1);
 assert.deepEqual(fds(),before);
 fs.writeSync(1,JSON.stringify({phase:'result',mode:'teardown',ok:true,worker_termination_code:code,fd_before:before.length,fd_after:fds().length})+'\n');
})().catch(()=>{fs.writeSync(1,JSON.stringify({phase:'failure',mode:'teardown',code:'test_failure'})+'\n');process.exitCode=1;});

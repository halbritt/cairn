'use strict';
const assert=require('node:assert/strict');
const {Buffer}=require('node:buffer');
const {createSpawner,LIMITS}=require('./trial_sdk_spawn_bridge.cjs');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(text=false) {
  let events,completion,allowance=0,releases=0;
  const errors=[];
  const owner={
    launch(tuple,e){events=e;return Promise.resolve({launched:true});},
    write(id,body,done){completion=done;}, end(done){done();},
    credit(n){assert(Number.isInteger(n)&&n>0&&n<=512,'credit must be positive and bounded');assert.equal(allowance,0);allowance=n;},
    signal(){return false;},abort(){},stderr(){},release(done){releases++;done();}
  };
  const api=createSpawner(owner);
  const proc=api.spawn({command:'/synthetic',args:[],env:{},signal:new AbortController().signal});
  proc.on('error',e=>errors.push(e.code));
  if(text)proc.stdout.setEncoding('utf8');
  return {api,proc,errors,events:()=>events,complete:n=>completion({accepted:n}),
    allowance:()=>allowance,releases:()=>releases,
    send(body){assert(body.length<=allowance);allowance-=body.length;events.stdout(body);}};
}
function bounded(f) {
  const s=f.api.inspect(f.proc);
  for(const [key,limit] of Object.entries(LIMITS)) {
    if(!(key in s.used))continue;
    assert(s.used[key]>=0&&s.used[key]<=limit,`${key} used ${s.used[key]} exceeds ${limit}`);
    assert(s.peak[key]>=s.used[key]&&s.peak[key]<=limit,`${key} peak does not cover current usage`);
  }
  assert.equal(s.used.readable,f.proc.stdout.readableLength*2);
  assert.equal(s.primary,null);assert.deepEqual(f.errors,[]);
}
async function utf8BoundAndProgress(prefix,scalar,width) {
  const f=fixture(true);await turn();
  const expected='A'.repeat(prefix)+scalar.repeat(3)+'Z';
  const bytes=Buffer.from(expected);let pos=0;const output=[];let stalled=0;
  while(pos<bytes.length) {
    bounded(f);
    if(f.allowance()>0) {
      const n=Math.min(width,f.allowance(),bytes.length-pos);
      f.send(bytes.subarray(pos,pos+n));pos+=n;stalled=0;
    } else {
      const part=f.proc.stdout.read(1);
      if(part!==null){output.push(part);stalled=0;}else assert(++stalled<4,'decoder stalled with no readable data or credit');
    }
    await turn();bounded(f);
  }
  f.proc.stdin.end();await turn();f.events().stdoutEOF();await turn();bounded(f);
  let part;while((part=f.proc.stdout.read())!==null)output.push(part);
  assert.equal(output.join(''),expected,'lossless decoded output');
  f.events().wait({code:0,signal:null});await turn();
  assert.equal(f.api.inspect(f.proc).released,true);assert.equal(f.releases(),1);
  assert.equal(f.api.inspect(f.proc).used.encoder,0);
}
async function returnedLoanCleanup(accepted) {
  const f=fixture();await turn();let outcome;
  f.proc.stdin.write('abc',e=>{outcome=e&&e.code;});await turn();
  f.complete(accepted);f.events().error();await turn();
  f.events().stdoutEOF();f.events().wait({code:0,signal:null});await turn();
  const s=f.api.inspect(f.proc);
  assert.equal(outcome,'owner_transport_failed');assert.equal(s.primary,'owner_transport_failed');
  assert.equal(s.used.encoder,0,'discarded owned buffer must release reservation');
  assert.equal(s.used.ipc,0);assert.equal(s.used.offered,0);assert.equal(s.released,true);assert.equal(f.releases(),1);
}
async function outstandingLoanRetained() {
  const f=fixture();await turn();let outcome;
  f.proc.stdin.write('abc',e=>{outcome=e&&e.code;});await turn();
  f.events().error();await turn();f.events().stdoutEOF();f.events().wait({code:0,signal:null});await turn();
  const held=f.api.inspect(f.proc);
  assert(held.used.encoder>0);assert(held.used.ipc>0);assert.equal(held.released,false);
  assert.equal(outcome,undefined);
  f.complete(0);await turn();const returned=f.api.inspect(f.proc);
  assert.equal(returned.used.encoder,0);assert.equal(returned.used.ipc,0);
  assert.equal(outcome,'owner_transport_failed');assert.equal(returned.released,true);assert.equal(f.releases(),1);
}
(async()=>{
  let pass=0;const failures=[];
  async function run(name,fn){try{await fn();pass++;}catch(e){failures.push({name,error:e.message});}}
  for(const prefix of [1020,1021,1022,1023,1024])for(const scalar of ['é','€','😀'])for(const width of [1,7,512])
    await run(`utf8 prefix=${prefix} scalar=${scalar.codePointAt(0)} width=${width}`,()=>utf8BoundAndProgress(prefix,scalar,width));
  for(const n of [0,1])await run(`returned loan accepted=${n}`,()=>returnedLoanCleanup(n));
  await run('outstanding loan remains charged until returned',outstandingLoanRetained);
  console.log(JSON.stringify({passed:pass,failed:failures.length,failures}));
  if(failures.length)process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exitCode=1;});

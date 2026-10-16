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

async function malformedEOF(prefix,tail,width) {
 const f=fixture(true);await turn();
 const input=Buffer.concat([Buffer.alloc(prefix,65),Buffer.from(tail)]);
 let pos=0,stalled=0;const out=[];
 while(pos<input.length){
  bounded(f);
  if(f.allowance()>0){const n=Math.min(width,f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;stalled=0;}
  else {const part=f.proc.stdout.read(1);if(part!==null){out.push(part);stalled=0;}else assert(++stalled<4,'no decoder progress');}
  await turn();bounded(f);
 }
 f.proc.stdin.end();await turn();f.events().stdoutEOF();await turn();bounded(f);
 let part;while((part=f.proc.stdout.read())!==null)out.push(part);
 assert.equal(out.join(''),input.toString('utf8'),'stream decoding differs from complete-byte decoding');
 assert.equal(f.api.inspect(f.proc).used.encoder,0);
 f.events().wait({code:0,signal:null});await turn();assert.equal(f.releases(),1);
}
async function encodingTransitionCarriesSplitScalar(){
 const f=fixture();await turn();
 f.send(Buffer.concat([Buffer.alloc(511,65),Buffer.from([0xf0])]));
 // Entire binary allowance returned; transition happens before the next credit microtask.
 f.proc.stdout.setEncoding('utf8');await turn();bounded(f);
 f.send(Buffer.from([0x9f,0x98,0x80]));await turn();bounded(f);
 f.proc.stdin.end();await turn();f.events().stdoutEOF();await turn();bounded(f);
 assert.equal(f.proc.stdout.read(),'A'.repeat(511)+'😀');
 f.events().wait({code:0,signal:null});await turn();assert.equal(f.releases(),1);
}
async function outstandingCreditRefusesEncodingWithoutChangingMode(){
 const f=fixture();await turn();
 assert.throws(()=>f.proc.stdout.setEncoding('utf8'),e=>e.code==='readable_encoding_bound');
 assert.equal(f.api.inspect(f.proc).used.encoder,0);
 f.send(Buffer.from([0xc3,0xa9]));await turn();
 const result=f.proc.stdout.read();assert(Buffer.isBuffer(result));assert.deepEqual(result,Buffer.from([0xc3,0xa9]));
 f.proc.stdin.end();await turn();f.events().stdoutEOF();f.events().wait({code:0,signal:null});await turn();assert.equal(f.releases(),1);
}
(async()=>{
 let passed=0;const failures=[];
 async function run(name,fn){try{await fn();passed++;}catch(e){failures.push({name,error:e.message});}}
 for(const prefix of [1018,1023])for(const tail of [[0xc2],[0xe2,0x82],[0xf0,0x9f,0x98],[0xff],[0xe2,0x28,0xa1],[0xf0,0x80,0x80],[0xed,0xa0,0x80],[0xf4,0x90,0x80,0x80]])for(const width of [1,512])
  await run(`malformed/EOF ${prefix} ${tail} width${width}`,()=>malformedEOF(prefix,tail,width));
 await run('binary-to-text transition with split scalar',encodingTransitionCarriesSplitScalar);
 await run('encoding refusal preserves binary mode',outstandingCreditRefusesEncodingWithoutChangingMode);
 console.log(JSON.stringify({passed,failed:failures.length,failures}));if(failures.length)process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exitCode=1;});

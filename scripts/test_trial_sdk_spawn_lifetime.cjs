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


async function afterEOF(tail){
 const f=fixture();await turn();
 const input=Buffer.concat([Buffer.alloc(511,65),Buffer.from(tail)]);
 let pos=0;while(pos<input.length){if(f.allowance()){const n=Math.min(f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;}await turn();}
 f.events().stdoutEOF();f.proc.stdout.setEncoding('utf8');
 const value=f.proc.stdout.read();
 assert.equal(value,input.toString('utf8'),'post-EOF transition loses decoder tail');
 assert.equal(f.api.inspect(f.proc).used.encoder,0);
}
async function exactRead(surrogate){
 const f=fixture(true);await turn();const input=Buffer.from('A'.repeat(surrogate?1023:1024)+(surrogate?'😀':''));
 let pos=0, value=null;
 for(let turns=0;turns<40&&value===null;turns++){
  if(f.allowance()&&pos<input.length){let n=Math.min(f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;}
  value=f.proc.stdout.read(1024);await turn();bounded(f);
 }
 assert.notEqual(value,null,'exact capacity read stalled');assert.equal(value.length,1024);
 let rest='';while(pos<input.length){if(f.allowance()){const n=Math.min(f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;}await turn();}
 f.events().stdoutEOF();let part;while((part=f.proc.stdout.read())!==null)rest+=part;
 assert.equal(value+rest,input.toString('utf8'));bounded(f);
}
async function fragmented(split){
 const f=fixture(true);await turn();f.proc.stdin.write('live encoder');await turn();
 const input=Buffer.concat([Buffer.alloc(1023,65),Buffer.from([0xf4,0x90,0x80,0x80]),Buffer.alloc(10,66)]);
 let pos=0,out='',stall=0;
 while(pos<input.length){
  if(f.allowance()){const n=Math.min(split,f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;stall=0;}
  else {const part=f.proc.stdout.read(1);if(part!==null){out+=part;stall=0;}else assert(++stall<5);}
  await turn();bounded(f);
 }
 f.events().stdoutEOF();let part;while((part=f.proc.stdout.read())!==null)out+=part;
 bounded(f);assert.equal(out,input.toString('utf8'));f.complete(12);await turn();
}
async function callbackEOF(){
 const f=fixture(true);await turn();let out='';
 f.proc.stdout.on('data',part=>{out+=part;f.events().stdoutEOF();f.proc.stdout.read(1);});
 f.send(Buffer.from('hello'));await turn();bounded(f);assert.equal(out,'hello');assert(f.api.inspect(f.proc).stdoutEnded);
}

async function stagedEOF(fail){
 const f=fixture(true);await turn();f.proc.stdin.write('abc');await turn();
 const input=Buffer.from('A'.repeat(1023)+'😀');let pos=0;
 while(pos<input.length){assert(f.allowance()>0);let n=Math.min(f.allowance(),input.length-pos);f.send(input.subarray(pos,pos+n));pos+=n;await turn();bounded(f);}
 assert.equal(f.proc.stdout.readableLength,1024);
 assert.equal(f.api.inspect(f.proc).used.encoder,1542);
 f.events().stdoutEOF();assert.equal(f.api.inspect(f.proc).stdoutEnded,false);
 assert.equal(f.api.inspect(f.proc).used.encoder,1538);
 if(fail){f.events().error();await turn();assert.equal(f.api.inspect(f.proc).used.encoder,1536);}
 f.complete(3);await turn();f.proc.stdin.end();await turn();f.events().wait({code:0,signal:null});await turn();
 assert.equal(f.releases(),1);
 if(!fail){assert.equal(f.api.inspect(f.proc).used.encoder,2);let got='',part;while((part=f.proc.stdout.read())!==null)got+=part;assert.equal(got,input.toString());assert.equal(f.api.inspect(f.proc).used.encoder,0);bounded(f);}
 else assert.equal(f.api.inspect(f.proc).used.encoder,0);
}
async function reentrantRead(){
 const f=fixture(true);await turn();const input=Buffer.from('A'.repeat(1023)+'😀');let pos=0;
 while(pos<input.length){const n=Math.min(f.allowance(),input.length-pos);assert(n>0);f.send(input.subarray(pos,pos+n));pos+=n;await turn();}
 let out='',depth=0;
 f.proc.stdout.on('data',s=>{out+=s;if(depth===0){depth++;f.proc.stdout.read(1);depth--;}});
 f.proc.stdout.read(1024);await turn();f.events().stdoutEOF();await turn();bounded(f);assert.equal(out,input.toString());
}
(async()=>{let passed=0;const failures=[];
 async function run(name,fn){try{await fn();passed++;}catch(e){failures.push({name,error:e.message});}}
 for(const tail of [[0xc2],[0xe2,0x82],[0xf0,0x9f,0x98],[0x41]])await run('post EOF encoding '+tail,()=>afterEOF(tail));
 for(const scalar of [false,true])await run('exact read '+scalar,()=>exactRead(scalar));
 for(const split of [1,2,3,7,31,124])await run('fragmented with encoder '+split,()=>fragmented(split));
 await run('synchronous data EOF/read',callbackEOF);
 for(const fail of [false,true])await run('pending tail EOF release/failure '+fail,()=>stagedEOF(fail));
 await run('nested read with staged surrogate',reentrantRead);
 console.log(JSON.stringify({passed,failed:failures.length,failures}));if(failures.length)process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exitCode=1;});

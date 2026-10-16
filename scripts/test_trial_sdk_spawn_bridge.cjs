"use strict";
const assert=require("node:assert/strict");
const {Buffer}=require("node:buffer");
const {createSpawner,LIMITS}=require("./trial_sdk_spawn_bridge.cjs");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const tests=[]; const test=(name,fn)=>tests.push([name,fn]);
function fixture(overrides={}) {
  const calls={launch:[],writes:[],ends:[],credits:[],signals:[],aborts:0,stderr:[],release:0};
  const owner={
    launch(tuple,events) { calls.launch.push(tuple); calls.events=events; return Promise.resolve({launched:true}); },
    write(id,body,done) { calls.writes.push({id,body,done}); },
    end(done) { calls.ends.push(done); }, credit(n) {calls.credits.push(n);},
    signal(sig) { calls.signals.push(sig); return true; }, abort() {calls.aborts++;},
    stderr(body) {calls.stderr.push(Buffer.from(body));}, release(done) {calls.release++;done();}, ...overrides
  };
  const api=createSpawner(owner); const controller=new AbortController();
  const options={command:"/unavailable/claude",args:["--opaque","value"],cwd:"/synthetic",env:{A:"B"},signal:controller.signal};
  const proc=api.spawn(options); const errors=[];proc.on("error",e=>errors.push(e.code||e.message));
  return {calls,owner,api,proc,controller,options,errors};
}
function peaks(f) {
  const state=f.api.inspect(f.proc);
  for(const [group,n] of Object.entries(state.peak)) assert.ok(n<=LIMITS[group],group);
  assert.ok(Object.values(state.peak).reduce((a,b)=>a+b,0)<=24576);
  return state;
}
async function finish(f) {
  f.proc.stdin.end();await tick();
  if(f.calls.ends.length) f.calls.ends.at(-1)();
  f.calls.events.stdoutEOF();f.calls.events.wait({code:0,signal:null});await tick();
}

test("prompt proxy, one launch and call-time tuple snapshot without mapping",async()=>{
  const f=fixture();assert.equal(f.calls.launch.length,0);
  f.options.args.push("changed"); f.options.env.A="changed";
  await tick(); assert.equal(f.calls.launch.length,1);
  assert.throws(()=>f.api.spawn(f.options),/spawn_already_consumed/);
  assert.equal(f.calls.launch.length,1);
  assert.deepEqual(f.calls.launch[0],{command:"/unavailable/claude",args:["--opaque","value"],cwd:"/synthetic",env:{A:"B"}});
  assert.equal(f.proc.exitCode,null);assert.equal(f.proc.signalCode,null);assert.equal(f.proc.killed,false);
  await finish(f);assert.equal(f.calls.release,1);peaks(f);
});
test("launch failure and uncertain completion emit error without manufactured exit",async()=>{
  for(const result of [Promise.reject(new Error("failed")),Promise.resolve(undefined)]) {
    const f=fixture({launch(tuple,events) {f.calls.events=events;return result;}});
    let exits=0;f.proc.on("exit",()=>exits++);await tick();await tick();
    assert.equal(exits,0);assert.equal(f.proc.exitCode,null);assert.equal(f.errors.length,1);assert.equal(f.calls.writes.length,0);
  }
});
test("SDK initialize and user offers preserve order without native ACK",async()=>{
  const f=fixture();const callbacks=[];
  f.proc.stdin.write("initialize opaque\n",err=>{assert.ifError(err);callbacks.push("initialize");});
  f.proc.stdin.write("user opaque\n",err=>{assert.ifError(err);callbacks.push("user");});
  await tick();assert.equal(f.calls.writes.length,1);assert.deepEqual(callbacks,[]);
  assert.equal(f.calls.writes[0].body.toString(),"initialize opaque\n");
  f.calls.writes[0].done({accepted:f.calls.writes[0].body.length});await tick();
  assert.equal(f.calls.writes[1].body.toString(),"user opaque\n");
  f.calls.writes[1].done({accepted:f.calls.writes[1].body.length});await tick();
  assert.deepEqual(callbacks,["initialize","user"]);assert.equal(f.api.inspect(f.proc).accepted,30);
  await finish(f);peaks(f);
});
test("partial then zero writes await explicit owner readiness and finish only after actual bytes",async()=>{
  const f=fixture();await tick();let done=0;f.proc.stdin.write(Buffer.from("abcdef"),err=>{assert.ifError(err);done++;});
  await tick();f.calls.writes[0].done({accepted:2});await tick();
  assert.equal(f.calls.writes[1].body.toString(),"cdef");f.calls.writes[1].done({accepted:0});
  await tick();assert.equal(f.calls.writes.length,2);assert.equal(done,0);
  f.calls.events.writable();await tick();f.calls.writes[2].done({accepted:4});await tick();assert.equal(done,1);
  assert.equal(f.api.inspect(f.proc).accepted,6);await finish(f);peaks(f);
});
test("wrong or uncertain completion counts never report successful write",async()=>{
  for(const result of [undefined,{accepted:-1},{accepted:4},{accepted:1.2},{accepted:true},{accepted:NaN},{accepted:3,verified:true}]) {
    const f=fixture();await tick();let completion;f.proc.stdin.write("abc",err=>{completion=err;});await tick();
    f.calls.writes[0].done(result);await tick();
    assert.equal(completion.code,"write_completion_unknown");assert.equal(f.calls.writes.length,1);
    assert.equal(f.api.inspect(f.proc).accepted,0);assert.equal(f.api.inspect(f.proc).uncertain,3);peaks(f);
  }
});
test("duplicate completion fails without double count; wait is still observed once",async()=>{
  const f=fixture();await tick();f.proc.stdin.write("abc");await tick();const done=f.calls.writes[0].done;
  done({accepted:3});await tick();done({accepted:3});await tick();
  assert.equal(f.api.inspect(f.proc).accepted,3);assert.ok(f.errors.includes("duplicate_write_completion"));
  let exits=0;f.proc.on("exit",()=>exits++);f.calls.events.wait({code:9,signal:null});
  f.calls.events.wait({code:0,signal:null});assert.equal(exits,1);assert.equal(f.proc.exitCode,9);peaks(f);
});
test("late known completion preserves prior transport failure and pending loan",async()=>{
  const f=fixture();await tick();let result;f.proc.stdin.write("abc",e=>{result=e;});await tick();
  f.calls.events.error();await tick();assert.ok(f.api.inspect(f.proc).used.ipc>0);
  f.calls.writes[0].done({accepted:1});await tick();
  assert.equal(result.code,"owner_transport_failed");assert.equal(f.api.inspect(f.proc).accepted,1);
  assert.equal(f.api.inspect(f.proc).used.ipc,0);assert.equal(f.api.inspect(f.proc).used.encoder,0);peaks(f);
});
test("offered reservation precedes corked buffering and UTF8 conversion",async()=>{
  const f=fixture();await tick();f.proc.stdin.cork();
  assert.equal(f.proc.stdin.write("x".repeat(8500)),true);
  assert.equal(f.api.inspect(f.proc).used.offered,17000);
  assert.throws(()=>f.proc.stdin.write("x"),/bridge_offered_bound/);
  assert.equal(f.calls.writes.length,0);assert.equal(f.api.inspect(f.proc).used.offered,17000);
  f.proc.stdin.uncork();await tick();
  let index=0;
  while(index<f.calls.writes.length) {const w=f.calls.writes[index++];assert.ok(w.body.length<=512);w.done({accepted:w.body.length});await tick();}
  assert.equal(f.api.inspect(f.proc).accepted,8500);assert.equal(f.api.inspect(f.proc).used.offered,0);await finish(f);peaks(f);
});
test("multibyte string chunks preserve UTF8 bytes including surrogate split boundaries",async()=>{
  const f=fixture();await tick();const text="a".repeat(511)+"😀"+"é".repeat(150)+"\ud800";
  const pieces=[];let completed=false;f.proc.stdin.end(text,()=>{completed=true;});await tick();
  let index=0;
  while(index<f.calls.writes.length) {const w=f.calls.writes[index++];pieces.push(Buffer.from(w.body));w.done({accepted:w.body.length});await tick();}
  assert.deepEqual(Buffer.concat(pieces),Buffer.from(text,"utf8"));assert.equal(completed,false);
  assert.equal(f.calls.ends.length,1);f.calls.ends[0]();await tick();assert.equal(completed,true);
  f.calls.events.stdoutEOF();f.calls.events.wait({code:0,signal:null});peaks(f);
});
test("Readable pressure stops credit while stderr and actual wait remain pumpable",async()=>{
  const f=fixture();await tick();
  for(let i=0;i<4;i++) {assert.equal(f.calls.credits.at(-1),512);f.calls.events.stdout(Buffer.alloc(512,i));await tick();}
  assert.equal(f.calls.credits.length,4);assert.equal(f.proc.stdout.readableLength,2048);
  f.calls.events.stderr(Buffer.from("tail"));let observed;
  f.proc.on("exit",(c,s)=>{observed=[c,s,f.proc.exitCode,f.proc.signalCode];});
  f.calls.events.wait({code:null,signal:"SIGTERM"});
  assert.deepEqual(observed,[null,"SIGTERM",null,"SIGTERM"]);assert.equal(f.calls.stderr[0].toString(),"tail");
  assert.equal(f.proc.stdout.read(512).length,512);await tick();assert.equal(f.calls.credits.length,5);
  f.calls.events.stdoutEOF();assert.equal(f.proc.stdout.readableLength,1536);peaks(f);
});
test("70KiB inbound traffic streams in credited pieces without bridge whole-body buffer",async()=>{
  const f=fixture();let bytes=0;f.proc.stdout.on("data",chunk=>{for(const b of chunk) assert.equal(b,37);bytes+=chunk.length;});await tick();
  for(let i=0;i<140;i++) {assert.equal(f.calls.credits.at(-1),512);f.calls.events.stdout(Buffer.alloc(512,37));await tick();}
  assert.equal(bytes,71680);assert.equal(f.proc.stdout.readableLength,0);peaks(f);await finish(f);
});
test("EOF is not wait, exit listeners see getters, and late listeners are not replayed",async()=>{
  const f=fixture();await tick();let exits=0;f.proc.on("exit",(c,s)=>{exits++;assert.equal(f.proc.exitCode,c);assert.equal(f.proc.signalCode,s);});
  f.calls.events.stdoutEOF();assert.equal(exits,0);assert.equal(f.proc.exitCode,null);
  f.calls.events.wait({code:0,signal:null});assert.equal(exits,1);
  f.proc.once("exit",()=>{throw Error("late replay");});await tick();assert.equal(exits,1);
});
test("signal accepted means killed, never exited; invalid acceptance stays unknown",async()=>{
  let answer=false;const f=fixture({signal(){return answer;}});await tick();
  assert.equal(f.proc.kill("SIGTERM"),false);assert.equal(f.proc.killed,false);
  answer=true;assert.equal(f.proc.kill("SIGKILL"),true);assert.equal(f.proc.killed,true);assert.equal(f.proc.exitCode,null);
  assert.equal(f.proc.kill("SIGHUP"),false);answer=undefined;assert.equal(f.proc.kill("SIGTERM"),false);await tick();
  assert.ok(f.errors.includes("signal_acceptance_unknown"));assert.equal(f.proc.exitCode,null);
});
test("only forwarded SDK abort triggers owner cancellation; no new grace or kill policy",async()=>{
  const raw=new AbortController();const f=fixture();await tick();raw.abort();assert.equal(f.calls.aborts,0);
  f.proc.stdin.end();await tick();assert.equal(f.calls.ends.length,1);assert.equal(f.calls.aborts,0);
  f.calls.ends[0]();f.controller.abort();f.controller.abort();assert.equal(f.calls.aborts,1);
  assert.equal(f.calls.signals.length,0);assert.equal(f.proc.killed,false);assert.equal(f.proc.exitCode,null);
});
test("primary and cleanup errors remain separate and release never invents wait",async()=>{
  const f=fixture({release(done){f.calls.release++;done(new Error("cleanup"));}});await tick();
  f.calls.events.error();await tick();assert.equal(f.calls.release,0);
  f.calls.events.stdoutEOF();assert.equal(f.calls.release,0);f.calls.events.wait({code:5,signal:null});await tick();
  const s=f.api.inspect(f.proc);assert.equal(s.primary,"owner_transport_failed");assert.equal(s.cleanup,"owner_release_failed");
  assert.equal(f.calls.release,1);assert.equal(f.proc.exitCode,5);
});
test("oversize, no-credit and malformed wait traffic cannot become successful lifecycle",async()=>{
  for(const mode of ["oversize","credit","wait"]) {
    const f=fixture();await tick();
    if(mode==="oversize") f.calls.events.stdout(Buffer.alloc(513));
    else if(mode==="credit") {f.calls.events.stdout(Buffer.alloc(512));f.calls.events.stdout(Buffer.alloc(1));}
    else f.calls.events.wait({code:null,signal:null});
    await tick();assert.ok(f.api.inspect(f.proc).primary);assert.equal(f.proc.exitCode,null);peaks(f);
  }
});
test("ordinary async iteration preserves split UTF8 and charges decoder carry",async()=>{
  const f=fixture();f.proc.stdout.setEncoding("utf8");let received="";
  const reading=(async()=>{for await(const chunk of f.proc.stdout) received+=chunk;})();
  await tick();f.calls.events.stdout(Buffer.from([0xf0,0x9f]));await tick();
  assert.equal(received,"");assert.ok(f.api.inspect(f.proc).used.encoder>=4);
  f.calls.events.stdout(Buffer.from([0x98,0x80]));await tick();
  f.calls.events.stdoutEOF();await reading;assert.equal(received,"😀");
  assert.equal(f.api.inspect(f.proc).used.encoder,0);assert.equal(f.proc.exitCode,null);peaks(f);
});
test("owner write throw preserves uncertain external loan until an explicit late completion",async()=>{
  let completion;const f=fixture({write(id,body,done){completion=done;throw Error("uncertain boundary");}});
  await tick();let outcome;f.proc.stdin.write("abc",e=>{outcome=e;});await tick();
  assert.equal(f.api.inspect(f.proc).primary,"owner_write_threw_uncertain");
  assert.ok(f.api.inspect(f.proc).used.ipc>0);assert.equal(outcome,undefined);
  completion({accepted:0});await tick();assert.equal(outcome.code,"owner_write_threw_uncertain");
  assert.equal(f.api.inspect(f.proc).used.ipc,0);peaks(f);
});
test("credited split UTF8 stays bounded and resumes after draining",async()=>{
  const f=fixture();f.proc.stdout.setEncoding("utf8");await tick();
  const wire=Buffer.from("A".repeat(1023)+"😀");let offset=0,grant=0,credit=0,received="";
  while(offset<wire.length) {
    if(grant<f.calls.credits.length) {
      assert.equal(credit,0);credit=f.calls.credits[grant++];
      assert.ok(Number.isSafeInteger(credit)&&credit>0&&credit<=512);
    }
    if(!credit) {
      const text=f.proc.stdout.read();assert.notEqual(text,null,"credit stall requires readable progress");
      received+=text;await tick();continue;
    }
    const n=Math.min(credit,offset<1023?1023-offset:1,512);
    f.calls.events.stdout(wire.subarray(offset,offset+n));offset+=n;credit-=n;await tick();
    const state=f.api.inspect(f.proc),actual=f.proc.stdout.readableLength*2;
    assert.ok(actual<=2048,"actual UTF16 queue exceeds hard share: "+actual);
    assert.ok(state.peak.readable>=actual,"peak hides retained bytes");assert.equal(state.primary,null);
  }
  f.calls.events.stdoutEOF();for await(const text of f.proc.stdout) received+=text;
  assert.equal(received,"A".repeat(1023)+"😀");
  assert.ok(f.calls.credits.every(n=>n>0&&n<=512));peaks(f);
});
test("returned write loans release the bridge-owned encoder after transport failure",async()=>{
  for(const accepted of [0,1]) {
    const f=fixture();await tick();let outcome;f.proc.stdin.write("abc",e=>{outcome=e;});await tick();
    f.calls.writes[0].done({accepted});
    // Complete the external loan, then fail before any scheduled next offer.
    f.calls.events.error();await tick();f.calls.events.stdoutEOF();f.calls.events.wait({code:0,signal:null});await tick();
    const state=f.api.inspect(f.proc);
    assert.equal(outcome.code,"owner_transport_failed");assert.equal(state.accepted,accepted);
    assert.equal(state.released,true);assert.equal(state.cleanup,null);assert.equal(f.calls.release,1);
    assert.equal(state.used.offered,0);assert.equal(state.used.ipc,0);assert.equal(state.used.encoder,0);
    peaks(f);
  }
});
test("exact-capacity text reads progress before EOF including a split surrogate pair",async()=>{
  for(const expected of ["A".repeat(1024),"A".repeat(1023)+"😀Z"]) {
    const f=fixture();f.proc.stdout.setEncoding("utf8");await tick();
    const wire=Buffer.from(expected);let offset=0,credit=0,index=0,received=null,stalled=0;
    for(let turn=0;turn<128&&received===null;turn++) {
      if(index<f.calls.credits.length) {assert.equal(credit,0);credit=f.calls.credits[index++];assert.ok(credit>0);}
      const previous=offset;
      if(credit&&offset<wire.length) {const n=Math.min(credit,wire.length-offset);f.calls.events.stdout(wire.subarray(offset,offset+n));credit-=n;offset+=n;}
      await tick();received=f.proc.stdout.read(1024);await tick();
      const state=f.api.inspect(f.proc);assert.equal(state.primary,null);assert.ok(f.proc.stdout.readableLength*2<=2048);peaks(f);
      if(offset===previous&&received===null) assert.ok(++stalled<4,"read demand made no progress with no new grant");else stalled=0;
    }
    assert.equal(received,expected.slice(0,1024));
    let tail="";
    for(let turn=0;offset<wire.length&&turn<128;turn++) {
      tail+=f.proc.stdout.read()||"";await tick();
      if(index<f.calls.credits.length) {assert.equal(credit,0);credit=f.calls.credits[index++];}
      if(credit) {const n=Math.min(credit,wire.length-offset);f.calls.events.stdout(wire.subarray(offset,offset+n));credit-=n;offset+=n;}await tick();
    }
    f.calls.events.stdoutEOF();await tick();tail+=f.proc.stdout.read()||"";await tick();tail+=f.proc.stdout.read()||"";
    assert.equal(received+tail,expected);peaks(f);
  }
});
test("decoded staging fits encoder share while an outgoing loan and conversion carry remain live",async()=>{
  const f=fixture();await tick();f.proc.stdin.write("x".repeat(512));await tick();
  const initial=Buffer.concat([Buffer.from("A".repeat(779)+"é".repeat(121)),Buffer.from([0xf4,0x90,0x80])]);
  assert.equal(initial.length,1024);
  f.calls.events.stdout(initial.subarray(0,512));await tick();f.calls.events.stdout(initial.subarray(512));
  f.proc.stdout.setEncoding("utf8");await tick();
  assert.equal(f.proc.stdout.readableLength,900);
  const source=Buffer.alloc(124,66);let offset=0;
  while(offset<source.length) {
    const n=Math.min(f.calls.credits.at(-1),source.length-offset);
    assert.ok(n>0);f.calls.events.stdout(source.subarray(offset,offset+n));offset+=n;await tick();
    assert.equal(f.api.inspect(f.proc).primary,null);peaks(f);
  }
  assert.equal(f.proc.stdout.readableLength,1024);
  assert.equal(f.api.inspect(f.proc).peak.encoder,2048,"worked worst-case reservations reach unchanged share");
  f.calls.writes[0].done({accepted:512});await tick();
  f.proc.stdin.end();await tick();f.calls.ends[0]();f.calls.events.stdoutEOF();
  let received="";for await(const part of f.proc.stdout)received+=part;
  assert.equal(received,Buffer.concat([initial,source]).toString("utf8"));
  f.calls.events.wait({code:0,signal:null});await tick();
  assert.equal(f.api.inspect(f.proc).used.encoder,0);assert.equal(f.calls.release,1);peaks(f);
});
(async()=>{let failed=0;for(const [name,fn] of tests) {try{await fn();console.log("PASS "+name);}catch(e){failed++;console.error("FAIL "+name+"\n"+e.stack);}}
 console.log(JSON.stringify({tests:tests.length,failed,qualification:"synthetic source only"}));if(failed)process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exitCode=1;});

'use strict';
const assert=require('node:assert/strict'),{Buffer}=require('node:buffer');
const {Codec,CodecError,CreditDenied,MAX_SEQUENCE}=require('./trial_sdk_ipc.cjs');
const vectors=require('./trial_sdk_ipc_vectors.json');
const literal=Buffer.from('4349504301040000000000000000000100000000000000070000000300000000616263','hex');
const mv=x=>Uint8Array.from(x);
function ledger(){return {live:0,peak:0,calls:[],returns:[],reserve(n){this.calls.push(n);this.live+=n;this.peak=Math.max(this.peak,this.live);return true;},release(n){this.returns.push(n);this.live-=n;}};}
function codec(d='N2P_DATA',first=1n){const l=ledger();return [new Codec(d,n=>l.reserve(n),n=>l.release(n),first),l];}
let count=0;const failures=[];
function test(name,fn){try{fn();count++;}catch(e){failures.push({name,error:e.stack});}}
const code=s=>e=>e instanceof CodecError&&e.code===s;
test('literal vector',()=>{const[c,l]=codec();const f=c.decode(literal);assert.equal(Buffer.from(f.body).toString(),'abc');assert.equal(f.operation,7);assert.equal(l.live,35);f.release();assert.equal(l.live,0);});
test('all independent kind wire vectors',()=>{for(const v of vectors){
 const[c,l]=codec(v.direction,BigInt(v.sequence)),dst=new Uint8Array(v.wire.length/2);
 const f=c.encodeInto(dst,v.kind,v.operation,mv(Buffer.from(v.body,'hex')));assert.equal(Buffer.from(f.record).toString('hex'),v.wire);f.release();
 const[r,k]=codec(v.direction,BigInt(v.sequence)),g=r.decode(dst);assert.equal(Buffer.from(g.body).toString('hex'),v.body);assert.equal(g.sequence,BigInt(v.sequence));assert.equal(g.operation,v.operation);g.release();assert.equal(l.live+k.live,0);
}});
test('offset views',()=>{const b=Buffer.concat([Buffer.from('x'),literal,Buffer.from('y')]);const[c]=codec(),f=c.decode(b.subarray(1,-1));assert.equal(Buffer.from(f.body).toString(),'abc');f.release();});
test('invalid header before reservation and terminal',()=>{
 for(const[offset,value]of [[0,0],[4,2],[5,255],[6,1],[7,1],[28,1],[31,1],[15,0],[15,2],[16,255],[23,0],[24,1],[27,4]]){
  const b=mv(literal);b[offset]=value;const[c,l]=codec();assert.throws(()=>c.decode(b),CodecError);assert.deepEqual(l.calls,[]);assert(c.failed);assert.throws(()=>c.decode(literal),code('frame_failed'));
 }
});
test('wrong direction truncated or surplus record',()=>{
 for(const b of [literal.subarray(0,31),literal.subarray(0,-1),Buffer.concat([literal,Buffer.from('x')]),new Uint8Array(545)]){const[c,l]=codec();assert.throws(()=>c.decode(b),CodecError);assert.deepEqual(l.calls,[]);}
 const[c,l]=codec('P2N_DATA');assert.throws(()=>c.decode(literal),code('frame_direction'));assert.deepEqual(l.calls,[]);
});
test('one frame and replay',()=>{const[c,l]=codec(),f=c.decode(literal);assert.throws(()=>c.decode(literal),code('frame_busy'));assert.deepEqual(l.calls,[35]);f.release();assert.throws(()=>f.release(),code('frame_released'));assert.throws(()=>f.body,code('frame_released'));assert.throws(()=>c.decode(literal),code('frame_sequence'));assert.deepEqual(l.returns,[35]);});
test('credit denial before mutation preserves sequence',()=>{let grant=false;const returned=[],c=new Codec('N2P_DATA',()=>grant,n=>returned.push(n)),dst=new Uint8Array(35).fill(120),body=mv(Buffer.from('abc'));assert.throws(()=>c.encodeInto(dst,4,7,body),CreditDenied);assert(dst.every(x=>x===120));assert(!c.failed);grant=true;const f=c.encodeInto(dst,4,7,body);assert.deepEqual(Buffer.from(f.record),literal);f.release();assert.deepEqual(returned,[35]);});
test('unknown or throwing grant is terminal',()=>{
 for(const result of [null,1,'yes',Promise.resolve(true)]){const returned=[],c=new Codec('N2P_DATA',()=>result,n=>returned.push(n));assert.throws(()=>c.decode(literal),code('frame_credit_unknown'));assert(c.failed);assert.deepEqual(returned,[]);}
 const c=new Codec('N2P_DATA',()=>{throw new CreditDenied('private detail');},()=>{});assert.throws(()=>c.decode(literal),code('frame_credit_unknown'));assert(c.failed);
});
test('release error terminal never retried',()=>{const calls=[],c=new Codec('N2P_DATA',()=>true,n=>{calls.push(n);throw Error('private detail');}),f=c.decode(literal);assert.throws(()=>f.release(),code('frame_release_unknown'));assert(c.failed&&c.cleanupUnknown);assert.throws(()=>f.release(),CodecError);assert.deepEqual(calls,[35]);});
test('credit hooks cannot reenter',()=>{const c=new Codec('N2P_DATA',()=>{assert.throws(()=>c.decode(literal),code('frame_busy'));return true;},()=>{assert.throws(()=>c.decode(literal),code('frame_busy'));});const f=c.decode(literal);f.release();assert(!c.failed);});
test('maximum sequence consumed once',()=>{const[c]=codec('N2P_DATA',MAX_SEQUENCE),f=c.encodeInto(new Uint8Array(35),4,7,mv(Buffer.from('abc')));assert.equal(f.sequence,MAX_SEQUENCE);f.release();assert.throws(()=>c.encodeInto(new Uint8Array(35),4,7,mv(Buffer.from('abc'))),code('frame_sequence_exhausted'));});
test('body operation and view boundaries',()=>{
 for(const[kind,op,size]of [[4,7,0],[4,7,513],[4,0,1],[4,2**53,1],[4,true,1],[4,-1,1],[6,1,2],[6,0,1],[4,NaN,1],[4,1.5,1]]){const[c,l]=codec();assert.throws(()=>c.encodeInto(new Uint8Array(32+size),kind,op,new Uint8Array(size)),CodecError);assert.deepEqual(l.calls,[]);}
 for(const b of [new DataView(new ArrayBuffer(35)),new Uint8Array(new SharedArrayBuffer(35)),new Uint8Array(new ArrayBuffer(35,{maxByteLength:40}))]){const[c]=codec();assert.throws(()=>c.decode(b),code('frame_view'));}
 const[c]=codec(),b=new Uint8Array(35);assert.throws(()=>c.encodeInto(b,4,7,b.subarray(32)),code('frame_alias'));
});
test('each kind wrong lengths',()=>{for(const v of vectors){const len=v.body.length/2,bad=[2,4,134,136].includes(v.kind)?[0,513]:[len+1,...(len?[len-1]:[])];for(const size of bad){const raw=new Uint8Array(32+size);raw.set(Buffer.from(v.wire.slice(0,64),'hex'));new DataView(raw.buffer).setUint32(24,size);const[c,l]=codec(v.direction,BigInt(v.sequence));assert.throws(()=>c.decode(raw),CodecError);assert.deepEqual(l.calls,[]);}}});
test('receive credit denied then same frame',()=>{let grant=false;const c=new Codec('N2P_DATA',()=>grant,()=>{});assert.throws(()=>c.decode(literal),CreditDenied);grant=true;const f=c.decode(literal);assert.equal(f.sequence,1n);f.release();});
test('shared ledger bounds simultaneous loans',()=>{let live=0;const reserve=n=>{if(live+n>544)return false;live+=n;return true;},release=n=>{live-=n;};const a=new Codec('N2P_DATA',reserve,release),b=new Codec('N2P_DATA',reserve,release);const f=a.encodeInto(new Uint8Array(544),4,1,new Uint8Array(512));assert.throws(()=>b.decode(literal),CreditDenied);assert.equal(live,544);f.release();const g=b.decode(literal);g.release();assert.equal(live,0);});
console.log(JSON.stringify({passed:count,failed:failures.length,failures}));if(failures.length)process.exitCode=1;

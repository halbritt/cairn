'use strict';
const assert=require('node:assert/strict'),{Buffer}=require('node:buffer');
const {Codec,CodecError,CreditDenied}=require('./trial_sdk_ipc.cjs');
const {InputClient}=require('./trial_sdk_input_profile.cjs');
const literal=Buffer.from('4349504302090000000000000000000100000000000000070000000300000000616263','hex');
let live=0;
const c=new Codec('N2P_DATA',n=>{live+=n;return true;},n=>{live-=n;},1n,2);
const f=c.encodeInto(new Uint8Array(35),9,7,Uint8Array.of(97,98,99));
assert.deepEqual(Buffer.from(f.record),literal);assert.equal(live,35);f.release();assert.equal(live,0);
const r=new Codec('N2P_DATA',()=>true,()=>{},1n,2),g=r.decode(literal);
assert.equal(g.kind,9);assert.equal(g.operation,7);assert.equal(Buffer.from(g.body).toString(),'abc');g.release();
const receipt=Buffer.from('43495043028c00000000000000000001000000000000000700000002000000000003','hex');
const end=Buffer.from('43495043028500000000000000000002000000000000000c00000005000000000100000003','hex');
const client=new InputClient(2,100);client.beginCustody(7,3);
const sender=new Codec('P2N_DATA',()=>true,()=>{},1n,2),receiver=new Codec('P2N_DATA',()=>true,()=>{},1n,2);
for(const [kind,op,body,expected] of [[140,7,Uint8Array.of(0,3),receipt],[133,12,Uint8Array.of(1,0,0,0,3),end]]) {
 if(kind===133){assert.equal(client.snapshot().custodied_total,3);assert.equal(client.snapshot().known_native_total,null);client.beginEnd(12);}
 const f=sender.encodeInto(new Uint8Array(expected.length),kind,op,body);
 assert.deepEqual(Buffer.from(f.record),expected);f.release();
 const f2=receiver.decode(expected);client.result(f2.operation,f2.kind,f2.body);f2.release();
}
assert.equal(client.snapshot().state,'closed');assert.equal(client.snapshot().known_native_total,3);
for(const v of require('./trial_sdk_ipc_vectors.json')) {
 if([4,131,133].includes(v.kind))continue;
 const expected=Buffer.from(v.wire,'hex');expected[4]=2;
 const c=new Codec(v.direction,()=>true,()=>{},BigInt(v.sequence),2);
 const f=c.encodeInto(new Uint8Array(expected.length),v.kind,v.operation,Uint8Array.from(Buffer.from(v.body,'hex')));
 assert.deepEqual(Buffer.from(f.record),expected);f.release();
}
const legacy=Buffer.from('4349504301040000000000000000000100000000000000070000000300000000616263','hex');
for(const [profile,wire] of [[1,literal],[2,legacy]]) {
 const calls=[],c=new Codec('N2P_DATA',n=>{calls.push(n);return true;},()=>{},1n,profile);
 assert.throws(()=>c.decode(wire),e=>e instanceof CodecError&&e.code==='frame_version');assert.deepEqual(calls,[]);assert(c.failed);
}
for(const [direction,kind,size,expected] of [['N2P_DATA',4,3,'frame_kind'],['P2N_DATA',131,2,'frame_kind'],
 ['P2N_DATA',133,1,'frame_length'],['N2P_DATA',9,0,'frame_length'],['N2P_DATA',9,513,'frame_length'],
 ['P2N_DATA',140,1,'frame_length'],['P2N_DATA',140,3,'frame_length']]) {
 const calls=[],c=new Codec(direction,n=>{calls.push(n);return true;},()=>{},1n,2);
 assert.throws(()=>c.encodeInto(new Uint8Array(32+size),kind,7,new Uint8Array(size)),e=>e instanceof CodecError&&e.code===expected);
 assert.deepEqual(calls,[]);assert(c.failed);
}
for(const profile of [true,false,0,3,'2',2.1,null,2n])assert.throws(()=>new Codec('N2P_DATA',()=>true,()=>{},1n,profile),e=>e.code==='frame_configuration');
let grant=false;
const denied=new Codec('N2P_DATA',()=>grant,()=>{},1n,2),destination=new Uint8Array(35).fill(120);
assert.throws(()=>denied.encodeInto(destination,9,7,Uint8Array.of(97,98,99)),CreditDenied);
assert(destination.every(x=>x===120));assert(!denied.failed);grant=true;
const granted=denied.encodeInto(destination,9,7,Uint8Array.of(97,98,99));assert.deepEqual(Buffer.from(granted.record),literal);granted.release();
console.log(JSON.stringify({profile2_cases:5,failed:0}));

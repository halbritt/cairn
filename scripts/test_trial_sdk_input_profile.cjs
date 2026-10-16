"use strict";
const assert=require('node:assert/strict');
const {InputClient:C,parseCustody,parseEnd,ProfileError}=require('./trial_sdk_input_profile.cjs');
let checks=0;
const r=n=>Uint8Array.of(n>>8,n&255), e=n=>Uint8Array.of(1,Math.floor(n/16777216)&255,n>>>16&255,n>>>8&255,n&255);
function reject(c,f){assert.throws(f,ProfileError);assert.equal(c.snapshot().state,'failed');assert.throws(()=>c.beginEnd(999),ProfileError);checks++;}
let c=new C(2,1000);
for(const [op,offered,n] of [[1,512,200],[5,312,0],[8,312,312],[20,488,488]]) {
 c.beginCustody(op,offered); const body=r(n);c.result(op,140,body);body.fill(255);
 assert.equal(c.snapshot().known_native_total,null); if(n===0)c.writable();
}
c.beginEnd(25);c.result(25,133,e(1000));assert.equal(c.snapshot().known_native_total,1000);reject(c,()=>c.result(25,133,e(1000)));
for(const op of [true,false,0,-1,2**53,1n,'1',null,NaN,Infinity]) {c=new C(2,10);reject(c,()=>c.beginCustody(op,1));}
for(const op of [1,2]) {c=new C(2,10);c.beginCustody(2,1);c.result(2,140,r(1));reject(c,()=>c.beginEnd(op));}
c=new C(2,10);c.beginEnd(Number.MAX_SAFE_INTEGER);c.result(Number.MAX_SAFE_INTEGER,133,e(0));
for(const [op,kind] of [[2,140],[1,131],[true,140],[1,true],[1,9]]){c=new C(2,10);c.beginCustody(1,1);reject(c,()=>c.result(op,kind,r(1)));}
c=new C(2,10);reject(c,()=>c.result(1,140,r(1)));
c=new C(2,10);c.beginCustody(1,1);reject(c,()=>c.beginEnd(2));
for(const offered of [0,513,true,-1,1.5]){c=new C(2,1000);reject(c,()=>c.beginCustody(1,offered));}
c=new C(2,1);reject(c,()=>c.beginCustody(1,2));c=new C(2,10);c.beginCustody(1,1);reject(c,()=>c.result(1,140,r(2)));
for(const [p,m] of [[1,1],[true,1],[2,true],[2,0],[2,3145729]])assert.throws(()=>new C(p,m),ProfileError);
for(const phase of ['ready','pending','paused']) {c=new C(2,10);if(phase!=='ready')c.beginCustody(1,1);if(phase==='paused')c.result(1,140,r(0));reject(c,()=>c.transportUnknown());assert.equal(c.snapshot().unknown,true);}
c=new C(2,10);reject(c,()=>c.writable());c=new C(2,10);c.beginCustody(1,1);c.result(1,140,r(0));reject(c,()=>c.beginEnd(2));
assert.equal(parseCustody(r(65535)),65535);assert.equal(parseEnd(e(4294967295)),4294967295);
for(const body of [new Uint8Array(0),new Uint8Array(1),new Uint8Array(3),[0,1],new Uint16Array(1),new DataView(new ArrayBuffer(2)),new Uint8Array(new SharedArrayBuffer(2))])assert.throws(()=>parseCustody(body),ProfileError);
if(Object.getOwnPropertyDescriptor(ArrayBuffer.prototype,'resizable'))assert.throws(()=>parseCustody(new Uint8Array(new ArrayBuffer(2,{maxByteLength:4}))),ProfileError);
const detached=new Uint8Array(2);structuredClone(detached.buffer,{transfer:[detached.buffer]});assert.throws(()=>parseCustody(detached),ProfileError);
for(const body of [new Uint8Array(5),e(1),new Uint8Array(0),Uint8Array.of(2,0,0,0,0)]){c=new C(2,10);c.beginEnd(1);reject(c,()=>c.result(1,133,body));}
c=new C(2,10);c.beginCustody(1,1);const snapshot=c.snapshot();snapshot.pending[0]=99; c.result(1,140,r(1));assert.equal(c.snapshot().custodied_total,1);
// Numeric results cannot alias the supplied view; helpers keep no body fields.
const b=r(258),number=parseCustody(b);b.fill(0);assert.equal(number,258);
console.log(JSON.stringify({passed:true,terminal_scenarios:checks}));

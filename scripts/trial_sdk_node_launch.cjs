'use strict';
// No full JSON/UTF8 token allocation: yields scalar byte numbers only.
const fail=()=>{throw Object.assign(new Error('owner_launch_tuple'),{code:'owner_launch_tuple'});};
const plain=value=>value!==null && (Object.getPrototypeOf(value)===Object.prototype || Object.getPrototypeOf(value)===null);
function dataKeys(value,maximum) {
 if(!plain(value))fail();
 const keys=Reflect.ownKeys(value);if(keys.length>maximum)fail();
 for(const key of keys) {
  if(typeof key!=='string')fail();
  const d=Object.getOwnPropertyDescriptor(value,key);
  if(!d || !Object.hasOwn(d,'value') || !d.enumerable)fail();
 }
 return keys;
}
function text(value) {if(typeof value!=='string'||value.length>4096)fail();}
function* ascii(s) {for(let i=0;i<s.length;i++)yield s.charCodeAt(i);}
function* quote(s) {
 yield 34;
 const hex='0123456789abcdef';
 for(let i=0;i<s.length;i++) {
  let c=s.charCodeAt(i);
  if(c===34||c===92) {yield 92;yield c;continue;}
  if(c<32 || (c>=0xd800&&c<=0xdfff && !(c<=0xdbff && i+1<s.length && s.charCodeAt(i+1)>=0xdc00 && s.charCodeAt(i+1)<=0xdfff))) {
   yield 92;yield 117;
   for(let shift=12;shift>=0;shift-=4)yield hex.charCodeAt((c>>shift)&15);
  } else {
   if(c>=0xd800&&c<=0xdbff)c=0x10000+((c-0xd800)<<10)+(s.charCodeAt(++i)-0xdc00);
   if(c<128)yield c;
   else if(c<2048){yield 192|(c>>6);yield 128|(c&63);}
   else if(c<65536){yield 224|(c>>12);yield 128|((c>>6)&63);yield 128|(c&63);}
   else {yield 240|(c>>18);yield 128|((c>>12)&63);yield 128|((c>>6)&63);yield 128|(c&63);}
  }
 }
 yield 34;
}
function prepareLaunch(tuple) {
 const keys=dataKeys(tuple,4);
 if(!keys.includes('command')||!keys.includes('args')||!keys.includes('env')||keys.some(k=>!['command','args','env','cwd'].includes(k)))fail();
 text(tuple.command);
 if(!Array.isArray(tuple.args)||tuple.args.length>64)fail();
 for(let i=0;i<tuple.args.length;i++) {
  const d=Object.getOwnPropertyDescriptor(tuple.args,String(i));
  if(!d||!Object.hasOwn(d,'value'))fail();text(d.value);
 }
 if(Reflect.ownKeys(tuple.args).length!==tuple.args.length+1)fail();
 const envKeys=dataKeys(tuple.env,128);
 for(const key of envKeys){text(key);if(tuple.env[key]!==undefined)text(tuple.env[key]);}
 if(tuple.cwd!==undefined)text(tuple.cwd);
 Object.freeze(tuple.args);Object.freeze(tuple.env);Object.freeze(tuple);
 function* bytes() {
  yield* ascii('{"command":');yield* quote(tuple.command);yield* ascii(',"args":[');
  for(let i=0;i<tuple.args.length;i++){if(i)yield 44;yield* quote(tuple.args[i]);}
  yield* ascii('],"env":{');let first=true;
  for(const key of envKeys) {
   if(tuple.env[key]===undefined)continue;
   if(!first)yield 44;first=false;yield* quote(key);yield 58;yield* quote(tuple.env[key]);
  }
  yield 125;
  if(tuple.cwd!==undefined){yield* ascii(',"cwd":');yield* quote(tuple.cwd);}
  yield 125;
 }
 let length=0;
 for(const ignored of bytes()){void ignored;if(++length>8500)fail();}
 return {length,iterator:bytes()};
}
module.exports=Object.freeze({prepareLaunch});

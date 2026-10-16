'use strict';
// Pure framing: supplied views are already allocated/accounted by the caller.
const MAX_SEQUENCE=(1n<<64n)-1n,MAX_OPERATION=Number.MAX_SAFE_INTEGER;
const TABLE=Object.freeze({
 1:['N2P_DATA',8,8,true],2:['N2P_DATA',1,512,true],3:['N2P_DATA',0,0,true],
 4:['N2P_DATA',1,512,true],5:['N2P_DATA',0,0,true],6:['N2P_DATA',2,2,false],
 7:['N2P_DATA',0,0,false],8:['N2P_DATA',0,0,true],
 129:['P2N_DATA',0,0,true],130:['P2N_DATA',1,1,true],131:['P2N_DATA',2,2,true],
 132:['P2N_DATA',0,0,false],133:['P2N_DATA',1,1,true],134:['P2N_DATA',1,512,false],
 135:['P2N_DATA',0,0,false],136:['P2N_DATA',1,512,false],137:['P2N_DATA',8,8,false],
 138:['P2N_DATA',2,2,false],139:['P2N_DATA',1,1,true],
 201:['N2P_CONTROL',1,1,true],202:['P2N_CONTROL',1,1,true]
});
for(const row of Object.values(TABLE))Object.freeze(row);
const TABLE_PROFILE_2={...TABLE,
 9:['N2P_DATA',1,512,true],140:['P2N_DATA',2,2,true],133:['P2N_DATA',5,5,true]};
delete TABLE_PROFILE_2[4];delete TABLE_PROFILE_2[131];
for(const row of Object.values(TABLE_PROFILE_2))Object.freeze(row);
Object.freeze(TABLE_PROFILE_2);
const DIRECTIONS=new Set(Object.values(TABLE).map(row=>row[0]));
class CodecError extends Error {constructor(code){super(code);this.code=code;}}
class CreditDenied extends CodecError {}
function view(value){
 if(!(value instanceof Uint8Array)||!(value.buffer instanceof ArrayBuffer)||value.buffer.resizable)
  throw new CodecError('frame_view');
 return value;
}
class Frame {
 #record;#finish;#metadata;
 constructor(record,kind,sequence,operation,finish){this.#record=record;this.#finish=finish;this.#metadata=[kind,sequence,operation];}
 get kind(){return this.#metadata[0];}
 get sequence(){return this.#metadata[1];}
 get operation(){return this.#metadata[2];}
 get record(){if(this.#record===null)throw new CodecError('frame_released');return this.#record;}
 get body(){return this.record.subarray(32);}
 release(){const size=this.record.byteLength;this.#record=null;this.#finish(size);}
}
class Codec {
 #direction;#reserve;#release;#next;#profile;#table;#busy=false;#failed=false;#cleanupUnknown=false;
 constructor(direction,reserve,release,firstSequence=1n,profile=1){
  if(!DIRECTIONS.has(direction)||typeof reserve!=='function'||typeof release!=='function'||
    typeof firstSequence!=='bigint'||firstSequence<1n||firstSequence>MAX_SEQUENCE||
    ![1,2].includes(profile))throw new CodecError('frame_configuration');
  this.#direction=direction;this.#reserve=reserve;this.#release=release;this.#next=firstSequence;
  this.#profile=profile;this.#table=profile===1?TABLE:TABLE_PROFILE_2;
 }
 get failed(){return this.#failed;}
 get cleanupUnknown(){return this.#cleanupUnknown;}
 #start(){
  if(this.#failed)throw new CodecError('frame_failed');
  if(this.#busy)throw new CodecError('frame_busy');
  if(this.#next===null)throw new CodecError('frame_sequence_exhausted');
  this.#busy=true;
 }
 #check(kind,operation,size){
  if(!Number.isInteger(kind)||!Object.hasOwn(this.#table,kind))throw new CodecError('frame_kind');
  const [direction,low,high,correlated]=this.#table[kind];
  if(direction!==this.#direction)throw new CodecError('frame_direction');
  if(size<low||size>high)throw new CodecError('frame_length');
  if(!Number.isSafeInteger(operation)||operation<0||operation>MAX_OPERATION||((operation!==0)!==correlated))throw new CodecError('frame_operation');
 }
 #acquire(size){
  let result;try{result=this.#reserve(size);}catch(_){throw new CodecError('frame_credit_unknown');}
  if(result===false)throw new CreditDenied('frame_credit');
  if(result!==true)throw new CodecError('frame_credit_unknown');
 }
 #finish(size){
  try{this.#release(size);}catch(_){this.#cleanupUnknown=this.#failed=true;throw new CodecError('frame_release_unknown');}
  finally{this.#busy=false;}
 }
 #issue(record,kind,operation){
  const f=new Frame(record,kind,this.#next,operation,size=>this.#finish(size));
  this.#next=this.#next===MAX_SEQUENCE?null:this.#next+1n;return f;
 }
 #refuse(exc,acquired){
  if(!(exc instanceof CreditDenied))this.#failed=true;
  if(acquired){try{this.#finish(acquired);}catch(_){/* cleanupUnknown retains the second failure. */}}
  else this.#busy=false;
 }
 encodeInto(destination,kind,operation,body){
  this.#start();let acquired=0;
  try{
   destination=view(destination);body=view(body);this.#check(kind,operation,body.byteLength);
   const size=32+body.byteLength;
   if(destination.byteLength!==size)throw new CodecError('frame_length');
   if(destination.buffer===body.buffer)throw new CodecError('frame_alias');
   this.#acquire(size);acquired=size;
   const d=new DataView(destination.buffer,destination.byteOffset,destination.byteLength);
   d.setUint32(0,0x43495043);d.setUint8(4,this.#profile);d.setUint8(5,kind);d.setUint16(6,0);
   d.setBigUint64(8,this.#next);d.setBigUint64(16,BigInt(operation));d.setUint32(24,body.byteLength);d.setUint32(28,0);
   destination.set(body,32);return this.#issue(destination,kind,operation);
  }catch(e){this.#refuse(e,acquired);throw e;}
 }
 decode(record){
  this.#start();let acquired=0;
  try{
   record=view(record);if(record.byteLength<32||record.byteLength>544)throw new CodecError('frame_length');
   const d=new DataView(record.buffer,record.byteOffset,record.byteLength);
   if(d.getUint32(0)!==0x43495043)throw new CodecError('frame_magic');
   if(d.getUint8(4)!==this.#profile)throw new CodecError('frame_version');
   if(d.getUint16(6)||d.getUint32(28))throw new CodecError('frame_reserved');
   const kind=d.getUint8(5),sequence=d.getBigUint64(8),op=d.getBigUint64(16),size=d.getUint32(24);
   if(record.byteLength!==32+size)throw new CodecError('frame_length');
   if(op>BigInt(MAX_OPERATION))throw new CodecError('frame_operation');
   const operation=Number(op);this.#check(kind,operation,size);
   if(sequence!==this.#next)throw new CodecError('frame_sequence');
   this.#acquire(record.byteLength);acquired=record.byteLength;return this.#issue(record,kind,operation);
  }catch(e){this.#refuse(e,acquired);throw e;}
 }
}
module.exports=Object.freeze({Codec,CodecError,CreditDenied,MAX_SEQUENCE,MAX_OPERATION});

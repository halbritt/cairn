'use strict';
// Fixed ordinary composition observer. No SDK, provider or native CLI is loaded.
const fs=require('node:fs'),path=require('node:path');
const {allocateOwnerStorage}=require('./trial_sdk_node_owner.cjs');
const {createCustodySpawner}=require('./trial_sdk_spawn_bridge.cjs');
const addon=require('../integrations/sdk-ipc/binding.node');
const [dataFD,controlFD,parentPIDFD,parentPID,parentUID,parentGID]=process.argv.slice(2,8).map(Number);
const deadline=BigInt(process.argv[8]);
if(process.argv.length!==9)throw new Error('fixed_arguments');
const command='KILL';
const MESSAGE=Buffer.from('{"type":"fixture","value":"雪"}\n');
const expectedSignal='SIGKILL';
const target=path.join(__dirname,'trial_sdk_node_owner_child.py');
const tuple={command:'/usr/bin/python3',args:['-I','-B',target],env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8',CAIRN_FIXTURE:'q"\\雪'.repeat(200)},cwd:__dirname};
const expected={dataTX:544,dataRX:544,controlTX:33,controlRX:33,scalar:8,c_auxiliary:128};
const claims=new Set();let claimed=0,printed=false;
function report(value){fs.writeSync(1,JSON.stringify(value)+'\n');}
function failure(error){
 if(printed)return;printed=true;process.exitCode=1;
 const code=/^[a-zA-Z0-9_]{1,96}$/.test(error.code||'')?error.code:
            /^[a-z][a-z_]{0,95}$/.test(error.message||'')?error.message:'actor_failure';
 report({phase:'result',ok:false,code});
}
try {
 addon.protect();
 const storage=allocateOwnerStorage({claim(name,size){
  if(!Object.hasOwn(expected,name)||expected[name]!==size||claims.has(name)||claimed+size>1290)return false;
  claimed+=size;claims.add(name);return true;
 }});
 let dataPeer,controlPeer;
 storage.borrowRXsetup(scratch=>{
  dataPeer=addon.openPeer(dataFD,parentPIDFD,parentPID,parentUID,parentGID,deadline,scratch);
  controlPeer=addon.openPeer(controlFD,parentPIDFD,parentPID,parentUID,parentGID,deadline,scratch);
  report({phase:'prepared',pid:process.pid,claimed});
  if(fs.readSync(0,scratch,0,1,null)!==1||scratch[0]!==71)throw new Error('bootstrap');
 });
 for(const fd of [dataFD,controlFD,parentPIDFD,0])fs.closeSync(fd);
 let stderrBytes=0,ownerReleased=false,ended=false,inputDone=false,wait=null,totalOutput=0,pressureText='',pressure=null;
 let lastArmMask=null,waitArms=0,exchanges=0,lastExchangeCount=null,controlScheduled=false,controlDone=false,controlRecord=null;
 const instrumented=Object.fromEntries(['now','sendRecord','receive','exchange','waitPeer','cancelWait','closePeer'].map(name=>[name,addon[name]]));
 instrumented.waitPeer=(...args)=>{const promise=addon.waitPeer(...args);lastArmMask=args[1];waitArms++;return promise;};
 instrumented.exchange=(...args)=>{const count=addon.exchange(...args);exchanges++;lastExchangeCount=count;return count;};
 instrumented.receive=(...args)=>{const count=addon.receive(...args);if(count>0&&!controlScheduled)setImmediate(selectControl);return count;};
 const owner=storage.bind({addon:instrumented,dataPeer,controlPeer,deadline,stderrSink(body){
  stderrBytes+=body.length;if(stderrBytes>128||pressure!==null)throw new Error('stderr_bound');
  for(const byte of body){if(byte>127)throw new Error('stderr_ascii');pressureText+=String.fromCharCode(byte);}
  if(pressureText.endsWith('\n')){
   pressure=JSON.parse(pressureText);
   if(Object.keys(pressure).sort().join(',')!=='accepted,eagain,errno'||pressure.eagain!==true||pressure.errno!==11||
      !Number.isInteger(pressure.accepted)||pressure.accepted<2048||pressure.accepted>1024*1024)throw new Error('pressure_metadata');
  }
  selectControl();
 }});
 let bridge,proc;
 function finish(){
  if(printed||!ownerReleased||!ended||!inputDone||wait===null)return;
  if(!pressure||totalOutput!==pressure.accepted+MESSAGE.length||wait.code!==null||wait.signal!==expectedSignal||stderrBytes!==pressureText.length||claimed!==1290||!controlDone||exchanges!==1||lastExchangeCount!==33)throw new Error('joined_result');
  printed=true;
  report({phase:'result',ok:true,actorPID:process.pid,outputBytes:totalOutput,stderrBytes,claimed,pressure,
          inputBytes:MESSAGE.length,echoBytes:MESSAGE.length,command,control:controlRecord,exchanges,lastExchangeCount,waitArms,proxy:bridge.inspect(proc),owner:owner.inspect()});
 }
 function selectControl(){
  if(printed||controlScheduled||!inputDone||!pressure||!bridge||!proc||bridge.inspect(proc).used.readable!==2048)return;
  controlScheduled=true;
  setImmediate(()=>{
   try{
    const before=owner.inspect(),readableBefore=bridge.inspect(proc).used.readable;
    if(readableBefore!==2048||before.waitArmed!==true||lastArmMask!==1||before.waitSeen!==false)throw new Error('pressure_read_not_armed');
    const accepted=proc.kill(expectedSignal);
    const after=owner.inspect();
    controlRecord={command,readArmedBefore:before.waitArmed,maskBefore:lastArmMask,accepted,
                   controlAccepted:after.controlAccepted,waitSeenAfterExchange:after.waitSeen,readableBefore};
    if(accepted!==true||after.controlAccepted!==true)throw new Error('control_not_accepted');
    controlDone=true;
    proc.stdout.removeListener('readable',selectControl);
    proc.stdout.on('data',body=>{
     try{
      if(!Buffer.isBuffer(body)||body.length>2048)throw new Error('drain_fragment');
      for(const byte of body){
       const expected=totalOutput<MESSAGE.length?MESSAGE[totalOutput]:65;
       if(byte!==expected)throw new Error('drain_spelling');
       totalOutput++;
      }
      if(totalOutput>pressure.accepted+MESSAGE.length)throw new Error('drain_count');
     }catch(error){failure(error);}
    });
    proc.stdout.resume();finish();
   }catch(error){failure(error);}
  });
 }
 const observed={...owner,release(done){owner.release(error=>{
  done(error);if(error)return failure(error);ownerReleased=true;finish();
 });}};
 bridge=createCustodySpawner(observed);
 const abort=new AbortController();
 proc=bridge.spawn({...tuple,signal:abort.signal});
 proc.on('error',failure);
 proc.on('exit',(code,signal)=>{wait={code,signal};finish();});
 proc.stdout.on('error',failure);
 proc.stdout.on('readable',selectControl);
 proc.stdout.on('end',()=>{ended=true;finish();});
 proc.stdin.on('error',failure);
 proc.stdin.end(MESSAGE,error=>{if(error)return failure(error);inputDone=true;selectControl();finish();});
} catch(error){failure(error);}

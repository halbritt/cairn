'use strict';
const {Codec}=require('./trial_sdk_ipc.cjs');
const {prepareLaunch}=require('./trial_sdk_node_launch.cjs');
const fault=code=>Object.assign(new Error(code),{code});
const PLAN=Object.freeze({dataTX:544,dataRX:544,controlTX:33,controlRX:33,scalar:8,c_auxiliary:128});
const SIGNALS=Object.freeze({1:'SIGHUP',2:'SIGINT',3:'SIGQUIT',4:'SIGILL',5:'SIGTRAP',6:'SIGABRT',7:'SIGBUS',8:'SIGFPE',9:'SIGKILL',10:'SIGUSR1',11:'SIGSEGV',12:'SIGUSR2',13:'SIGPIPE',14:'SIGALRM',15:'SIGTERM',16:'SIGSTKFLT',17:'SIGCHLD',18:'SIGCONT',19:'SIGSTOP',20:'SIGTSTP',21:'SIGTTIN',22:'SIGTTOU',23:'SIGURG',24:'SIGXCPU',25:'SIGXFSZ',26:'SIGVTALRM',27:'SIGPROF',28:'SIGWINCH',29:'SIGIO',30:'SIGPWR',31:'SIGSYS'});
function ordinaryBytes(body,maximum) {
 return body instanceof Uint8Array && body.buffer instanceof ArrayBuffer && !body.buffer.resizable && body.byteLength>0 && body.byteLength<=maximum;
}

function allocateOwnerStorage(quota) {
 if(!quota||typeof quota.claim!=='function')throw fault('owner_quota');
 const claims=new Map(),buffers=Object.create(null);
 let setup='allocated',bound=false;
 // These debits subdivide a genuine parent-held prelaunch quota. Never refund it here.
 for(const [name,size] of Object.entries(PLAN)) {
  if(quota.claim(name,size)!==true)throw fault('owner_quota');
  claims.set(name,size);
  if(name!=='c_auxiliary')buffers[name]=new Uint8Array(size);
 }
 const inspect=()=>Object.freeze({backingBytes:1162,cAuxiliaryBytes:128,claims:claims.size,setup,bound,physicalRefunded:false});
 function borrowRXsetup(use) {
  if(setup!=='allocated'||bound||typeof use!=='function')throw fault('owner_setup');
  setup='borrowed';
  try {
   const result=use(buffers.dataRX.subarray(0,512));
   if(result&&typeof result.then==='function')throw fault('owner_setup_async');
   setup='prepared';return result;
  } catch(e) {setup='failed';throw e;}
  finally {buffers.dataRX.fill(0);}
 }
 function bind({addon,dataPeer,controlPeer,deadline,stderrSink}) {
  if(setup!=='prepared'||bound)throw fault('owner_setup');
  if(!addon||['now','sendRecord','receive','exchange','waitPeer','cancelWait','closePeer'].some(k=>typeof addon[k]!=='function')||
    !dataPeer||!controlPeer||dataPeer===controlPeer||typeof deadline!=='bigint'||typeof stderrSink!=='function')throw fault('owner_binding');
  const now=addon.now();if(typeof now!=='bigint'||deadline<=now)throw fault('owner_deadline');
  // The trusted builder owns both authentic Peers until this complete transfer.
  bound=true;
  return buildOwner(addon,dataPeer,controlPeer,deadline,stderrSink,buffers,inspect);
 }
 return Object.freeze({borrowRXsetup,bind,inspect});
}

function buildOwner(addon,dataPeer,controlPeer,deadline,stderrSink,buffers,storageInspect) {
 let phase='bound',primary=null,cleanup=null,failureStage=null,events=null,consumed=false;
 let dataOp=0,controlOp=0,lastProxyID=0,custodied=0,nativeWritten=null;
 let launchResolve=null,launchReject=null,launch=null,launchStep=0,launchOp=0;
 let custodyPending=null,endPending=null,creditPending=null,releasePending=null;
 let creditRemaining=0,paused=false,inputClosed=false,waitSeen=false;
 let stdoutEOF=false,stderrEOF=false,bothEOF=false,releaseAck=false,waitReturncode=null;
 let controlAccepted=null;
 let txFrame=null,txIntent=null,rxFrame=null,controlBusy=false,controlUnknown=false;
 let readBlocked=false,writeBlocked=false,waitToken=null,generation=0,pumping=false,scheduled=false;
 let errorScheduled=false,closeStarted=false;
 const slots=Object.fromEntries(['dataTX','dataRX','controlTX','controlRX'].map(name=>[name,{buffer:buffers[name],preparing:null,loan:null}]));
 function checkedView(slot,view) {
  if(!(view instanceof Uint8Array)||view.buffer!==slot.buffer.buffer||view.byteOffset!==slot.buffer.byteOffset||view.byteLength>slot.buffer.byteLength||slot.loan!==null)throw fault('owner_loan');
 }
 function codec(direction,name) {
  const slot=slots[name];
  return new Codec(direction,n=>{
   if(!slot.preparing||slot.preparing.buffer!==slot.buffer.buffer||slot.preparing.byteOffset!==slot.buffer.byteOffset||slot.preparing.byteLength!==n||slot.loan!==null)throw fault('owner_loan');
   slot.loan=n;return true;
  },n=>{
   if(slot.loan!==n)throw fault('owner_loan_return');slot.loan=null;
  },1n,2);
 }
 const dataEncode=codec('N2P_DATA','dataTX'),dataDecode=codec('P2N_DATA','dataRX');
 const controlEncode=codec('N2P_CONTROL','controlTX'),controlDecode=codec('P2N_CONTROL','controlRX');
 function loan(name,view,operation) {
  const slot=slots[name];checkedView(slot,view);slot.preparing=view;
  try{return operation();}finally{slot.preparing=null;}
 }
 function encode(codec,name,kind,op,body) {
  const destination=buffers[name].subarray(0,32+body.byteLength);
  return loan(name,destination,()=>codec.encodeInto(destination,kind,op,body));
 }
 function decode(codec,name,length) {
  const record=buffers[name].subarray(0,length);
  return loan(name,record,()=>codec.decode(record));
 }
 function nextOperation() {if(dataOp===Number.MAX_SAFE_INTEGER)throw fault('owner_operation');return ++dataOp;}
 function emit(name,...args) {
  try{events[name](...args);}catch(_){fail('owner_event_callback');}
 }
 function complete(callback,...args) {
  try{callback(...args);}catch(_){fail('owner_completion_callback');}
 }
 function fail(code) {
  if(!primary){primary=code;failureStage=phase;}
  phase='failed';
  if(launch) {launch.iterator.return();launch=null;}
  if(launchReject){const reject=launchReject;launchReject=launchResolve=null;reject(fault(primary));}
  // No synthetic custody0/end/wait/release callback. Unresolved loans stay charged.
  if(waitToken&&!waitToken.cancelAttempted)cancelArm();
  if(events&&!errorScheduled) {
   errorScheduled=true;
   queueMicrotask(()=>{try{events.error();}catch(_){cleanup=cleanup||'owner_error_callback';}});
  }
 }
 function usable() {if(primary||phase==='released'||phase==='closing')throw fault(primary||'owner_closed');}
 function scalar(length) {return buffers.scalar.subarray(0,length);}
 function scalarView() {return new DataView(buffers.scalar.buffer,buffers.scalar.byteOffset,8);}
 function candidateTX() {
  if(phase==='launching') {
   if(launchStep===0){scalarView().setUint32(0,launch.length);scalarView().setUint32(4,2);return {kind:1,op:launchOp,body:scalar(8)};}
   if(launchStep===1) {
    let used=0;
    while(used<512) {const next=launch.iterator.next();if(next.done)break;buffers.dataRX[used++]=next.value;}
    if(used)return {kind:2,op:launchOp,body:buffers.dataRX.subarray(0,used)};
    launchStep=2;
   }
   return {kind:3,op:launchOp,body:scalar(0)};
  }
  if(phase!=='launched'&&phase!=='release_requested')return null;
  if(custodyPending&&!custodyPending.sent)return {kind:9,op:custodyPending.op,body:custodyPending.body};
  if(endPending&&!endPending.sent)return {kind:5,op:endPending.op,body:scalar(0)};
  if(creditPending!==null){scalarView().setUint16(0,creditPending);return {kind:6,op:0,body:scalar(2),credit:creditPending};}
  if(releasePending&&!releasePending.sent&&bothEOF&&waitSeen&&inputClosed&&!custodyPending&&!endPending) return {kind:8,op:releasePending.op,body:scalar(0)};
  return null;
 }
 function hasTX() {
  return phase==='launching'||(phase==='launched'||phase==='release_requested')&&
   (custodyPending&&!custodyPending.sent||endPending&&!endPending.sent||creditPending!==null||releasePending&&!releasePending.sent&&bothEOF&&waitSeen&&inputClosed&&!custodyPending&&!endPending);
 }
 function receiveEnabled() {return !['bound','launching','failed','closing','released'].includes(phase);}
 function sent(intent) {
  if(intent.kind===1)launchStep=1;
  else if(intent.kind===3){phase='await_launch';launch=null;}
  else if(intent.kind===9)custodyPending.sent=true;
  else if(intent.kind===5)endPending.sent=true;
  else if(intent.kind===6){creditRemaining=intent.credit;creditPending=null;}
  else if(intent.kind===8)releasePending.sent=true;
 }
 function cancelArm() {
  const token=waitToken;if(!token||token.cancelAttempted)return;
  token.cancelAttempted=true;
  try {
   const result=addon.cancelWait(dataPeer);
   if(result!==true&&result!==false)throw fault('owner_cancel_unknown');
   // false can mean native completion queued its Promise before this JS turn.
   token.cancelRequested=result;
  } catch(_){cleanup=cleanup||'owner_cancel_unknown';fail('owner_cancel_unknown');}
 }
 function wake() {
  if(primary||phase==='released'||phase==='closing')return;
  if(waitToken) {
   if(!(waitToken.mask&2)&&hasTX())cancelArm();
   return; // Must consume this exact Promise/generation before touching its Peer.
  }
  if(!scheduled&&!pumping){scheduled=true;queueMicrotask(()=>{scheduled=false;pump();});}
 }
 function arm(mask) {
  if(waitToken||!mask)throw fault('owner_wait_state');
  if(generation===Number.MAX_SAFE_INTEGER){fail('owner_wait_generation');return;}
  const token={generation:++generation,mask,cancelAttempted:false,cancelRequested:false};waitToken=token;
  let promise;
  try{promise=addon.waitPeer(dataPeer,mask,deadline);}catch(_){waitToken=null;fail('owner_wait_unknown');return;}
  if(!promise||typeof promise.then!=='function'){fail('owner_wait_unknown');return;}
  promise.then(ready=>{
   if(waitToken!==token){fail('owner_stale_wait');return;}
   waitToken=null;
   if(token.cancelRequested){fail('owner_cancel_completion_unknown');return;}
   if(!Number.isInteger(ready)||ready<1||ready>3||(ready&~mask)){fail('owner_readiness_unknown');return;}
   if(ready&1)readBlocked=false;if(ready&2)writeBlocked=false;wake();
  },err=>{
   if(waitToken!==token){fail('owner_stale_wait');return;}
   waitToken=null;
   if(token.cancelRequested&&err&&err.code==='ipc_wait_cancelled'){wake();return;}
   fail('owner_wait_unknown');
  });
 }
 function pump() {
  if(pumping||waitToken||primary||phase==='released'||phase==='closing')return;
  pumping=true;let progressed=false,quantum=0;
  try {
   do {
    progressed=false;
    if(!txFrame){txIntent=candidateTX();if(txIntent)txFrame=encode(dataEncode,'dataTX',txIntent.kind,txIntent.op,txIntent.body);}
    if(txFrame&&!writeBlocked) {
     const count=addon.sendRecord(dataPeer,txFrame.record);
     if(count===-1)writeBlocked=true;
     else {
      if(count!==txFrame.record.byteLength)throw fault('owner_send_unknown');
      const intent=txIntent;txFrame.release();txFrame=txIntent=null;writeBlocked=false;
      sent(intent);progressed=true;
     }
    }
    if(receiveEnabled()&&!readBlocked) {
     const count=addon.receive(dataPeer,buffers.dataRX);
     if(count===-1)readBlocked=true;
     else {
      if(!Number.isInteger(count)||count<32||count>544)throw fault('owner_receive_unknown');
      rxFrame=decode(dataDecode,'dataRX',count);
      try{dispatch(rxFrame);}finally{rxFrame.release();rxFrame=null;}
      progressed=true;
      if(releaseAck){finishRelease();break;}
     }
    }
    ++quantum;
   } while(progressed&&quantum<64&&!primary);
  } catch(_){fail('owner_transport_unknown');}
  finally{pumping=false;}
  if(primary||phase==='released'||phase==='closing')return;
  if(progressed&&quantum===64){wake();return;} // Progress only; never an EAGAIN retry loop.
  const mask=(receiveEnabled()&&readBlocked?1:0)|(txFrame&&writeBlocked?2:0);
  if(mask)arm(mask);
  else if(hasTX())wake();
 }
 function dispatch(frame) {
  const {kind,operation:op}=frame,body=frame.body;
  const view=new DataView(body.buffer,body.byteOffset,body.byteLength);
  if(kind===129) {
   if(phase!=='await_launch'||op!==launchOp||body.length!==0)throw fault('owner_launch_ack');
   phase='launched';const resolve=launchResolve;launchResolve=launchReject=null;resolve(Object.freeze({launched:true}));return;
  }
  if(!['launched','release_requested'].includes(phase))throw fault('owner_stage');
  if(kind===140) {
   const p=custodyPending;
   if(!p||!p.sent||op!==p.op)throw fault('owner_custody_ack');
   const n=view.getUint16(0);
   if(n>p.bytes||custodied+n>0xffffffff)throw fault('owner_custody_count');
   custodied+=n;paused=n===0;custodyPending=null;p.body=null;
   complete(p.done,Object.freeze({custodied:n}));
  } else if(kind===132) {
   if(!paused||op!==0||custodyPending||inputClosed)throw fault('owner_writable');
   paused=false;emit('writable');
  } else if(kind===133) {
   const p=endPending;
   if(!p||!p.sent||op!==p.op||body[0]!==1||view.getUint32(1)!==custodied)throw fault('owner_end_ack');
   nativeWritten=view.getUint32(1);inputClosed=true;endPending=null;
   complete(p.done,null,Object.freeze({closed:true,nativeWritten}));
  } else if(kind===134) {
   if(op!==0||stdoutEOF||body.length>creditRemaining)throw fault('owner_stdout');
   creditRemaining-=body.length;emit('stdout',body);
  } else if(kind===136) {
   if(op!==0||stderrEOF)throw fault('owner_stderr');emit('stderr',body);
  } else if(kind===138) {
   if(op!==0||body[0]>1||body[1]>1||(!body[0]&&stdoutEOF)||(!body[1]&&stderrEOF)||
     (Boolean(body[0])===stdoutEOF&&Boolean(body[1])===stderrEOF))throw fault('owner_eof');
   const first=body[0]&&!stdoutEOF;stdoutEOF=Boolean(body[0]);stderrEOF=Boolean(body[1]);
   if(first){creditRemaining=0;if(creditPending!==null&&(!txIntent||txIntent.kind!==6))creditPending=null;emit('stdoutEOF');}
  } else if(kind===135) {
   if(op!==0||bothEOF||!stdoutEOF||!stderrEOF)throw fault('owner_both_eof');bothEOF=true;
  } else if(kind===137) {
   if(op!==0||waitSeen||view.getUint32(4)!==0)throw fault('owner_wait');
   const rc=view.getInt32(0);let code=null,signal=null;
   if(rc>=0&&rc<=255)code=rc;else if(rc<0){signal=SIGNALS[-rc];if(!signal)throw fault('owner_signal_unknown');}
   else throw fault('owner_wait_code');
   waitSeen=true;waitReturncode=rc;emit('wait',Object.freeze({code,signal}));
  } else if(kind===139) {
   if(!releasePending||!releasePending.sent||op!==releasePending.op||body[0]!==1||
      !bothEOF||!waitSeen||!inputClosed||custodyPending||endPending||txFrame)throw fault('owner_release_ack');
   releaseAck=true;
  } else throw fault('owner_kind');
 }
 function finishRelease() {
  phase='closing';closeStarted=true;
  try {
   if(waitToken||txFrame||rxFrame||Object.values(slots).some(s=>s.loan!==null))throw fault('owner_release_loan');
   let bad=false;
   // Independent once-only close attempts: one failure must not skip the other Peer.
   for(const peer of [dataPeer,controlPeer]) {
    try{if(addon.cancelWait(peer)!==false)bad=true;}catch(_){bad=true;}
    try{if(addon.closePeer(peer)!==undefined)bad=true;}catch(_){bad=true;}
   }
   if(bad)throw fault('owner_close_unknown');
   phase='released';const done=releasePending.done;releasePending=null;
   complete(done,null);
  } catch(_){cleanup=cleanup||'owner_close_unknown';fail('owner_release_unknown');}
 }
 function control(which) {
  if(controlBusy||controlUnknown||phase==='bound'||phase==='launching'||phase==='await_launch'||phase==='closing'||phase==='released')throw fault('owner_control_state');
  if(controlOp===Number.MAX_SAFE_INTEGER)throw fault('owner_control_operation');
  controlBusy=true;let tx=null,rx=null,answer;const op=++controlOp;
  try {
   buffers.scalar[0]=which;
   tx=encode(controlEncode,'controlTX',201,op,scalar(1));
   const count=addon.exchange(controlPeer,tx.record,buffers.controlRX,deadline);
   if(count!==33)throw fault('owner_control_reply');
   rx=decode(controlDecode,'controlRX',count);
   if(rx.kind!==202||rx.operation!==op||rx.body[0]>1)throw fault('owner_control_reply');
   answer=rx.body[0]===1;controlAccepted=answer;
  } catch(_){controlUnknown=true;fail('owner_control_unknown');}
  finally {
   for(const frame of [rx,tx]) {
    try{if(frame)frame.release();}catch(_){controlUnknown=true;fail('owner_control_loan');}
   }
   controlBusy=false;
  }
  if(controlUnknown)throw fault(primary);
  return answer;
 }
 const owner={
  launch(tuple,callbacks) {
   if(consumed)return Promise.reject(fault('owner_launch_consumed'));
   consumed=true;
   return new Promise((resolve,reject)=>{
    launchResolve=resolve;launchReject=reject;
    try {
     if(phase!=='bound'||!callbacks||['stdout','stdoutEOF','stderr','writable','wait','error'].some(k=>typeof callbacks[k]!=='function'))throw fault('owner_events');
     events=callbacks;launch=prepareLaunch(tuple);launchOp=nextOperation();phase='launching';wake();
    } catch(_){fail('owner_launch_refused');}
   });
  },
  custody(id,body,done) {
   usable();
   if(phase!=='launched'||!Number.isSafeInteger(id)||id<=lastProxyID||!ordinaryBytes(body,512)||typeof done!=='function'||custodyPending||endPending||inputClosed||paused)throw fault('owner_custody_state');
   lastProxyID=id;custodyPending={id,op:nextOperation(),body,bytes:body.byteLength,done,sent:false};wake();
  },
  endCustody(done) {
   usable();
   if(phase!=='launched'||typeof done!=='function'||custodyPending||endPending||inputClosed||paused)throw fault('owner_end_state');
   endPending={op:nextOperation(),done,sent:false};wake();
  },
  credit(n) {
   usable();
   if(phase!=='launched'||!Number.isInteger(n)||n<1||n>512||stdoutEOF||creditPending!==null||creditRemaining)throw fault('owner_credit_state');
   creditPending=n;wake();
  },
  signal(name) {
   if(!['SIGTERM','SIGKILL'].includes(name))throw fault('owner_signal');
   if(waitSeen)return false;
   return control(name==='SIGTERM'?2:3);
  },
  abort(){return control(1);},
  stderr(body) {
   if(!ordinaryBytes(body,512))throw fault('owner_stderr');
   try{if(stderrSink(body)!==undefined)throw fault('owner_stderr_sink_result');}catch(_){fail('owner_stderr_sink');}
  },
  release(done) {
   usable();
   if(phase!=='launched'||typeof done!=='function'||releasePending||!waitSeen||!stdoutEOF||!inputClosed||custodyPending||endPending)throw fault('owner_release_state');
   releasePending={op:nextOperation(),done,sent:false};phase='release_requested';wake();
  },
  inspect() {
   return Object.freeze({phase,primary,cleanup,failureStage,custodied,nativeWritten,inputClosed,waitSeen,waitReturncode,controlAccepted,stdoutEOF,stderrEOF,bothEOF,
    releaseAck,closeStarted,waitArmed:waitToken!==null,txLoan:slots.dataTX.loan,rxLoan:slots.dataRX.loan,
    unresolvedCustody:custodyPending!==null,unresolvedEnd:endPending!==null,unresolvedRelease:releasePending!==null,
    qualification:'unqualified',storage:storageInspect()});
  }
 };
 return Object.freeze(owner);
}
module.exports=Object.freeze({allocateOwnerStorage,PLAN});

"use strict";
// Source component only. The injected owner holds the real child and transport.
const { EventEmitter } = require("node:events");
const { Writable, Readable } = require("node:stream");
const { Buffer } = require("node:buffer");
const LIMITS = Object.freeze({ offered:17000, readable:2048, ipc:2048,
  encoder:2048, framing:1432, total:24576, body:512, header:32 });
// A decoder can retain three malformed prefix bytes, each later becoming U+FFFD.
// Charge decoded output and possible slices together with the outgoing encoder.
const DECODE_EXTRA = 6, DECODER_BYTES = 4, WRITE_ENCODER = 1536;
const TEXT_BODY = Math.floor((LIMITS.encoder-WRITE_ENCODER-DECODER_BYTES-2*DECODE_EXTRA)/4);
const error = code => Object.assign(new Error(code), { code });

function createSpawner(owner) {
  return createBridge(owner,false);
}
function createCustodySpawner(owner) {
  return createBridge(owner,true);
}
function createBridge(owner,custodyMode) {
  // No permissive fallback: these functions are the external owner boundary.
  const inputMethod=custodyMode?"custody":"write", endMethod=custodyMode?"endCustody":"end";
  for (const name of ["launch",inputMethod,endMethod,"credit","signal","abort","stderr","release"])
    if (typeof owner[name] !== "function") throw error("owner_boundary_missing");
  const states = new WeakMap();
  let consumed=false;
  function spawn(options) {
    if(consumed) throw error("spawn_already_consumed");
    consumed=true;
    const proc = new EventEmitter();
    const used = { offered:0, readable:0, ipc:0, encoder:0, framing:0 };
    const peak = { ...used };
    let primary = null, cleanup = null, launched = false, launchDone = false;
    let exited = false, eof = false, killed = false, code = null, signal = null;
    let current = null, flight = null, nextID = 0, blocked = false, scheduled = false;
    let credit = 0, paused = false, finalCB = null, ending = false, inputDone = false;
    let releaseStarted = false, released = false, abortPending = false, abortSent = false;
    let abortSignal = null, spawnCharge = 0, accepted = 0, uncertain = 0;
    let custodied=0, custodyUncertain=0, nativeOutcomeUnknown=custodyMode, endReturned=false, endPending=false;
    let launchLoan = null, textOutput = false, decoderReserved = false;
    let textDecoder = null, textTail = null, stdoutEnded = false, textBusy = 0;
    const activeOffers = new Set();
    function reserve(group, count) {
      if (!Number.isSafeInteger(count) || count < 0 || used[group]+count > LIMITS[group])
        throw error("bridge_"+group+"_bound");
      used[group] += count; peak[group] = Math.max(peak[group], used[group]);
    }
    function unreserve(group, count) {
      used[group] -= count;
      if (used[group] < 0) throw error("bridge_accounting");
    }
    function failure(reason) {
      const e = error(reason && typeof reason.code==="string" && /^[a-z_]{1,96}$/.test(reason.code) ? reason.code : "owner_failure");
      if (!primary) {
        primary = e.code || "owner_failure";
        if(textTail!==null) { const charge=textTail.length*2; textTail=null; unreserve("encoder",charge); }
        proc.stdin.destroy(e); proc.stdout.destroy();
        // A pending external loan stays charged until its completion returns.
        if (!flight && current) finishWrite(e);
        if (finalCB) { const cb=finalCB; finalCB=null; cb(e); }
        queueMicrotask(() => proc.emit("error", e));
      }
      settle();
    }
    function settle() {
      if (!launchDone || !exited || !eof || flight || (custodyMode&&endPending) ||
        (!inputDone && !primary) || releaseStarted) return;
      releaseStarted = true;
      if (abortSignal) abortSignal.removeEventListener("abort", onAbort);
      try {
        owner.release(err => {
          if (released) { cleanup = cleanup || "duplicate_release_completion"; return; }
          released = true;
          if (err) { cleanup = "owner_release_failed"; proc.emit("error", error(cleanup)); }
        });
      } catch (_) { released=true; cleanup="owner_release_failed"; proc.emit("error",error(cleanup)); }
    }
    function schedule() {
      if (scheduled) return;
      scheduled=true;
      queueMicrotask(() => { scheduled=false; pump(); });
    }
    function finishWrite(err) {
      const write=current; current=null;
      if (write) {
        // finishWrite owns no external loan. Discard its remaining local chunk
        // once; pending owner loans are retained by failure until completion.
        if (write.encoded) unreserve("encoder",1536);
        write.chunk=null; write.encoded=null; write.cb(err);
      }
      settle(); schedule();
    }
    function encode(write) {
      if (typeof write.chunk !== "string") {
        // No whole-buffer copy. The owner borrows only the bounded offered view.
        return write.chunk.subarray(write.offset, Math.min(write.chunk.length,write.offset+LIMITS.body));
      }
      let end=write.offset, length=0;
      while (end < write.chunk.length) {
        const point=write.chunk.codePointAt(end);
        const units=point>0xffff ? 2 : 1;
        const bytes=point<=0x7f ? 1 : point<=0x7ff ? 2 : point<=0xffff ? 3 : 4;
        if (length+bytes > LIMITS.body) break;
        length+=bytes; end+=units;
      }
      // Max 1024-byte UTF-16 slice plus 512-byte UTF-8 allocation.
      const temporary=write.chunk.slice(write.offset,end);
      const bytes=Buffer.from(temporary,"utf8");
      write.nextOffset=end;
      return bytes;
    }
    function pump() {
      if (primary || !launched || flight || blocked) return;
      if (!current) {
        if (finalCB && !ending) {
          ending=true;
          if(custodyMode) endPending=true;
          try { owner[endMethod]((err,result) => {
            if (custodyMode?endReturned:inputDone) { failure(error("duplicate_end_completion")); return; }
            endReturned=true;endPending=false;
            if (err) { failure(error("owner_end_failed")); return; }
            if(custodyMode) {
              if(!result || Object.keys(result).sort().join(",")!=="closed,nativeWritten" ||
                result.closed!==true || !Number.isSafeInteger(result.nativeWritten) ||
                result.nativeWritten!==custodied) {
                failure(error("native_close_completion_unknown"));return;
              }
              accepted=result.nativeWritten;nativeOutcomeUnknown=false;
              if(primary) {settle();return;}
            }
            inputDone=true; const cb=finalCB; finalCB=null; if(cb) cb(); settle();
          }); } catch (_) { failure(error("owner_end_failed")); }
        }
        return;
      }
      const write=current;
      if (!write.encoded) {
        if (write.offset === write.chunk.length) { finishWrite(); return; }
        reserve("encoder",1536);
        try { write.encoded=encode(write); write.part=0; }
        catch (_) { unreserve("encoder",1536); failure(error("encode_failed")); return; }
      }
      const body=write.encoded.subarray(write.part);
      const id=++nextID;
      reserve("ipc",body.length+LIMITS.header);
      flight={id,bytes:body.length,charge:body.length+LIMITS.header,completed:false};
      const pending=flight;
      try { owner[inputMethod](id,body, result => {
        if (pending.completed) { failure(error("duplicate_write_completion")); return; }
        pending.completed=true;
        if (flight !== pending) { failure(error("late_write_completion")); return; }
        flight=null; unreserve("ipc",pending.charge);
        const n=result && result[custodyMode?"custodied":"accepted"];
        if (!result || (custodyMode?Object.keys(result).join(",")!=="custodied":Object.keys(result).length!==1) ||
          !Number.isSafeInteger(n) || n<0 || n>pending.bytes) {
          if(custodyMode) custodyUncertain+=pending.bytes;
          else uncertain+=pending.bytes;
          unreserve("encoder",1536); write.encoded=null;
          const code=custodyMode?"custody_completion_unknown":"write_completion_unknown";
          failure(error(code));
          if (current===write) finishWrite(error(primary||code));
          return;
        }
        if(custodyMode) custodied+=n;
        else accepted+=n;
        write.part+=n;
        if (write.part === write.encoded.length) {
          write.offset=typeof write.chunk === "string" ? write.nextOffset : write.offset+write.encoded.length;
          write.encoded=null; unreserve("encoder",1536);
        }
        if (primary) {
          if (write.encoded) { write.encoded=null; unreserve("encoder",1536); }
          finishWrite(error(primary)); return;
        }
        if (n===0) blocked=true;
        schedule();
      }); } catch (_) {
        // Throwing does not prove the owner stopped borrowing the body.
        failure(error("owner_write_threw_uncertain"));
      }
    }
    class Input extends Writable {
      constructor() { super({decodeStrings:false,highWaterMark:LIMITS.offered,autoDestroy:false}); }
      write(chunk, encoding, cb) {
        if (typeof encoding === "function") { cb=encoding; encoding=undefined; }
        const charge=typeof chunk === "string" ? chunk.length*2 :
          chunk instanceof Uint8Array ? chunk.byteLength : -1;
        if (encoding && !["utf8","utf-8"].includes(encoding)) throw error("unsupported_encoding");
        reserve("offered",charge); // before base Writable conversion or queue insertion
        const offer={charge}; activeOffers.add(offer);
        const done=err => {
          if(activeOffers.delete(offer)) unreserve("offered",charge);
          if(cb) cb(err);
        };
        try {
          const value=typeof chunk === "string" || Buffer.isBuffer(chunk) ? chunk :
            Buffer.from(chunk.buffer,chunk.byteOffset,chunk.byteLength);
          return super.write(value,"utf8",done);
        } catch(e) { if(activeOffers.delete(offer)) unreserve("offered",charge); throw e; }
      }
      end(chunk, encoding, cb) {
        if(typeof chunk === "function") { cb=chunk; chunk=undefined; }
        else if(typeof encoding === "function") { cb=encoding; encoding=undefined; }
        if(chunk!==undefined && chunk!==null) this.write(chunk,encoding);
        return super.end(cb);
      }
      _write(chunk, enc, cb) { current={chunk,offset:0,encoded:null,part:0,cb}; schedule(); }
      _final(cb) { finalCB=cb; schedule(); }
    }
    proc.stdin=new Input();
    proc.stdin.on("error",failure);
    proc.stdout=new Readable({highWaterMark:LIMITS.readable,read() { paused=false; scheduleCredit(); }});
    // Count explicit retained Readable bytes through the public read boundary.
    const originalRead=proc.stdout.read;
    proc.stdout.read=function(size) {
      const value=originalRead.call(this,size);
      syncReadable();
      if(value!==null) {
        if(textOutput&&!primary&&!textBusy) drainText();
        scheduleCredit();
      }
      return value;
    };
    function syncReadable() {
      used.readable=proc.stdout.readableLength*(textOutput ? 2 : 1);
      peak.readable=Math.max(peak.readable,used.readable);
      if(used.readable>LIMITS.readable) throw error("bridge_readable_bound");
    }
    function pushDecoded(text) {
      // The caller keeps this whole decoded string charged until return. Only a
      // split requires another allocation; its two slices share one reservation.
      if(!text.length) return;
      if(textTail!==null) {
        const previous=textTail.length*2, combined=previous+text.length*2;
        if(combined>2*DECODE_EXTRA) throw error("decoder_tail_bound");
        reserve("encoder",combined);
        textTail=textTail+text;
        unreserve("encoder",previous);
        return;
      }
      const fit=Math.min(text.length,Math.floor((LIMITS.readable-proc.stdout.readableLength*2)/2));
      let head=text, tail=null, slices=0;
      try {
        if(fit<text.length) {
          slices=text.length*2;reserve("encoder",slices);
          head=text.slice(0,fit);tail=text.slice(fit);
        }
        reserve("readable",head.length*2);
        if(head.length) paused=!proc.stdout.push(head,"utf8");
        if(tail!==null&&!primary) {textTail=tail;tail=null;}
      } finally {
        head=null;tail=null;
        if(slices) unreserve("encoder",slices-(textTail===null?0:textTail.length*2));
        syncReadable();
      }
    }
    function drainText() {
      if(textBusy) return;
      if(textTail!==null) {
        let held=textTail;const charge=held.length*2;textTail=null;textBusy++;
        try {pushDecoded(held);} finally {held=null;unreserve("encoder",charge);textBusy--;}
      }
      if(eof&&textTail===null&&!stdoutEnded) {
        stdoutEnded=true;proc.stdout.push(null);syncReadable();
      }
    }
    function decodeBody(body) {
      // This reservation covers the whole decoded temporary even during a
      // synchronous data/read callback. Queued text gets its separate share.
      const charge=body.byteLength*2+DECODE_EXTRA;reserve("encoder",charge);
      let text=null;textBusy++;
      try {
        text=textDecoder.write(Buffer.from(body.buffer,body.byteOffset,body.byteLength));
        if(text.length*2>charge) throw error("decoder_output_bound");
        pushDecoded(text);
      } finally {text=null;unreserve("encoder",charge);textBusy--;}
    }
    const originalEncoding=proc.stdout.setEncoding;
    proc.stdout.setEncoding=function(enc) {
      if(!["utf8","utf-8"].includes(enc) || 2*this.readableLength>LIMITS.readable || credit)
        throw error("readable_encoding_bound");
      if(textOutput) return this;
      const binary=this.readableLength;
      // Account existing raw buffers separately while Node converts its queue.
      reserve("ipc",binary);
      try {
        used.readable=0;reserve("readable",2*binary);
        reserve("encoder",DECODER_BYTES);decoderReserved=true;
        originalEncoding.call(this,enc);textOutput=true;
        // Preserve the decoder that converted queued binary data, including its
        // partial prefix. This is explicit coupling to the selected Node runtime;
        // the public SDK seam remains a Readable. Unknown implementations refuse.
        const decoder=this._readableState&&this._readableState.decoder;
        if(!decoder||typeof decoder.write!=="function"||typeof decoder.end!=="function")
          throw error("readable_decoder_unavailable");
        textDecoder=decoder;
        if(eof) {textDecoder=null;unreserve("encoder",DECODER_BYTES);decoderReserved=false;}
      } catch(e) {failure(error("readable_encoding_failed"));throw e;}
      finally {unreserve("ipc",binary);syncReadable();}
      return this;
    };
    let creditScheduled=false;
    function scheduleCredit() {
      if(creditScheduled) return;
      creditScheduled=true;
      queueMicrotask(() => {
        creditScheduled=false;
        if(primary || !launched) return;
        try {if(textOutput) drainText();} catch (_) {failure(error("text_drain_failed"));return;}
        if(eof || paused || credit || textTail!==null) return;
        syncReadable();
        const available=Math.floor((LIMITS.readable-used.readable)/(textOutput ? 2 : 1));
        if(available<=0) return;
        credit=Math.min(textOutput?TEXT_BODY:LIMITS.body,available);
        try {owner.credit(credit);} catch (_) {failure(error("owner_credit_failed"));}
      });
    }
    function incoming(body, consume) {
      if(!(body instanceof Uint8Array) || body.byteLength>LIMITS.body) { failure(error("incoming_body_bound")); return; }
      let held=false;
      try { reserve("ipc",body.byteLength+LIMITS.header); held=true; consume(); }
      catch (_) { failure(error("incoming_refused")); }
      finally { if(held) unreserve("ipc",body.byteLength+LIMITS.header); }
    }
    const events=Object.freeze({
      stdout(body) { incoming(body, () => {
        if(eof || primary || body.byteLength===0 || body.byteLength>credit) throw error("stdout_credit");
        credit-=body.byteLength;
        if(textOutput) decodeBody(body);
        else {reserve("readable",body.byteLength);paused=!proc.stdout.push(Buffer.from(body));syncReadable();}
        scheduleCredit();
      }); },
      stdoutEOF() {
        if(eof) { failure(error("duplicate_stdout_eof")); return; }
        eof=true;credit=0;
        if(textOutput&&!primary) {
          reserve("encoder",DECODE_EXTRA);let text=null;textBusy++;
          try {text=textDecoder.end();if(text.length*2>DECODE_EXTRA) throw error("decoder_output_bound");pushDecoded(text);}
          finally {text=null;unreserve("encoder",DECODE_EXTRA);textBusy--;}
        }
        textDecoder=null;
        if(decoderReserved) {unreserve("encoder",DECODER_BYTES);decoderReserved=false;}
        if(textOutput&&!primary) drainText();
        else {stdoutEnded=true;proc.stdout.push(null);syncReadable();}
        settle();
      },
      stderr(body) { incoming(body, () => owner.stderr(body)); },
      writable() { blocked=false; schedule(); },
      wait(result) {
        if(exited) { failure(error("duplicate_owned_wait")); return; }
        if(!result || Object.keys(result).sort().join(",")!=="code,signal" ||
          !(Number.isSafeInteger(result.code) && result.code>=0 && result.signal===null ||
            result.code===null && typeof result.signal==="string" && result.signal.length<=32 && /^SIG[A-Z0-9]+$/.test(result.signal))) {
          failure(error("owned_wait_unknown")); return;
        }
        exited=true; code=result.code; signal=result.signal;
        proc.emit("exit",code,signal); settle();
      },
      error() { failure(error("owner_transport_failed")); }
    });
    Object.defineProperties(proc,{killed:{get:()=>killed},exitCode:{get:()=>code},signalCode:{get:()=>signal}});
    proc.kill=function(sig) {
      if(!launched || exited || !["SIGTERM","SIGKILL"].includes(sig)) return false;
      try { const answer=owner.signal(sig); if(answer===true) { killed=true; return true; }
        if(answer!==false) failure(error("signal_acceptance_unknown"));
      } catch (_) { failure(error("owner_signal_failed")); }
      return false;
    };
    function forwardAbort() {
      if(!launched || !abortPending || abortSent || exited) return;
      abortSent=true;
      try { owner.abort(); } catch (_) { failure(error("owner_abort_failed")); }
    }
    function onAbort() { abortPending=true; forwardAbort(); }
    states.set(proc, () => ({used:{...used},peak:{...peak},accepted,uncertain,primary,cleanup,
      ...(custodyMode?{custodied,custodyUncertain,nativeOutcomeUnknown,endPending}:{}),
      launched,exited,eof,stdoutEnded,inputDone,released,killed,qualification:"unqualified"}));
    // Snapshot the borrowed tuple at the call boundary, after reservation. Only
    // the owner request/error is deferred so listeners can attach to the proxy.
    try {
      if(!options || typeof options.command!=="string" || !Array.isArray(options.args) || options.args.length>64 ||
        !options.env || typeof options.env!=="object" || (options.cwd!==undefined && typeof options.cwd!=="string"))
        throw error("spawn_tuple");
      let wire=0, raw=0;
      function text(value) {
        if(typeof value!=="string" || Buffer.byteLength(value,"utf8")>4096) throw error("spawn_text_bound");
        wire+=Buffer.byteLength(value,"utf8"); raw+=value.length*2;
      }
      text(options.command); for(const arg of options.args) text(arg);
      if(options.cwd!==undefined) text(options.cwd);
      for(const key of Object.keys(options.env)) { text(key); if(options.env[key]!==undefined) text(options.env[key]); }
      if(wire>32768) throw error("spawn_tuple_bound");
      reserve("offered",raw); spawnCharge=raw;
      launchLoan={command:options.command,args:options.args.slice(),env:{...options.env}};
      if(options.cwd!==undefined) launchLoan.cwd=options.cwd;
      abortSignal=options.signal;
      if(!abortSignal || typeof abortSignal.addEventListener!=="function") throw error("forwarded_signal_missing");
      abortSignal.addEventListener("abort",onAbort,{once:true}); abortPending=abortSignal.aborted;
      options=null;
      queueMicrotask(() => {
        try {
          const requested=owner.launch(launchLoan,events);
          if(!requested || typeof requested.then!=="function") throw error("owner_launch_completion_unknown");
          Promise.resolve(requested).then(result => {
            launchLoan=null; unreserve("offered",spawnCharge); spawnCharge=0;
            launchDone=true;
            if(!result || Object.keys(result).join(",")!=="launched" || result.launched!==true) {
              failure(error("owner_launch_completion_unknown")); return;
            }
            launched=true; forwardAbort(); schedule(); scheduleCredit(); settle();
          }, () => {
            launchLoan=null; unreserve("offered",spawnCharge); spawnCharge=0;
            launchDone=true; failure(error("owner_launch_failed"));
          });
        } catch(e) { launchDone=true; failure(e); }
      });
    } catch(e) {
      options=null; launchLoan=null;
      if(spawnCharge) { unreserve("offered",spawnCharge); spawnCharge=0; }
      launchDone=true; queueMicrotask(() => failure(e));
    }
    return proc;
  }
  return Object.freeze({spawn,inspect(proc) { const state=states.get(proc); if(!state) throw error("foreign_proxy"); return state(); }});
}
module.exports={createSpawner,createCustodySpawner,LIMITS};

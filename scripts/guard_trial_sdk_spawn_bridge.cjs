"use strict";
// Installed before authored tests. This guard is not runtime isolation evidence.
const Module=require("node:module");const path=require("node:path");
const load=Module._load;const root=__dirname;
const allowed=new Set(["node:events","node:stream","node:buffer","node:assert/strict"]);
const files=new Set(["trial_sdk_spawn_bridge.cjs","test_trial_sdk_spawn_bridge.cjs",
 "test_trial_sdk_spawn_utf8.cjs","test_trial_sdk_spawn_invalid_utf8.cjs",
 "test_trial_sdk_spawn_lifetime.cjs"].map(name=>path.join(root,name)));
Module._load=function(name,parent,main) {
 if(allowed.has(name)) return load.apply(this,arguments);
 const resolved=path.resolve(parent ? path.dirname(parent.filename) : root,name);
 if(files.has(resolved)) return load.apply(this,arguments);
 throw Error("forbidden module: "+name);
};
for(const name of ["dlopen","binding","_linkedBinding","kill","chdir"]) process[name]=()=>{throw Error("forbidden process boundary: "+name);};
for(const name of ["fetch","WebSocket"]) globalThis[name]=()=>{throw Error("forbidden network boundary");};

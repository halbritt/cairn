'use strict';
const Module=require('node:module'),path=require('node:path'),load=Module._load;
Module._load=function(name,parent,main){
 if(['node:assert/strict','node:buffer'].includes(name))return load.apply(this,arguments);
 const p=path.resolve(parent?path.dirname(parent.filename):__dirname,name);
 if(['test_trial_sdk_ipc.cjs','trial_sdk_ipc.cjs','trial_sdk_ipc_vectors.json',
  'test_trial_sdk_ipc_profile2.cjs','trial_sdk_input_profile.cjs','test_trial_sdk_input_profile.cjs']
  .some(n=>p===path.join(__dirname,n)))return load.apply(this,arguments);
 throw Error('unselected_module');
};
for(const n of ['dlopen','binding','_linkedBinding','kill','chdir'])process[n]=()=>{throw Error('unselected_effect');};
for(const n of ['fetch','WebSocket'])globalThis[n]=()=>{throw Error('unselected_effect');};

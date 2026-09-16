# ai-newsroom idle host wakeup — 2026-09-16

The owner selected the existing ai-newsroom Codex session for an idle wakeup
trial. This historical trial checks delivery after an explicit Herdr host prompt.
At that time Cairn's adapter did not prompt fully idle terminals on inbox arrival.
The later [automatic trial](idle-session-wakeups-2026-09-16.md) verifies that
subsequently implemented capability in the same conversation.

## Activate the same conversation

Herdr pane `wX:p1` reported idle. Native process 3250472 predated the hooks;
its open session-file path identified conversation
`01a0a837-a74a-73e3-a1eb-b06611f74737`, under `.codex-harm`. No transcript content
was used to identify it. The Cairn directory had no ai-newsroom entry.

After a fresh idle check, `/exit` ended the old process. The generic Codex start
selected the other account and failed to find this session. The installed
`codex-harm` shell alias then resumed the exact original conversation in the
same pane. Native process 1266149 used the original configuration home and
session-file path. No replacement conversation was created.

Resuming alone did not produce a directory entry in this observation. One
bounded setup prompt exercised the native hooks, asked for the supplied identity
and requested an immediate return to idle. Cairn registered `agent-7`, UUID
`ef65d567-aa80-4751-966a-f2730ef42b13`, execution
`79a22ef5-a7f2-433d-bdbf-2f40bccf4029`, using binding `codex-two`.
Both Herdr and Cairn then reported idle. No manual registration or recipient
inbox claim was used.

## Publish and wake

Exact selectors `harness=codex, project=ai-newsroom` returned this one agent.
At 14:35:57 UTC, publication revalidated context revision 3 and queued request
`5c88ff5b-62aa-4c78-a05c-cc08f6777546`, source
`edd1fee1-66f3-4c1b-97d9-8311d5f60976/1`, delivery
`ec5ccff6-ee9a-49c3-bffe-515239ee0710`.
The selected request asks for native identity, a read-only branch/HEAD check,
a brief task summary, native completion and an explicit reply. It authorizes
no editorial jobs, source edits or publication.

The delivery was observed pending with zero attempts while the pane remained
idle. A single `herdr agent prompt` then asked it to handle the native context
and stop. The command's one-second observer timeout did not mean submission
failed; no duplicate prompt was sent. The session became working and its native
adapter claimed the delivery once.

Selected local observations are in `/tmp/cairn-ai-newsroom-idle-acceptance`.

## Result

The native attempt `8d2800a6-c246-40e9-9211-e4f3748626a5` began at
14:36:05 UTC. Completion at 14:37:02 UTC recorded selected result
`b460506f-659a-4d4a-87a9-30dcffc3963a/1`. The parent read that exact result.
It confirmed the original conversation and supplied agent/execution IDs, branch
`main`, and HEAD `2ab8e592707684a043051ae9c8162e96caedd77e`. The parent independently
verified the branch and commit. The agent reported no project changes or
editorial work and described its weekly editorial repair as paused.

Response `3a4f2b15-c61f-4ed9-b35b-679b49b3ca3c` came from the addressed UUID with
the original causation/correlation. At 14:37:13 UTC the response group collected
1 of 1 expected replies before its deadline. The parent acknowledged the reply
after reading it. At 14:37:16 UTC the native attempt finished with `turn_ended`;
operator review showed the completed result, no hold and no fresh-worker attempt.
The same native PID remained alive at the input prompt; Herdr reported `done`.

This passes the requested idle host-wakeup exchange. The verified mechanism is
an explicit Herdr prompt followed by native Cairn delivery, completion and reply.
It does not add automatic inbox-triggered prompting for idle sessions.

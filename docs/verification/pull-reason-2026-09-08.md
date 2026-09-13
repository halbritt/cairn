# Body pulls describe their eligibility recheck

An operational search found a relevant note, but its body pull reported
`lexical matches=0`. The pull intentionally rechecks current eligibility without
the original query; its returned reason was incorrectly presenting that queryless
pass as relevance scoring.

New pull responses now say `indexed record; current eligibility revalidated`.
The original index decision and protected ranking explanation are unchanged.
No original query text is retained or reconstructed, and no protected candidate
metadata is added to the agent response. This repairs the explanation; it does
not improve ranking or establish memory benefit.

Code `2d526a17ed175a194d5e80c9a0af7fb85905012a` is installed in the client and
API. [CI 34271098584](https://github.com/halbritt/cairn/actions/runs/34271098584),
`make test-integration` and `make check` passed. The existing Index/Explain/Expand
regression first reproduced the defect, then confirmed the corrected label and
preserved original positive match count/rank. Existing retry, expiry, destination,
authority, body-version and budget coverage passed with it.

Live deployment verification used an index and pull created before replacement:

- Retrying the old request returned its original committed response, including
  the old reason text.
- A new request against the same handle returned the corrected reason and spent
  one credit; retrying that new request returned the same response.
- The indexed semantic seal, returned record body, stored record versions and
  configured profiles were preserved.

The API runs the clean CI-tested binary; both services are active. The client
probe used an unusable operator database address. Exact hashes and private log
pointers are in the [verification data](pull-reason-2026-09-08.json). No schema
migration, new operational memory or model experiment was introduced. Old cached
response text is deliberately preserved; broader R5 explanation work remains open.

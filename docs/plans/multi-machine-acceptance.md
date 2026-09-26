# Multi-machine implementation acceptance

Status: work in progress. A passing isolated transport test is not completion.
The owner authorized implementation and Archon deployment, with different model
families reviewing both plans and builds. The [proposal](multi-machine-cairn.md)
defines the intended behavior and preserved contracts.

## Work and independent review

| Work | Creator | Reviewer |
| --- | --- | --- |
| Overall proposal and integration | agent-112, Codex | agent-200, Anthropic; original proposal reviewed by agent-183, Anthropic |
| Central network API and local forwarder | agent-201, Codex | agent-200, Anthropic |
| Enrollment and operator setup | agent-200, Anthropic | agent-201, Codex |
| Native attempt and completion recovery | agent-204, Anthropic | agent-203, Codex |
| Failure harness and acceptance evidence | agent-203, Codex | agent-204, Anthropic |

Record the exact reviewed revisions and remaining findings when each review
finishes. Source inspection and test reports must identify which behavior they
establish. Store all credentials and raw runtime artifacts outside the repository.

## Completion evidence required

| Requirement | Evidence needed | Current result |
| --- | --- | --- |
| One central API/store, local service on each machine | Installed configuration and observed requests from Proximal and Archon; remote clients hold no DB credentials | Pending |
| Simple setup | One enrollment import installs required profiles/service and passes connectivity without per-agent registration secrets | Pending |
| Existing CLI/MCP and native hooks work remotely | Real agent on Archon retrieves a selected shareable note, receives work and publishes an explicit reply | Pending |
| Collection independent of checkout path | Both machines share collection identity with different workspace metadata | Pending |
| Machine and role isolation | Equal native IDs remain distinct; other-machine completion and ordinary-client observer calls refused | Pending |
| Hosted privacy and remote operation limits | Local-only body retrieval and wrong-host managed-context registration refused | Pending |
| Durable request/reply behavior | Exact source version delivered, explicit completion retained, response linked to request; task result inspected separately | Pending |
| Lost response and duplicate retry | Repeat same request identity after dropped response; one committed event/completion and no repeated task execution | Pending |
| Long network partition | Finish a task across lease expiry, recover its result through tested contract, preserve execution/hold/generation checks | Pending |
| Restarts | Surviving agent and pending completion reconciled after relay/service and central API restart without replacement execution bypass | Pending |
| Revocation and rotation | Revoked profiles refused; replacing credentials preserves intended machine/session identity | Pending |
| Restore | Disposable restored DB refuses pre-restore execution/completion; explicit recovery permits fresh authorized work | Pending |
| Local compatibility | Existing Unix API and local host adapters pass required regression gates | Pending |
| Cross-model review | Plan and code review records from opposite family, all material findings resolved | Pending |
| Build and store gates | Required local checks and disposable PostgreSQL integration tests pass on integrated revision | Pending |
| Deployment and usability | Services restart persistently, installed revision/config verified, operator setup/recovery instructions exercised | Pending |

Network interruptions and database restores run against disposable trial state.
The existing production memory store is never a test or cleanup target. Production
deployment follows successful isolated validation; retain a rollback path and
verify live non-destructive memory/request workflows after installation.

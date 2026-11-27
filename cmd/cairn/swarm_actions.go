package main

import "net/http"

// Operator actions of the swarm view (CAIRN-833). The session, CSRF, host,
// origin and same-origin checks have already passed when handleAction runs
// (swarm_serve.go). Every action is an existing store operation performed as
// the local operator principal; the page has no action that starts or stops a
// session, manages a worktree, triggers compaction or selects a model.
//
// Routes (docs/swarm-view.md holds the request and response shapes):
//
//	GET  /api/swarm/event?campaign=&event_id=            one event with its deliveries and note body
//	POST /api/swarm/action/send                          request or notice to an agent; supersede with a notice
//	POST /api/swarm/action/acknowledge                   claim then complete the operator's oldest delivery
//	GET  /api/swarm/action/cancel-preview?campaign=&delivery_id=
//	POST /api/swarm/action/cancel-delivery               one delivery of one recipient
//	POST /api/swarm/action/cancel-wait
//
// CAIRN-833 replaces this stub.
func (s *swarmServer) handleAction(w http.ResponseWriter, r *http.Request) {
	swarmRefuse(w, http.StatusNotImplemented, "FEATURE_UNAVAILABLE", "operator actions are not built yet")
}

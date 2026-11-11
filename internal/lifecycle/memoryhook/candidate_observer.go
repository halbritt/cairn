package memoryhook

import "github.com/halbritt/cairn/internal/lifecycle/value"

type candidateMeasuredText struct {
	Text      string
	WireBytes int
}

// candidateObserver belongs to the reached consumer. Existing callers omit it;
// observation cannot change policy, live values, accounting or returned data.
type candidateObserver interface {
	Route(string, []*value.Value)
	Validated(string, *value.Value)
	Rendered([]*value.Value, []*value.Value, *value.Value, int, candidateMeasuredText, int, *value.Value)
}

func candidateObserve(observers []candidateObserver, run func(candidateObserver)) {
	for _, observer := range observers {
		if observer == nil {
			continue
		}
		func() {
			defer func() {
				if recover() != nil {
					func() {
						defer func() { _ = recover() }()
						if loss, ok := observer.(interface{ ObservationLost() }); ok {
							loss.ObservationLost()
						}
					}()
				}
			}()
			run(observer)
		}()
	}
}
func candidateSnapshots(items []*value.Value) []*value.Value {
	copied := make([]*value.Value, len(items))
	for i, item := range items {
		copied[i] = clone(item)
	}
	return copied
}
func candidateRoute(observers []candidateObserver, reason string, group []*value.Value) {
	candidateObserve(observers, func(o candidateObserver) { o.Route(reason, candidateSnapshots(group)) })
}
func candidateValidated(observers []candidateObserver, extent string, response *value.Value) {
	candidateObserve(observers, func(o candidateObserver) { o.Validated(extent, clone(response)) })
}
func candidateRendered(observers []candidateObserver, entries, bodies []*value.Value, selected *value.Value, budget int, text string, measure func(string) int, remaining int, packing *value.Value) {
	candidateObserve(observers, func(o candidateObserver) {
		o.Rendered(candidateSnapshots(entries), candidateSnapshots(bodies), clone(selected), budget, candidateMeasuredText{Text: text, WireBytes: measure(text)}, remaining, clone(packing))
	})
}

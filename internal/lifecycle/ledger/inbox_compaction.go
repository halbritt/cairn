package ledger

import "github.com/halbritt/cairn/internal/lifecycle/value"

// CompactInbox folds only superseded grants; retained identity and counters are
// unchanged. The caller supplies the actual live attempt/delivery/owner sets.
func CompactInbox(state *value.Value, owners, deliveries, attempts, turns map[string]bool) int {
	grants := state.Get("grants")
	retained := value.Mapping()
	fresh := []string{}
	contains := func(set map[string]bool, v *value.Value) bool {
		return v != nil && v.Kind == value.String && set[string(v.String)]
	}
	for i, member := range grants.Members {
		grant, key := member.Value, string(member.Key)
		identity := grant.Get("identity")
		if i >= len(grants.Members)-64 || value.SameText(state.Get("active"), key) || value.Truth(identity.Get("recall_task_key")) ||
			value.Truth(grant.Get("required").Get("pending")) || contains(owners, identity.Get("native_owner")) ||
			contains(deliveries, identity.Get("delivery_id")) || contains(attempts, identity.Get("attempt_id")) {
			retained.Members = append(retained.Members, member)
			continue
		}
		fresh = append(fresh, InboxFoldedToken("key", key))
		if v := grant.Get("wake_sha256"); v != nil && v.Kind == value.String {
			fresh = append(fresh, InboxFoldedToken("wake", string(v.String)))
		}
		if v := identity.Get("native_owner"); v != nil && v.Kind == value.String {
			fresh = append(fresh, InboxFoldedToken("owner", string(v.String)))
		}
	}
	folded := len(grants.Members) - len(retained.Members)
	if folded > 0 {
		prior := state.Get("folded")
		count := integer(0)
		tokens := &value.Value{Kind: value.Array}
		if value.Truth(prior) {
			count = prior.Get("count")
			tokens.Items = append(tokens.Items, prior.Get("tokens").Items...)
		}
		seen := map[string]bool{}
		for _, token := range tokens.Items {
			seen[string(token.String)] = true
		}
		for _, token := range fresh {
			if !seen[token] {
				seen[token] = true
				tokens.Items = append(tokens.Items, value.Text(token))
			}
		}
		if len(tokens.Items) > 4096 {
			tokens.Items = tokens.Items[len(tokens.Items)-4096:]
		}
		fold := value.Mapping(value.Field("count", count), value.Field("tokens", tokens))
		AddCodexCounter(fold, "count", folded)
		state.Set("grants", retained)
		state.Set("folded", fold)
	}
	if decisions := state.Get("inbox_activation"); decisions != nil && decisions.Kind == value.Object && len(decisions.Members) > 64 {
		keep := value.Mapping()
		for i, member := range decisions.Members {
			if i >= len(decisions.Members)-64 || turns[string(member.Key)] {
				keep.Members = append(keep.Members, member)
			}
		}
		state.Set("inbox_activation", keep)
	}
	return folded
}

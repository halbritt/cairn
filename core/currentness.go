package core

import (
	"strings"
	"time"
)

// ContextPins are declared by the caller/embedding host. Matching them does not
// certify the repository's physical state; the CLI can observe Git pins locally.
type ContextPins struct {
	Revision        string `json:"revision,omitempty"`
	WorkspaceSHA256 string `json:"workspace_sha256,omitempty"`
	TaskClass       string `json:"task_class,omitempty"`
	BindingID       string `json:"binding_id,omitempty"`
	CapabilityID    string `json:"capability_id,omitempty"`
}
type Applicability struct {
	Revision        string     `json:"revision,omitempty"`
	WorkspaceSHA256 string     `json:"workspace_sha256,omitempty"`
	TaskClass       string     `json:"task_class,omitempty"`
	BindingID       string     `json:"binding_id,omitempty"`
	CapabilityID    string     `json:"capability_id,omitempty"`
	ValidFrom       *time.Time `json:"valid_from,omitempty"`
	ValidUntil      *time.Time `json:"valid_until,omitempty"`
}

func (p *ContextPins) validate() error {
	if p == nil {
		return nil
	}
	if p.Revision != "" && !revisionValid(p.Revision) {
		return failure("INVALID_REQUEST", "revision must be an immutable Git object ID")
	}
	if p.WorkspaceSHA256 != "" && !digestValid(p.WorkspaceSHA256) {
		return failure("INVALID_REQUEST", "invalid workspace digest")
	}
	for _, v := range []string{p.TaskClass, p.BindingID, p.CapabilityID} {
		if len(v) > 256 || v == "*" {
			return failure("INVALID_REQUEST", "invalid context label")
		}
	}
	return nil
}
func revisionValid(s string) bool {
	if len(s) != 40 && len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}
func (p *Applicability) validate() error {
	if p == nil {
		return nil
	}
	if err := (&ContextPins{p.Revision, p.WorkspaceSHA256, p.TaskClass, p.BindingID, p.CapabilityID}).validate(); err != nil {
		return err
	}
	if p.ValidFrom != nil && p.ValidUntil != nil && !p.ValidFrom.Before(*p.ValidUntil) {
		return failure("INVALID_REQUEST", "validity interval must be nonempty")
	}
	return nil
}
func sameApplicability(a, b *Applicability) bool {
	if a == nil || b == nil {
		return a == b
	}
	sameTime := func(x, y *time.Time) bool {
		if x == nil || y == nil {
			return x == y
		}
		return x.Equal(*y)
	}
	return a.Revision == b.Revision && a.WorkspaceSHA256 == b.WorkspaceSHA256 && a.TaskClass == b.TaskClass && a.BindingID == b.BindingID && a.CapabilityID == b.CapabilityID && sameTime(a.ValidFrom, b.ValidFrom) && sameTime(a.ValidUntil, b.ValidUntil)
}

func applicabilityReason(p *Applicability, context *ContextPins, now time.Time) string {
	if p == nil {
		return ""
	}
	if (p.ValidFrom != nil && now.Before(*p.ValidFrom)) || (p.ValidUntil != nil && !now.Before(*p.ValidUntil)) {
		return "OUTSIDE_VALIDITY"
	}
	actual := ContextPins{}
	if context != nil {
		actual = *context
	}
	for _, pair := range [][2]string{{p.Revision, actual.Revision}, {p.WorkspaceSHA256, actual.WorkspaceSHA256}, {p.TaskClass, actual.TaskClass}, {p.BindingID, actual.BindingID}, {p.CapabilityID, actual.CapabilityID}} {
		if pair[0] == "" {
			continue
		}
		if pair[1] == "" {
			return "CONTEXT_MISSING"
		}
		if pair[0] != pair[1] {
			return "CURRENTNESS_MISMATCH"
		}
	}
	return ""
}

// False means the declared applicability sets cannot intersect. Missing pins
// are unconstrained, so they do not justify suppressing a potential conflict.
func applicabilityOverlaps(a, b *Applicability) bool {
	if a == nil || b == nil {
		return true
	}
	for _, pair := range [][2]string{{a.Revision, b.Revision}, {a.WorkspaceSHA256, b.WorkspaceSHA256}, {a.TaskClass, b.TaskClass}, {a.BindingID, b.BindingID}, {a.CapabilityID, b.CapabilityID}} {
		if pair[0] != "" && pair[1] != "" && pair[0] != pair[1] {
			return false
		}
	}
	if a.ValidUntil != nil && b.ValidFrom != nil && !b.ValidFrom.Before(*a.ValidUntil) {
		return false
	}
	if b.ValidUntil != nil && a.ValidFrom != nil && !a.ValidFrom.Before(*b.ValidUntil) {
		return false
	}
	return true
}

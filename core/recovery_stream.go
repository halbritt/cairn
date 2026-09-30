package core

import (
	"context"
	"encoding/json"
	"sort"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// RecoveryExport describes a completed stream. Every emitted record is required;
// a callback or transaction failure means the consumer must discard its staging
// output. The callback must not publish a partial stream as a complete export.
type RecoveryExport struct {
	RootGrantID string `json:"root_grant_id"`
	SetID       string `json:"set_id,omitempty"`
	Count       int    `json:"count"`
}

func (s *Store) StreamRecovery(ctx context.Context, emit func(RecoveryRecord) error) (RecoveryExport, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return RecoveryExport{}, err
	}
	if emit == nil {
		return RecoveryExport{}, failure("INVALID_REQUEST", "recovery stream consumer required")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecoveryExport{}, err
	}
	defer tx.Rollback(ctx)
	staged, err := stageKnownRecovery(ctx, tx)
	if err != nil {
		return RecoveryExport{}, err
	}
	if err = staged.incomplete(ctx, func(string) error {
		return failure("INTEGRITY_FAILURE", "retained recovery segment set is incomplete; retain missing original segments before exporting")
	}); err != nil {
		return RecoveryExport{}, err
	}
	summary, err := streamStagedRecovery(ctx, staged, emit)
	if err != nil {
		return summary, err
	}
	return summary, tx.Commit(ctx)
}

func streamStagedRecovery(ctx context.Context, staged *recoveryStage, emit func(RecoveryRecord) error) (RecoveryExport, error) {
	summary := RecoveryExport{RootGrantID: staged.root}
	small, fits, err := staged.small(ctx)
	if err != nil {
		return summary, err
	}
	if fits {
		summary.Count = 1
		if err = emit(small); err != nil {
			return summary, err
		}
	} else {
		summary.SetID = uuid.NewString()
		// A first bounded pass determines the exact count in this same snapshot. No
		// list of records or descriptors is retained between passes.
		if err = staged.walk(ctx, summary.SetID, func(RecoveryRecord) error { summary.Count++; return nil }); err != nil {
			return summary, err
		}
		if summary.Count > MaxRecoverySegments {
			return summary, failure("BUDGET_REFUSED", "recovery stream exceeds the existing segment-count representation bound")
		}
		index := 0
		if err = staged.walk(ctx, summary.SetID, func(record RecoveryRecord) error {
			index++
			if summary.Count > 1 {
				record.Segment = &RecoverySegment{SetID: summary.SetID, Index: index, Count: summary.Count}
			} else {
				record.Segment = nil
			}
			var err error
			record.SHA256, err = recoveryDigest(record)
			if err != nil {
				return err
			}
			if err = record.validate(); err != nil {
				return err
			}
			return emit(record)
		}); err != nil {
			return summary, err
		}
		if index != summary.Count {
			return summary, failure("INTEGRITY_FAILURE", "recovery stream changed within its snapshot")
		}
	}
	return summary, nil
}

func (s *recoveryStage) fresh() RecoveryRecord {
	return RecoveryRecord{Schema: recoverySchema, RootGrantID: s.root, CapturedAt: s.captured, Audit: []AuditMember{}, Withdrawals: []RecoveryWithdrawal{}, Contexts: []RecoveryContext{}}
}

// small is bounded by both legacy per-list counts and one record's bytes. A
// larger union goes to walk; this compatibility decision never truncates it.
func (s *recoveryStage) small(ctx context.Context) (RecoveryRecord, bool, error) {
	record := s.fresh()
	var tooMany bool
	if err := s.tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_temp.cairn_recovery_expected WHERE kind<>'segment' GROUP BY kind HAVING count(*)>$1)`, maxRecoveryEntries).Scan(&tooMany); err != nil {
		return record, false, err
	}
	if tooMany {
		return record, false, nil
	}
	bytes := 0
	for _, kind := range []string{"audit", "withdrawal", "context"} {
		after := ""
		for {
			bodies, keys, err := s.page(ctx, kind, after, recoveryPageSize)
			if err != nil {
				return record, false, err
			}
			if len(keys) == 0 {
				break
			}
			for _, body := range bodies {
				bytes += len(body)
				if bytes > MaxRecoveryBytes {
					return s.fresh(), false, nil
				}
				switch kind {
				case "audit":
					var item AuditMember
					if err = json.Unmarshal(body, &item); err != nil {
						return record, false, err
					}
					record.Audit = append(record.Audit, item)
				case "withdrawal":
					var item RecoveryWithdrawal
					if err = json.Unmarshal(body, &item); err != nil {
						return record, false, err
					}
					record.Withdrawals = append(record.Withdrawals, item)
				case "context":
					var item RecoveryContext
					if err = json.Unmarshal(body, &item); err != nil {
						return record, false, err
					}
					record.Contexts = append(record.Contexts, item)
				}
			}
			after = keys[len(keys)-1]
		}
	}
	var err error
	record.SHA256, err = recoveryDigest(record)
	if err != nil {
		return record, false, err
	}
	if err = record.validate(); Code(err) == "BUDGET_REFUSED" {
		return s.fresh(), false, nil
	} else if err != nil {
		return record, false, err
	}
	return record, true, nil
}

func (s *recoveryStage) walk(ctx context.Context, setID string, emit func(RecoveryRecord) error) error {
	for _, kind := range []string{"audit", "context"} {
		after := ""
		limit := recoverySegmentWithdrawals
		if kind == "context" {
			limit = recoverySegmentContexts
		}
		for {
			bodies, keys, err := s.page(ctx, kind, after, limit)
			if err != nil {
				return err
			}
			if len(keys) == 0 {
				break
			}
			if err = s.emitPartition(ctx, kind, bodies, setID, emit); err != nil {
				return err
			}
			after = keys[len(keys)-1]
		}
	}
	return nil
}

func (s *recoveryStage) emitPartition(ctx context.Context, kind string, bodies []json.RawMessage, setID string, emit func(RecoveryRecord) error) error {
	record := s.fresh()
	keys := make([]string, 0, len(bodies))
	for _, body := range bodies {
		if kind == "audit" {
			var m AuditMember
			if err := json.Unmarshal(body, &m); err != nil {
				return err
			}
			record.Audit = append(record.Audit, m)
			keys = append(keys, m.EventID)
		} else {
			var c RecoveryContext
			if err := json.Unmarshal(body, &c); err != nil {
				return err
			}
			record.Contexts = append(record.Contexts, c)
			keys = append(keys, c.RecordID)
		}
	}
	query := `SELECT w.body AS withdrawal,NULL::jsonb AS audit FROM pg_temp.cairn_recovery_expected w WHERE w.kind='withdrawal' AND w.key=ANY($1::text[]) ORDER BY w.key`
	if kind == "context" {
		query = `SELECT w.body AS withdrawal,a.body AS audit FROM (SELECT DISTINCT ON(body->>'subject_id') key,body FROM pg_temp.cairn_recovery_expected WHERE kind='withdrawal' AND body->>'kind'='forget' AND body->>'subject_id'=ANY($1::text[]) ORDER BY body->>'subject_id',key) w JOIN pg_temp.cairn_recovery_expected a ON a.kind='audit' AND a.key=w.key ORDER BY w.key`
	}
	// Associated anchors may come from different individually bounded records.
	// Measure their combined bytes before transferring them to Go, so a page of
	// small audit identities cannot pull an oversized collection of withdrawals.
	var anchorBytes int64
	if err := s.tx.QueryRow(ctx, `SELECT COALESCE(sum(octet_length(withdrawal::text)+COALESCE(octet_length(audit::text),0)),0) FROM (`+query+`) anchors`, keys).Scan(&anchorBytes); err != nil {
		return err
	}
	for _, body := range bodies {
		anchorBytes += int64(len(body))
	}
	if anchorBytes > MaxRecoveryBytes {
		if len(bodies) < 2 {
			return failure("BUDGET_REFUSED", "one recovery expectation and its anchors exceed the record byte bound")
		}
		middle := len(bodies) / 2
		if err := s.emitPartition(ctx, kind, bodies[:middle], setID, emit); err != nil {
			return err
		}
		return s.emitPartition(ctx, kind, bodies[middle:], setID, emit)
	}
	rows, err := s.tx.Query(ctx, query, keys)
	if err != nil {
		return err
	}
	anchors := map[string]bool{}
	for rows.Next() {
		var w RecoveryWithdrawal
		var audit []byte
		if err = rows.Scan(&w, &audit); err != nil {
			rows.Close()
			return err
		}
		record.Withdrawals = append(record.Withdrawals, w)
		anchors[w.SubjectID] = true
		if kind == "context" {
			var m AuditMember
			if err = json.Unmarshal(audit, &m); err != nil {
				rows.Close()
				return err
			}
			record.Audit = append(record.Audit, m)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if kind == "context" {
		for _, c := range record.Contexts {
			if !anchors[c.RecordID] {
				return failure("INTEGRITY_FAILURE", "context custody lacks a forgotten-record withdrawal; no partial stream exported")
			}
		}
	}
	// Reserve the largest supported metadata position/count before deciding that
	// a partition fits. Actual emitted metadata will be no larger.
	record.Segment = &RecoverySegment{SetID: setID, Index: MaxRecoverySegments, Count: MaxRecoverySegments}
	record.SHA256, err = recoveryDigest(record)
	if err != nil {
		return err
	}
	if err = record.validate(); err != nil {
		if Code(err) != "BUDGET_REFUSED" || len(bodies) < 2 {
			return err
		}
		middle := len(bodies) / 2
		if err = s.emitPartition(ctx, kind, bodies[:middle], setID, emit); err != nil {
			return err
		}
		return s.emitPartition(ctx, kind, bodies[middle:], setID, emit)
	}
	return emit(record)
}

// InspectRecoveryStream reads one bounded source at a time and checks their full
// union in one database snapshot. Consumers must validate their bundle manifest
// and its EOF before next returns ok=false. No input stream grants resume rights.
func (s *Store) InspectRecoveryStream(ctx context.Context, next func() (RecoveryRecord, bool, error)) (RecoveryInspection, error) {
	ctx = s.recoveryContext(ctx)
	report := RecoveryInspection{Gaps: []RecoveryGap{}, Coverage: "Complete supplied recovery union in one snapshot; not source freshness, physical erasure or restore admission."}
	if err := s.checkpointAccess(); err != nil {
		return report, err
	}
	if next == nil {
		return report, failure("INVALID_REQUEST", "recovery stream source required")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return report, err
	}
	defer tx.Rollback(ctx)
	staged, err := newRecoveryStage(ctx, tx)
	if err != nil {
		return report, err
	}
	report.RootGrantID = staged.root
	count := 0
	for {
		record, ok, err := next()
		if err != nil {
			return report, err
		}
		if !ok {
			break
		}
		if err = staged.add(ctx, record); err != nil {
			return report, err
		}
		count++
	}
	if count == 0 {
		return report, failure("INVALID_REQUEST", "empty recovery input stream")
	}
	if err = staged.incomplete(ctx, func(string) error { return failure("INTEGRITY_FAILURE", "recovery segment set is incomplete") }); err != nil {
		return report, err
	}
	gaps := 0
	if err = staged.inspect(ctx, func(g RecoveryGap) error {
		if len(report.Gaps) < maxReportedDetails {
			report.Gaps = append(report.Gaps, g)
		}
		gaps++
		return nil
	}); err != nil {
		return report, err
	}
	report.AdditionalGaps = max(0, gaps-maxReportedDetails)
	report.Consistent = gaps == 0
	if err = tx.QueryRow(ctx, `SELECT count(*) FILTER(WHERE status IN ('pending','running','failed')),count(*) FILTER(WHERE status='not_possible') FROM cairn.deletion_effect`).Scan(&report.OutstandingEffects, &report.ResidualEffects); err != nil {
		return report, err
	}
	sort.Slice(report.Gaps, func(i, j int) bool { return report.Gaps[i].EventID < report.Gaps[j].EventID })
	return report, tx.Commit(ctx)
}

package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/jackc/pgx/v5"
)

// Frozen legacy algorithms are test oracles only; production never collects
// all audit metadata or serializes a corpus-sized checkpoint slice.
func checkpointDigest(members []AuditMember) (string, error) {
	body, err := json.Marshal(struct {
		Schema  string        `json:"schema"`
		Members []AuditMember `json:"members"`
	}{checkpointSchema, members})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:]), nil
}

// Only emitted governance and C/D transition events participate. B promotion,
// correction and retraction and all ordinary A history are excluded. Reasons and
// content are excluded; this detects audit metadata damage, not payload damage.
// The member set has no count ceiling: memory grows with the number of these
// events (a few hundred bytes each), never with records, evidence or receipts.
func auditMembers(ctx context.Context, tx pgx.Tx) ([]AuditMember, error) {
	rows, err := tx.Query(ctx, auditMetadataSQL+" ORDER BY e.event_id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	members := []AuditMember{}
	for rows.Next() {
		var id, metadata string
		if err = rows.Scan(&id, &metadata); err != nil {
			return nil, err
		}
		sum := sha256.Sum256([]byte(metadata))
		members = append(members, AuditMember{id, hex.EncodeToString(sum[:])})
	}
	return members, rows.Err()
}

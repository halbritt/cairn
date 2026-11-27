package core

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestTeamCampaignMetadataUpgradeFrom95(t *testing.T) {
	ctx := context.Background()
	old := admissionUpgradeFixture(t, 95)
	const repo, campaign, root = "/campaign-upgrade", "existing", "agent/root"
	if _, err := old.pool.Exec(ctx, `INSERT INTO cairn.agent_team_campaign(repo, campaign, root_agent) VALUES($1,$2,$3)`, repo, campaign, root); err != nil {
		t.Fatal(err)
	}
	if _, err := old.pool.Exec(ctx, `INSERT INTO cairn.agent_team_member(repo, campaign, member, role, assigned_by) VALUES($1,$2,$3,'root',$3)`, repo, campaign, root); err != nil {
		t.Fatal(err)
	}
	old.compatibility = DefaultCompatibilityPolicy()
	if err := old.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	var version int
	if err := old.pool.QueryRow(ctx, `SELECT max(version) FROM public.cairn_migration`).Scan(&version); err != nil || version < 96 {
		t.Fatalf("migration version = %d, err = %v", version, err)
	}
	got, err := old.GetTeamCampaign(ctx, GetTeamCampaignRequest{Repo: repo, Campaign: campaign}, Destination{Name: "hosted"})
	if err != nil || got.RootAgent != root || got.Description != "" || len(got.Members) != 1 ||
		got.Members[0].Member != root || got.Members[0].RoleLabel != "" {
		t.Fatalf("upgraded campaign = %+v, err = %v", got, err)
	}
	var description, label *string
	if err := old.pool.QueryRow(ctx, `SELECT c.description, m.role_label FROM cairn.agent_team_campaign c JOIN cairn.agent_team_member m USING (repo, campaign) WHERE c.repo=$1 AND c.campaign=$2`, repo, campaign).
		Scan(&description, &label); err != nil || description != nil || label != nil {
		t.Fatalf("existing description = %v, label = %v, err = %v", description, label, err)
	}
}

func TestTeamCampaignMetadataBounds(t *testing.T) {
	ctx := context.Background()
	const root, worker = "agent/root", "agent/worker"
	repo := "/campaign-bounds/" + uuid.NewString()
	store := testStore(t, Channel{Principal: root, Repo: repo})
	dest := Destination{Name: "hosted"}
	description := strings.Repeat("é", MaxCampaignDescriptionBytes/2)
	label := strings.Repeat("é", MaxTeamRoleLabelBytes/2)
	req := SetTeamCampaignRequest{RequestID: uuid.NewString(), Campaign: "bounds", Description: &description}
	created, err := store.SetTeamCampaign(ctx, req, dest)
	if err != nil || created.Description != description {
		t.Fatalf("set maximum-size description: %v", err)
	}
	memberReq := SetTeamMemberRequest{RequestID: uuid.NewString(), Campaign: req.Campaign, Member: worker, Lead: root, RoleLabel: &label}
	set, err := store.SetTeamMember(ctx, memberReq, dest)
	if err != nil || set.RoleLabel != label {
		t.Fatalf("set maximum-size role label: %+v, %v", set, err)
	}
	for _, value := range []string{description + "x", "nul\x00here", "\xff"} {
		req.RequestID = uuid.NewString()
		req.Description = &value
		_, err := store.SetTeamCampaign(ctx, req, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	for _, value := range []string{label + "x", "nul\x00here", "tab\there", "newline\nhere", "c1\u0085here", "line\u2028here", "paragraph\u2029here", "\xff"} {
		memberReq.RequestID = uuid.NewString()
		memberReq.RoleLabel = &value
		_, err := store.SetTeamMember(ctx, memberReq, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	requireCheckViolation := func(err error) {
		t.Helper()
		var pgErr *pgconn.PgError
		if !errors.As(err, &pgErr) || pgErr.Code != "23514" {
			t.Fatalf("expected a database check-constraint failure, got %v", err)
		}
	}
	for _, value := range []string{"", description + "x"} {
		_, err := store.pool.Exec(ctx, `UPDATE cairn.agent_team_campaign SET description=$3 WHERE repo=$1 AND campaign=$2`, repo, req.Campaign, value)
		requireCheckViolation(err)
	}
	for _, value := range []string{"", label + "x", "tab\there", "newline\nhere", "c1\u0085here", "line\u2028here", "paragraph\u2029here"} {
		_, err := store.pool.Exec(ctx, `UPDATE cairn.agent_team_member SET role_label=$4 WHERE repo=$1 AND campaign=$2 AND member=$3`, repo, req.Campaign, worker, value)
		requireCheckViolation(err)
	}
	got, err := store.GetTeamCampaign(ctx, GetTeamCampaignRequest{Campaign: req.Campaign}, dest)
	if err != nil || got.Revision != created.Revision || got.Description != description || len(got.Members) != 2 ||
		got.Members[1].Member != worker || got.Members[1].RoleLabel != label {
		t.Fatalf("invalid writes altered the stored metadata: revision %d, members %+v, err %v", got.Revision, got.Members, err)
	}
}

package main

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

func TestRecoveryDirectoryCommandsCrossCeilingAndResume(t *testing.T) {
	base := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if base == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	host, err := pgx.Connect(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	name := "cairn_bundle_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = host.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		host.Close(ctx)
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.Exec(ctx, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
		host.Close(ctx)
	})
	dsn := base + " dbname=" + name
	if strings.HasPrefix(base, "postgres://") || strings.HasPrefix(base, "postgresql://") {
		u, err := url.Parse(base)
		if err != nil {
			t.Fatal(err)
		}
		u.Path = "/" + name
		dsn = u.String()
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	invoke := func(args []string, input any) any {
		t.Helper()
		body, err := json.Marshal(input)
		if err != nil {
			t.Fatal(err)
		}
		result, err := run(ctx, args, strings.NewReader(string(body)))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		return result
	}
	invoke([]string{"migrate"}, nil)
	invoke([]string{"bootstrap"}, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Bootstrap isolated directory recovery fixture"})
	database, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { database.Close(ctx) })
	if _, err = database.Exec(ctx, `INSERT INTO cairn.authority_event(event_id,event_type,subject_id,previous_version,resulting_version,actor,basis,reason)
 SELECT md5('bundle-event:'||i)::uuid,'revoke_grant',md5('bundle-grant:'||i)::uuid,1,2,'fixture:bundle','[]'::jsonb,'Synthetic revoked grant for CLI recovery boundary' FROM generate_series(1,10001) i;
 INSERT INTO cairn.authority_grant(grant_id,principal,repo,capabilities,parent_id,depth,version,revoked,event_id)
 SELECT md5('bundle-grant:'||i)::uuid,'fixture:bundle','fixture:bundle',root.capabilities,root.grant_id,1,2,true,md5('bundle-event:'||i)::uuid FROM generate_series(1,10001) i CROSS JOIN (SELECT grant_id,capabilities FROM cairn.authority_grant WHERE parent_id IS NULL) root`); err != nil {
		t.Fatal(err)
	}
	cp := invoke([]string{"checkpoint", "--header"}, core.CheckpointRequest{RequestID: uuid.NewString(), ExportID: "bundle-cli-backup"}).(core.AuditCheckpointHeader)
	if cp.Count <= 10000 || cp.Schema != "cairn.audit-checkpoint-header/1" {
		t.Fatalf("ambiguous checkpoint header: %+v", cp)
	}
	path := filepath.Join(t.TempDir(), "recovery")
	exported := invoke([]string{"recovery-export", "--directory", path}, nil).(recoveryBundleResult)
	if exported.Count < 2 {
		t.Fatalf("fixture did not cross record ceiling: %+v", exported)
	}
	invoke([]string{"recovery-inspect", "--directory", path}, nil)
	session := invoke([]string{"begin-restore"}, core.BeginRestoreRequest{RequestID: uuid.NewString(), Target: cp.ExportID, Reason: "Begin isolated CLI recovery continuation"}).(core.RestoreSession)
	invoke([]string{"rebuild-restore"}, core.RebuildRestoreRequest{RequestID: uuid.NewString(), SessionID: session.SessionID})
	reader, err := openRecoveryBundle(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	var first core.RecoveryRecord
	for index := 1; ; index++ {
		record, ok, err := reader.Next()
		if err != nil {
			t.Fatal(err)
		}
		if !ok {
			break
		}
		if index == 1 {
			first = record
		}
		request := uuid.NewSHA1(uuid.MustParse(exported.SetID), []byte(recoveryPartName(index))).String()
		args := []string{"recovery-reapply", "--request-id", request, "--expected-sha256", record.SHA256, "--reason", "Retain original complete bundle segment", filepath.Join(path, recoveryPartName(index))}
		applied := invoke(args, nil).(core.RecoveryApplication)
		if index == 1 {
			retry := invoke(args, nil).(core.RecoveryApplication)
			if applied.ApplicationID != retry.ApplicationID {
				t.Fatal("CLI segment retry changed application")
			}
		}
	}
	if reader.manifestSHA256 != exported.ManifestSHA256 {
		t.Fatal("inspection did not consume complete manifest")
	}
	verification := core.VerifyRestoreRequest{SessionID: session.SessionID, Checkpoint: core.VerifyCheckpointRequest{CheckpointID: cp.ID, ExpectedDigest: cp.Digest, ExpectedExportID: cp.ExportID}, Recovery: first}
	result := invoke([]string{"verify-restore"}, verification).(core.RestoreVerification)
	if !result.Ready {
		t.Fatalf("complete CLI path not ready: %+v", result)
	}
	invoke([]string{"resume-restore"}, core.ResumeRestoreRequest{RequestID: uuid.NewString(), Verification: verification, Policy: "local-restore/1", Reason: "Resume after complete CLI recovery verification"})
	status := invoke([]string{"restore-status"}, nil).(core.RestoreStatus)
	if status.Paused {
		t.Fatal("successful CLI continuation remained paused")
	}
}

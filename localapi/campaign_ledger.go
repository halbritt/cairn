package localapi

import "github.com/halbritt/cairn/core"

var agentLedgerOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "ledger-item-set", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SetCampaignLedgerItem),
	bindDestination(OperationSpec{Name: "ledger-get", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).GetCampaignLedger),
	bindDestination(OperationSpec{Name: "ledger-sync-context", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SyncCampaignContext),
	bindDestination(OperationSpec{Name: "ledger-publish", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RetryClass: "idempotent"}, (*core.Store).CampaignPublish),
	bindDestination(OperationSpec{Name: "ledger-send", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "idempotent"}, (*core.Store).CampaignSend),
	bindDestination(OperationSpec{Name: "ledger-complete", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "idempotent"}, (*core.Store).CampaignComplete),
	bindDestination(OperationSpec{Name: "ledger-ack", SourceFile: "campaign_ledger.go", RemoteAllowed: true, RetryClass: "idempotent"}, (*core.Store).CampaignAck),
}

package localapi

import (
	"github.com/halbritt/cairn/core"
)

var agentWaitOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "wait-create", SourceFile: "agent_waits.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).CreateWait),
	bindDestination(OperationSpec{Name: "wait-get", SourceFile: "agent_waits.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).GetWait),
	bindDestination(OperationSpec{Name: "wait-list", SourceFile: "agent_waits.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).ListWaits),
	bindDestination(OperationSpec{Name: "wait-cancel", SourceFile: "agent_waits.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).CancelWait),
}

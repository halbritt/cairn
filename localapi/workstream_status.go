package localapi

import (
	"github.com/halbritt/cairn/core"
)

var agentStatusOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "status-set", SourceFile: "workstream_status.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SetWorkstreamStatus),
	bindDestination(OperationSpec{Name: "status-authorize", SourceFile: "workstream_status.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).AuthorizeWorkstreamStatus),
	bindDestination(OperationSpec{Name: "status-get", SourceFile: "workstream_status.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).GetWorkstreamStatus),
	bindDestination(OperationSpec{Name: "status-history", SourceFile: "workstream_status.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).WorkstreamStatusHistory),
}

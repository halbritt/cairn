package localapi

import (
	"github.com/halbritt/cairn/core"
)

var agentTeamOperations = []OperationSpec{
	bindDestination(OperationSpec{Name: "team-campaign-set", SourceFile: "team_hierarchy.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SetTeamCampaign),
	bindDestination(OperationSpec{Name: "team-member-set", SourceFile: "team_hierarchy.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).SetTeamMember),
	bindDestination(OperationSpec{Name: "team-campaign-get", SourceFile: "team_hierarchy.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).GetTeamCampaign),
	bindDestination(OperationSpec{Name: "team-campaign-list", SourceFile: "team_hierarchy.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).ListTeamCampaigns),
}

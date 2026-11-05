package tests

import (
	"testing"
)

// Standard Refusal and Refusal Error Codes defined in docs/plans/work-coordination-design.md
const (
	CodeSourceIneligible     = "SOURCE_INELIGIBLE"
	CodeUnsupportedDelivery  = "UNSUPPORTED_DELIVERY"
	CodeQueueFull            = "QUEUE_FULL"
	CodeProjectTooLarge      = "PROJECT_TOO_LARGE"
	CodeProposalExpired      = "PROPOSAL_EXPIRED"
	CodeUndoConflict         = "UNDO_CONFLICT"
	CodeSubscriptionRequired = "SUBSCRIPTION_REQUIRED"
	CodeQuotaExhausted       = "QUOTA_EXHAUSTED"
	CodeModelTimeout         = "MODEL_TIMEOUT"
	CodeInvalidProposal      = "INVALID_PROPOSAL"
	CodeVersionConflict      = "VERSION_CONFLICT"
	CodeIdempotencyConflict  = "IDEMPOTENCY_CONFLICT"
	CodeAuthorityDenied      = "AUTHORITY_DENIED"
	CodeConfigMismatch       = "CONFIG_MISMATCH"
)

// TestWorkCoordination is the overarching suite entry point invoking all 45 acceptance checks.
func TestWorkCoordination(t *testing.T) {
	t.Run("Tier1_FeatureCoverage", func(t *testing.T) {
		t.Run("AC01_ProjectIdentityDistinctByUUIDTuple", TestAC01_ProjectIdentityDistinctByUUIDTuple)
		t.Run("AC03_HistoricalExecutionIsolation", TestAC03_HistoricalExecutionIsolation)
		t.Run("AC05_LinkedRequestsDisplayStatusSeparately", TestAC05_LinkedRequestsDisplayStatusSeparately)
		t.Run("AC12_ManyArtifactLinksAndRolesIdempotent", TestAC12_ManyArtifactLinksAndRolesIdempotent)
		t.Run("AC14_ReverseImpactPaginationAndIdentitySeparation", TestAC14_ReverseImpactPaginationAndIdentitySeparation)
		t.Run("AC17_CLIAndBrowserArtifactNavigation", TestAC17_CLIAndBrowserArtifactNavigation)
		t.Run("AC18_ResponseBeforeRegistrationSatisfies", TestAC18_ResponseBeforeRegistrationSatisfies)
		t.Run("AC22_NativeRecipientDeliveryAndAck", TestAC22_NativeRecipientDeliveryAndAck)
		t.Run("AC28_ZeroIdleWakesIsolatedQueue", TestAC28_ZeroIdleWakesIsolatedQueue)
		t.Run("AC36_ReviewApplyUndoLoop", TestAC36_ReviewApplyUndoLoop)
	})

	t.Run("Tier2_BoundariesAndCorners", func(t *testing.T) {
		t.Run("AC02_RepointingPlaneInstanceRefusesRefresh", TestAC02_RepointingPlaneInstanceRefusesRefresh)
		t.Run("AC04_OperatorSeparationLocalProfileDenied", TestAC04_OperatorSeparationLocalProfileDenied)
		t.Run("AC06_FullRefreshReplacesCacheGeneration", TestAC06_FullRefreshReplacesCacheGeneration)
		t.Run("AC07_DuplicateMutationsIdempotencyAndConflicts", TestAC07_DuplicateMutationsIdempotencyAndConflicts)
		t.Run("AC08_RefreshFixturesRejectMutatingCommands", TestAC08_RefreshFixturesRejectMutatingCommands)
		t.Run("AC09_PurgeRemovesTextAndExclusionPersists", TestAC09_PurgeRemovesTextAndExclusionPersists)
		t.Run("AC10_ConcurrentPurgeWinsOverStagedMetadata", TestAC10_ConcurrentPurgeWinsOverStagedMetadata)
		t.Run("AC11_BrowserUIPresentationAndSecurity", TestAC11_BrowserUIPresentationAndSecurity)
		t.Run("AC13_RevisionImmutabilityAndUnverifiedLocators", TestAC13_RevisionImmutabilityAndUnverifiedLocators)
		t.Run("AC15_SourceMaskingAndUnlinkPurgeDistinction", TestAC15_SourceMaskingAndUnlinkPurgeDistinction)
		t.Run("AC16_MaliciousSchemesAndCredentialsRefused", TestAC16_MaliciousSchemesAndCredentialsRefused)
		t.Run("AC19_FailureReportSatisfiesWithoutTaskSuccess", TestAC19_FailureReportSatisfiesWithoutTaskSuccess)
		t.Run("AC20_TimestampOrderingDeterminism", TestAC20_TimestampOrderingDeterminism)
		t.Run("AC21_CrashRecoveryToSingleNotice", TestAC21_CrashRecoveryToSingleNotice)
		t.Run("AC23_ResumedConversationInspectCancel", TestAC23_ResumedConversationInspectCancel)
		t.Run("AC24_RestoreFencingOfWaits", TestAC24_RestoreFencingOfWaits)
		t.Run("AC26_DestinationCeilingRevalidation", TestAC26_DestinationCeilingRevalidation)
		t.Run("AC27_MaintenanceContinuityAndOverdueState", TestAC27_MaintenanceContinuityAndOverdueState)
		t.Run("AC29_OriginGatingSupportedTurnAdmits", TestAC29_OriginGatingSupportedTurnAdmits)
		t.Run("AC30_RootStabilityAndBurstLimit", TestAC30_RootStabilityAndBurstLimit)
		t.Run("AC31_ContextBudgetAndNonAcknowledgment", TestAC31_ContextBudgetAndNonAcknowledgment)
		t.Run("AC32_RaceResolutionAndQueueCap", TestAC32_RaceResolutionAndQueueCap)
		t.Run("AC33_HoldPreservationOnCrash", TestAC33_HoldPreservationOnCrash)
		t.Run("AC34_ExecutionBindingAndRestoreFencing", TestAC34_ExecutionBindingAndRestoreFencing)
		t.Run("AC35_UnsupportedBindingsRefuseAndSafeDisable", TestAC35_UnsupportedBindingsRefuseAndSafeDisable)
		t.Run("AC37_ModelOutputRejectionAndZeroMutations", TestAC37_ModelOutputRejectionAndZeroMutations)
		t.Run("AC38_HostedEligibilityAndSourcePrivacy", TestAC38_HostedEligibilityAndSourcePrivacy)
		t.Run("AC39_AtomicSubsetApplyAndConflictCheck", TestAC39_AtomicSubsetApplyAndConflictCheck)
		t.Run("AC40_BlockerCreationAndResolutionUndoLogic", TestAC40_BlockerCreationAndResolutionUndoLogic)
		t.Run("AC41_QuotaAuthFailStopZeroFallback", TestAC41_QuotaAuthFailStopZeroFallback)
		t.Run("AC42_ProcessGroupCleanupOnTimeout", TestAC42_ProcessGroupCleanupOnTimeout)
		t.Run("AC43_RetentionLimitsAndCleanupContinuity", TestAC43_RetentionLimitsAndCleanupContinuity)
	})

	t.Run("Tier3_CrossFeatureInteractions", func(t *testing.T) {
		t.Run("AC25_PersistentAdmissionFenceOnRestore", TestAC25_PersistentAdmissionFenceOnRestore)
	})

	t.Run("Tier4_RealWorldWorkloadScenarios", func(t *testing.T) {
		t.Run("AC44_EndToEndMultiFeatureUserJourney", TestAC44_EndToEndMultiFeatureUserJourney)
		t.Run("AC45_FailureInjectionAndReverseRetirement", TestAC45_FailureInjectionAndReverseRetirement)
	})
}

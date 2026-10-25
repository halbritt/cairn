"""Build a payload-free report from the live Codex/Store recovery probe."""
import hashlib
import uuid


def _uuid(value, field):
    if not isinstance(value, str):
        raise ValueError(field + " must be a UUID")
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(field + " must be a UUID") from exc
    if parsed != value:
        raise ValueError(field + " must be canonical")
    return value


def _ref(kind, value):
    return hashlib.sha256(("cairn.codex-recovery/1\0" + kind + "\0" + value).encode()).hexdigest()[:24]


def compile_report(receipt):
    if not isinstance(receipt, dict) or receipt.get("schema") != "cairn.codex-recovery-probe/1":
        raise ValueError("unsupported Codex recovery probe receipt")
    refusal, settlement = receipt.get("refusal"), receipt.get("settlement")
    if not isinstance(refusal, dict) or not isinstance(settlement, dict):
        raise ValueError("refusal and settlement observations are required")
    names = ("event_id", "delivery_id", "attempt_id", "native_turn_id")
    identity = {name: _uuid(refusal.get(name), "refusal." + name) for name in names}
    for name in names:
        if _uuid(settlement.get(name), "settlement." + name) != identity[name]:
            raise ValueError("refusal and settlement identities do not match")
    _uuid(refusal.get("request_id"), "refusal.request_id")
    _uuid(refusal.get("store_attempt_id"), "refusal.store_attempt_id")
    if (refusal.get("code") != "STALE_LEASE" or refusal.get("admission_state") != "leased" or
            refusal.get("store_attempt_id") != identity["attempt_id"] or
            refusal.get("store_attempt_native_turn_id") != identity["native_turn_id"]):
        raise ValueError("refusal is not bound to the admitted Store attempt and native turn")
    attempts = settlement.get("attempts")
    if settlement.get("state") != "handled" or type(attempts) is not int or attempts != 1:
        raise ValueError("same-attempt recovery settlement is not confirmed")
    return dict(
        schema="cairn.recovery-model-report/3",
        admission=dict(state="claimed", delivery=_ref("delivery", identity["delivery_id"]),
                       attempt=_ref("attempt", identity["attempt_id"]),
                       native_turn=_ref("native-turn", identity["native_turn_id"])),
        refusal=dict(phase="settlement", code="STALE_LEASE", state_preserved=True),
        recovery=dict(path="retry_same_attempt", state="committed"),
        settlement=dict(disposition="handled", attempts=1),
        provider_turn=dict(status="not_observed", responses=None, usage=None),
        limitations=["The turn ID is from an observed Codex loopback fixture.",
                     "Loopback fixture responses are scripted and do not establish provider work or usage.",
                     "The refusal uses a deliberately invalid lease; it is a Store fencing control, not an incident sample."])

"""Unit tests for RecoveryPolicy escalation ladder."""

import pytest

from local_control.agent.recovery import RecoveryPolicy
from local_control.core.types import VerificationResult


def make_verif(outcome: str = "failure", evidence: str = "test evidence") -> VerificationResult:
    return VerificationResult(
        outcome=outcome,  # type: ignore[arg-type]
        source=["deterministic"],
        evidence=evidence,
    )


@pytest.mark.unit
def test_recovery_ladder_step_failure_progression() -> None:
    policy = RecoveryPolicy(max_retries_per_step=2, max_failures_after_ask=1)

    # 1st failure -> retry 1
    d1 = policy.decide(make_verif("failure"), step_index=0)
    assert d1.kind == "retry_hint"
    assert "Retry attempt 1/2" in (d1.hint or "")

    # 2nd failure -> retry 2
    d2 = policy.decide(make_verif("failure"), step_index=0)
    assert d2.kind == "retry_hint"
    assert "Retry attempt 2/2" in (d2.hint or "")

    # 3rd failure -> replan
    d3 = policy.decide(make_verif("failure"), step_index=0)
    assert d3.kind == "replan"
    assert "Replan required" in (d3.hint or "")

    # 4th failure -> ask_user
    d4 = policy.decide(make_verif("failure"), step_index=0)
    assert d4.kind == "ask_user"
    assert "User intervention" in (d4.hint or "")

    # 5th failure -> abort
    d5 = policy.decide(make_verif("failure"), step_index=0)
    assert d5.kind == "abort"
    assert "Aborting" in (d5.hint or "")


@pytest.mark.unit
def test_recovery_success_resets_ladder() -> None:
    policy = RecoveryPolicy(max_retries_per_step=2)

    # 1 failure
    d1 = policy.decide(make_verif("failure"), step_index=0)
    assert d1.kind == "retry_hint"

    # Then success
    d_succ = policy.decide(make_verif("success"), step_index=0)
    assert d_succ.kind == "continue"

    # Next failure is retry 1 again!
    d2 = policy.decide(make_verif("failure"), step_index=0)
    assert d2.kind == "retry_hint"
    assert "Retry attempt 1/2" in (d2.hint or "")


@pytest.mark.unit
def test_recovery_low_confidence_twice_asks_user() -> None:
    policy = RecoveryPolicy(low_confidence_threshold=0.3)

    policy.record_proposal_confidence(0.25)
    d1 = policy.decide(make_verif("success"))
    assert d1.kind == "continue"

    # Second consecutive low confidence
    policy.record_proposal_confidence(0.20)
    d2 = policy.decide(make_verif("success"))
    assert d2.kind == "ask_user"
    assert "confidence has been below 0.30" in (d2.hint or "")


@pytest.mark.unit
def test_recovery_blocked_action_triggers_ask_user() -> None:
    policy = RecoveryPolicy()
    d = policy.decide(make_verif("failure"), blocked_action=True)
    assert d.kind == "ask_user"
    assert "blocked by safety policy" in (d.hint or "")


@pytest.mark.unit
def test_recovery_user_stopped_triggers_abort() -> None:
    policy = RecoveryPolicy()
    d = policy.decide(make_verif("failure"), user_stopped=True)
    assert d.kind == "abort"
    assert "User requested stop" in (d.hint or "")

"""The hard spending guard for World Labs.

World Labs admits requests against a low-balance threshold and bills overage at month end, so the vendor
will not stop runaway spending. This guard refuses a paid call before it is sent when:

1. its worst-case cost plus what this run already committed exceeds the per-run cap
   (WORLDLABS_MAX_CREDITS), or
2. its worst-case cost plus the project's ledger total exceeds the project cap (112,500 credits = $90, a working default under the user's ~$30 target
   decided 2026-09-29), or
3. the account balance is below its worst-case cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from wefarm.worlds.worldlabs.errors import BudgetRefusedError

PROJECT_CREDIT_CAP = 112_500
PER_RUN_CAP_ENVIRONMENT_VARIABLE = "WORLDLABS_MAX_CREDITS"


@dataclass(frozen=True)
class BudgetDecision:
    estimated_credits: float
    per_run_cap: float
    run_committed_before: float
    project_cap: float
    project_counted_before: float
    balance_before: float

    def summary(self) -> str:
        return (
            f"estimate {self.estimated_credits:g}; run {self.run_committed_before:g}/{self.per_run_cap:g}; "
            f"project {self.project_counted_before:g}/{self.project_cap:g}; balance {self.balance_before:g}"
        )


class BudgetGuard:
    def __init__(self, per_run_cap_credits: float, project_cap_credits: float = PROJECT_CREDIT_CAP) -> None:
        if per_run_cap_credits <= 0:
            raise ValueError("the per-run credit cap must be positive")
        if project_cap_credits <= 0:
            raise ValueError("the project credit cap must be positive")
        self.per_run_cap_credits = float(per_run_cap_credits)
        self.project_cap_credits = float(min(project_cap_credits, PROJECT_CREDIT_CAP))
        self.run_committed_credits = 0.0

    def check(self, estimated_credits: float, *, project_counted_credits: float, balance: float) -> BudgetDecision:
        """Raise BudgetRefusedError, or return the decision that allowed the call. Does not commit anything."""
        if estimated_credits < 0:
            raise ValueError("estimated credits cannot be negative")
        decision = BudgetDecision(
            estimated_credits=estimated_credits,
            per_run_cap=self.per_run_cap_credits,
            run_committed_before=self.run_committed_credits,
            project_cap=self.project_cap_credits,
            project_counted_before=project_counted_credits,
            balance_before=balance,
        )
        if self.run_committed_credits + estimated_credits > self.per_run_cap_credits:
            raise BudgetRefusedError(
                f"worst case {estimated_credits:g} credits would take this run to "
                f"{self.run_committed_credits + estimated_credits:g}, over the per-run cap of "
                f"{self.per_run_cap_credits:g} ({PER_RUN_CAP_ENVIRONMENT_VARIABLE})",
                estimated_credits=int(estimated_credits),
            )
        if project_counted_credits + estimated_credits > self.project_cap_credits:
            raise BudgetRefusedError(
                f"worst case {estimated_credits:g} credits would take project spending to "
                f"{project_counted_credits + estimated_credits:g}, over the project cap of {self.project_cap_credits:g}",
                estimated_credits=int(estimated_credits),
            )
        if balance < estimated_credits:
            raise BudgetRefusedError(
                f"balance {balance:g} is below the worst case {estimated_credits:g}; "
                "World Labs would bill the overage at month end",
                estimated_credits=int(estimated_credits),
            )
        return decision

    def commit(self, estimated_credits: float) -> None:
        """Count a call against this run once it has been sent (whether or not it succeeds)."""
        self.run_committed_credits += estimated_credits

    def release(self, credits_not_spent: float) -> None:
        """Give back credits of a call that definitely did not start."""
        self.run_committed_credits = max(0.0, self.run_committed_credits - credits_not_spent)

"""Print LLM spend/tokens grouped by purpose — the step-1 cost measurement."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from orchestrator.utils.llm_usage import summary


class Command(BaseCommand):
    help = "Cost/token usage by purpose (agent_loop, planner, role_router, …)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=float,
            default=24.0,
            help="Look back this many hours (default: 24).",
        )

    def handle(self, *args, **options):
        hours = float(options.get("hours") or 24.0)
        rows = summary(since_hours=hours)
        if not rows:
            self.stdout.write(f"No LLM calls logged in the last {hours:g}h.")
            return
        total_cost = sum(r["cost_usd"] or 0.0 for r in rows)
        total_calls = sum(r["calls"] for r in rows)
        self.stdout.write(
            f"{'purpose':<28} {'calls':>7} {'failed':>7} {'cost $':>10} "
            f"{'prompt tok':>12} {'compl tok':>10}"
        )
        self.stdout.write("-" * 78)
        for r in rows:
            self.stdout.write(
                f"{r['purpose']:<28} {r['calls']:>7} {r['failed']:>7} "
                f"{(r['cost_usd'] or 0.0):>10.4f} "
                f"{(r['prompt_tokens'] or 0):>12} {(r['completion_tokens'] or 0):>10}"
            )
        self.stdout.write("-" * 78)
        self.stdout.write(
            f"TOTAL over last {hours:g}h: {total_calls} call(s), ${total_cost:.4f}"
        )

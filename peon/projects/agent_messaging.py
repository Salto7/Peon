"""Django-backed ``AgentMessagingPortBase`` (swappable for A2A later)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_tz

from orchestrator.agent.messaging_base import AgentMessage as MsgDTO
from orchestrator.agent.messaging_base import AgentMessagingPortBase, MessageType
from orchestrator.utils.stream_events import envelope
from peon.projects.models import AgentMessage, Job, Objective
from peon.projects.streaming import emit_job_stream


class DjangoAgentMessaging(AgentMessagingPortBase):
    """Persist peer messages; inject via Job bridge inbox."""

    def send(
        self,
        *,
        objective_id: str,
        from_job_id: str,
        to_job_id: str,
        type: MessageType,
        body: str,
        artifact_refs: list[str] | None = None,
    ) -> MsgDTO:
        from_job = Job.objects.select_related("project", "objective").get(pk=from_job_id)
        objective = None
        if objective_id:
            objective = Objective.objects.filter(pk=objective_id).first()
        objective = objective or from_job.objective
        if objective is None:
            raise ValueError("peer messaging requires an objective on the job")
        if from_job.project_id is None:
            raise ValueError("peer messaging requires a project")

        to_job = None
        tid = (to_job_id or "").strip()
        if tid and tid not in {"*", "broadcast"}:
            to_job = Job.objects.filter(pk=tid, project_id=from_job.project_id).first()
            if to_job is None:
                raise ValueError(f"target job not found: {tid}")

        msg_type = type if type in {c.value for c in AgentMessage.MsgType} else "inform"
        row = AgentMessage.objects.create(
            project_id=from_job.project_id,
            objective=objective,
            from_job=from_job,
            to_job=to_job,
            msg_type=msg_type,
            body=(body or "")[:8000],
            artifact_refs=list(artifact_refs or [])[:32],
        )
        emit_job_stream(
            from_job,
            "log",
            f"a2a {msg_type} → {tid or 'broadcast'}: {(body or '')[:200]}",
            metadata=envelope(
                "agent_message",
                {
                    "message_id": str(row.id),
                    "msg_type": msg_type,
                    "to_job_id": tid,
                },
            ),
        )
        return self._dto(row)

    def inbox(
        self, job_id: str, *, limit: int = 20, consume: bool = True
    ) -> list[MsgDTO]:

        job = Job.objects.filter(pk=job_id).first()
        if job is None:
            return []
        with transaction.atomic():
            qs = (
                AgentMessage.objects.select_for_update()
                .filter(consumed_at__isnull=True)
                .filter(
                    # directed to this job OR broadcast on same objective
                    models_q_broadcast(job)
                )
                .exclude(from_job_id=job.id)
                .order_by("created_at")[: max(1, limit)]
            )
            rows = list(qs)
            if consume and rows:
                now = dj_tz.now()
                for r in rows:
                    r.consumed_at = now
                AgentMessage.objects.bulk_update(rows, ["consumed_at"])
        return [self._dto(r) for r in rows]

    def list_peers(self, objective_id: str, *, exclude_job_id: str = "") -> list[dict]:
        qs = Job.objects.filter(objective_id=objective_id).order_by("created_at")
        if exclude_job_id:
            qs = qs.exclude(pk=exclude_job_id)
        return [
            {
                "job_id": str(j.id),
                "title": j.title,
                "status": j.status,
                "roles": list(j.role_ids or []),
            }
            for j in qs[:40]
        ]

    @staticmethod
    def _dto(row: AgentMessage) -> MsgDTO:
        return MsgDTO(
            id=str(row.id),
            objective_id=str(row.objective_id or ""),
            from_job_id=str(row.from_job_id),
            to_job_id=str(row.to_job_id or ""),
            type=row.msg_type,  # type: ignore[arg-type]
            body=row.body or "",
            artifact_refs=tuple(row.artifact_refs or ()),
            created_at=row.created_at,
        )


def models_q_broadcast(job: Job):

    q = Q(to_job_id=job.id)
    if job.objective_id:
        q = q | Q(to_job__isnull=True, objective_id=job.objective_id)
    return q

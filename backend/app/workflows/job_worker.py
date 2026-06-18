from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import AgentArtifact, AgentJob, NotificationOutbox
from app.services import uid
from app.workflows.artifact_generators import generate_setting_batch


EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-job")
LEASE_SECONDS = 120


def enqueue_job(job_id: str) -> None:
    EXECUTOR.submit(run_job, job_id)


def run_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(AgentJob, job_id)
        if not job or job.status not in {"queued", "revision_requested"}:
            return
        job.status = "running"
        job.lease_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=LEASE_SECONDS)
        job.error = None
        db.commit()
        try:
            if job.job_type == "generate_settings":
                artifact_value = generate_setting_batch(copy.deepcopy(job.request_payload or {}))
                artifact_type = artifact_value.artifact_type
                payload = artifact_value.model_dump(mode="json")
            else:
                raise ValueError(f"unsupported job_type: {job.job_type}")
            artifact = AgentArtifact(
                id=uid("artifact"), job_id=job.id, campaign_id=job.campaign_id,
                artifact_type=artifact_type, schema_version=1, status="draft",
                payload=payload, validation_errors=[], committed_objects=[],
            )
            db.add(artifact)
            job.status = "ready_to_review"
            job.progress = {"artifact_id": artifact.id, "count": len(payload.get("settings") or [])}
            job.lease_until = None
            db.add(NotificationOutbox(
                id=uid("notice"), campaign_id=job.campaign_id,
                owner_user_id=job.owner_user_id, platform=job.platform, chat_id=job.chat_id,
                event_type="job_ready_to_review",
                payload={"job_id": job.id, "artifact_id": artifact.id,
                         "summary": f"后台生成已完成：{len(payload.get('settings') or [])} 条设定待审核。"},
                status="pending", attempts=0,
            ))
            db.commit()
        except Exception as exc:
            job = db.get(AgentJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.retry_count = (job.retry_count or 0) + 1
                job.lease_until = None
                db.add(NotificationOutbox(
                    id=uid("notice"), campaign_id=job.campaign_id,
                    owner_user_id=job.owner_user_id, platform=job.platform, chat_id=job.chat_id,
                    event_type="job_failed",
                    payload={"job_id": job.id, "summary": f"后台任务失败：{exc}"},
                    status="pending", attempts=0,
                ))
                db.commit()


def recover_jobs() -> int:
    """Requeue durable work after a process restart or expired lease."""
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        jobs = db.scalars(select(AgentJob).where(
            (AgentJob.status == "queued") |
            ((AgentJob.status == "running") & (AgentJob.lease_until < now))
        )).all()
        ids = []
        for job in jobs:
            job.status = "queued"
            job.lease_until = None
            ids.append(job.id)
        db.commit()
    for job_id in ids:
        enqueue_job(job_id)
    return len(ids)

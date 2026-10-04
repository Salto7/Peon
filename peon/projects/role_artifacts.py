"""Optional role-pack artifact hooks under ``assets/`` (``roles/<id>/`` layout)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from orchestrator.packs import resolve_resource
from peon.projects.models import Job

logger = logging.getLogger(__name__)


def ingest_role_artifacts(job: Job, workspace: Path) -> None:
    """Run ``assets/ingest_artifacts.py`` when a role pack provides one.

    Scanners that leave raw tool output should omit this hook — the analyzer
    AI role reads artifacts. Optional curating roles may write
    ``workspace/findings_queue.jsonl``.
    """
    roles_dir = Path(settings.ROLES_DIR).resolve()
    helpers = Path(settings.HELPERS_DIR).resolve()
    env = os.environ.copy()
    env["ORCHESTRATOR_WORKSPACE"] = str(workspace)
    env["JOB_WORKSPACE"] = str(workspace)
    py_path = [str(helpers)]
    prior = env.get("PYTHONPATH") or ""
    if prior:
        py_path.append(prior)
    env["PYTHONPATH"] = os.pathsep.join(py_path)

    for raw in job.role_ids or []:
        role_id = str(raw or "").strip()
        if not role_id:
            continue
        pack = roles_dir / role_id
        script = resolve_resource(pack, "assets/ingest_artifacts.py")
        if script is None:
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(workspace),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("role artifact ingest %s failed: %s", role_id, exc)
            continue
        if proc.returncode != 0:
            logger.warning(
                "role artifact ingest %s exit=%s stderr=%s",
                role_id,
                proc.returncode,
                (proc.stderr or "")[:500],
            )
        elif proc.stdout:
            logger.info("role artifact ingest %s: %s", role_id, proc.stdout.strip()[:300])

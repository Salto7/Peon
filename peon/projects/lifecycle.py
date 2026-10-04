"""Project / Job operator lifecycle: pause, resume, delete (single + bulk).

Thin façade — implementations live in lifecycle_status, lifecycle_jobs,
lifecycle_projects mixins.
"""

from __future__ import annotations

from peon.projects.lifecycle_jobs import LifecycleJobsMixin, apply_directive
from peon.projects.lifecycle_projects import LifecycleProjectsMixin
from peon.projects.lifecycle_status import LifecycleStatusMixin

__all__ = ["ProjectLifecycle", "apply_directive"]


class ProjectLifecycle(LifecycleStatusMixin, LifecycleJobsMixin, LifecycleProjectsMixin):
    """Project / Job operator lifecycle: pause, resume, delete (single + bulk)."""

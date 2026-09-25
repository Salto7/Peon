"""Isolated Learn-lab Docker — not a project sandbox."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

from orchestrator.sandbox import DockerCli, DockerSandbox, SandboxInfo, SandboxSession
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.tools.catalog.catalog import CatalogProvisioner
from orchestrator.utils.service import SharedService

LEARN_LAB_LABEL = "peon.role=learn-lab"
LEARN_LAB_FILTER = "label=peon.learn_lab=1"


class LearnLab(SharedService):
    """Minimal throwaway container for testing catalog install recipes.

    At most one lab exists: fixed name ``LEARN_LAB_CONTAINER`` (default
    ``peon-learn-lab``). ``ensure`` always recreates it so each start is a
    clean image with no leftover installs.
    """

    def image(self) -> str:
        return str(
            getattr(settings, "LEARN_LAB_IMAGE", None) or "debian:bookworm-slim"
        ).strip()

    def container_name(self) -> str:
        return str(
            getattr(settings, "LEARN_LAB_CONTAINER", None) or "peon-learn-lab"
        ).strip()

    def staging_root(self) -> Path:
        root = Path(getattr(settings, "PROJECT_WORKSPACES_DIR")).resolve().parent / "learn_lab"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _cli(self) -> DockerCli:
        return DockerCli.shared()

    def status(self) -> dict[str, Any]:
        name = self.container_name()
        image = self.image()
        cli = self._cli()
        ok, err = cli.daemon_ok()
        if not ok:
            return {
                "name": name,
                "image": image,
                "exists": False,
                "running": False,
                "docker": False,
                "error": (err or "")[:300],
            }
        exists, running = cli.inspect_running(name)
        return {
            "name": name,
            "image": image,
            "exists": exists,
            "running": running,
            "docker": True,
            "error": "",
        }

    def ensure(self) -> SandboxInfo:
        """Wipe any prior lab and create a fresh container (no leftover tools)."""
        cli = self._cli()
        if not cli.available():
            raise RuntimeError("docker CLI missing — cannot run Learn lab tests")
        name = self.container_name()
        image = self.image()
        cli.require_daemon()
        # Always recreate so apt/git_clone/pip from a previous test cannot leak.
        wiped = self.delete()
        if not wiped.get("ok"):
            raise RuntimeError(
                f"Failed to reset Learn lab {name}: {wiped.get('error') or 'unknown'}"
            )

        action = cli.ensure_running(
            name,
            image=image,
            workdir="/tmp",
            labels=[LEARN_LAB_LABEL, "peon.learn_lab=1"],
            pull_image=True,
        )
        info = SandboxInfo(
            project_id="",
            name=name,
            mode="learn-lab",
            action=action,
            image=image,
            workdir="/tmp",
            base_commands=frozenset(),
        )
        self._bind(info)
        return info

    def create(self) -> dict[str, Any]:
        """Explicit create/start for the Learn UI switch (always clean slate)."""
        info = self.ensure()
        return {
            "ok": True,
            "action": info.action,
            "lab": self.status(),
            "name": info.name,
        }

    def delete(self) -> dict[str, Any]:
        """Remove the canonical lab and any labeled duplicates."""
        name = self.container_name()
        cli = self._cli()
        try:
            cli.require_daemon()
        except RuntimeError as exc:
            return {"ok": False, "removed": False, "name": name, "error": str(exc)}

        removed_any = False
        exists, _ = cli.inspect_running(name)
        if exists:
            rm = cli.rm_force(name)
            if not rm.ok:
                return {
                    "ok": False,
                    "removed": False,
                    "name": name,
                    "error": (rm.stderr or rm.stdout or "").strip(),
                }
            removed_any = True

        for cid in cli.ps_ids(LEARN_LAB_FILTER):
            cli.rm_force(cid)
            removed_any = True

        SandboxSession.reset()
        if not removed_any:
            return {"ok": True, "removed": False, "name": name, "reason": "not found"}
        return {"ok": True, "removed": True, "name": name}

    def _bind(self, info: SandboxInfo) -> None:
        """Bind session + job workdir so exec/provision use the lab cwd."""
        from orchestrator.utils.job_env import JobEnv

        work = (info.workdir or "/tmp").strip() or "/tmp"
        os.environ["ORCHESTRATOR_SANDBOX_WORKDIR"] = work
        if JobEnv.current():
            JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": work})
        else:
            JobEnv.bind({"ORCHESTRATOR_SANDBOX_WORKDIR": work})
        SandboxSession.bind(
            DockerSandbox(info, docker_bin=self._cli().require_bin())
        )

    def connect(self) -> SandboxInfo:
        """Bind SandboxSession to the existing running lab (no create)."""
        cli = self._cli()
        if not cli.available():
            raise RuntimeError("docker CLI missing — cannot run Learn lab tests")
        name = self.container_name()
        st = self.status()
        if not st.get("running"):
            raise RuntimeError(
                f"Test docker {name!r} is not running — turn on the lab switch first."
            )
        info = SandboxInfo(
            project_id="",
            name=name,
            mode="learn-lab",
            action="reused",
            image=self.image(),
            workdir="/tmp",
            base_commands=frozenset(),
        )
        self._bind(info)
        return info

    def test_tool_install(
        self, *, yaml_text: str, install_script: str = ""
    ) -> dict[str, Any]:
        """Run catalog provision for one recipe inside the Learn lab."""
        import yaml

        text = (yaml_text or "").strip()
        if not text:
            raise ValueError("yaml is required")
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("tool YAML must be a mapping")

        tid = str(raw.get("id") or "").strip()
        if not tid or "/" in tid or ".." in tid or not tid.replace("-", "").isalnum():
            raise ValueError("tool id is required and must be a simple slug")

        catalog_dir = Path(tempfile.mkdtemp(prefix=f"{tid}-", dir=str(self.staging_root())))
        (catalog_dir / f"{tid}.yaml").write_text(text + "\n", encoding="utf-8")
        script = (install_script or "").strip()
        if script:
            (catalog_dir / f"{tid}.sh").write_text(script + "\n", encoding="utf-8")

        prev_env = os.environ.get("TOOLS_CATALOG_DIR")
        os.environ["TOOLS_CATALOG_DIR"] = str(catalog_dir)
        ToolCatalog.shared().invalidate()
        log_lines: list[str] = []
        try:
            try:
                info = self.ensure()
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "verified": False,
                    "message": str(exc),
                    "log": "",
                    "lab": self.status(),
                    "tool_id": tid,
                }
            log_lines.append(f"lab={info.name} image={info.image} action={info.action}")

            bootstrap = SandboxSession.current().exec(
                "export DEBIAN_FRONTEND=noninteractive; "
                "apt-get update -qq && "
                "apt-get install -y --no-install-recommends ca-certificates curl bash "
                ">/dev/null",
                timeout=300,
                shell=True,
            )
            if bootstrap.code != 0:
                detail = (bootstrap.stderr or bootstrap.stdout or "").strip()[:500]
                log_lines.append(f"bootstrap failed: {detail}")
                return {
                    "ok": False,
                    "verified": False,
                    "message": f"lab bootstrap failed: {detail}",
                    "log": "\n".join(log_lines),
                    "lab": self.status(),
                    "tool_id": tid,
                }

            tool = ToolCatalog.shared().by_id(tid)
            if tool is None:
                return {
                    "ok": False,
                    "verified": False,
                    "message": f"catalog did not load tool {tid!r}",
                    "log": "\n".join(log_lines),
                    "lab": self.status(),
                    "tool_id": tid,
                }

            ok, msg = CatalogProvisioner.shared().provision_binary(tool.binary or tool.id)
            log_lines.append(msg)
            # Always capture verify stdout/stderr for the Learn UI (operator judgment).
            verify = CatalogProvisioner.run_verify(tool)
            log_lines.append("--- verify ---")
            log_lines.append(str(verify.get("output") or ""))
            verified = bool(verify.get("ok"))
            return {
                "ok": bool(ok and verified),
                "verified": verified,
                "message": msg,
                "log": "\n".join(log_lines),
                "verify_output": str(verify.get("output") or ""),
                "verify_steps": verify.get("steps") or [],
                "lab": self.status(),
                "tool_id": tid,
            }
        finally:
            if prev_env is None:
                os.environ.pop("TOOLS_CATALOG_DIR", None)
            else:
                os.environ["TOOLS_CATALOG_DIR"] = prev_env
            ToolCatalog.shared().invalidate()

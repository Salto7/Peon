"""Isolated Learn-lab Docker — not a project sandbox."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

from agent_runtime.api import RuntimeSpec, Session, SessionInfo
from agent_runtime.registry import get as get_runtime
from agent_runtime.registry import register_builtin
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.tools.catalog.catalog import CatalogProvisioner
from orchestrator.utils.service import SharedServiceBase
from peon.projects.runtime_bind import bind_runtime_session, runtime_state_dir

LEARN_LAB_FILTER = "label=peon.learn_lab=1"


class LearnLab(SharedServiceBase):
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

    def _runtime(self):
        register_builtin()
        return get_runtime("sandbox")

    def _compose_network(self) -> str:
        """Attach lab to the Peon Compose network so it can reach litellm."""
        from agent_runtime.docker.cli import DockerCli
        from peon.projects.llm_proxy import LlmProxy

        return LlmProxy.shared()._compose_network(DockerCli.shared())

    def _spec(
        self,
        *,
        pull: bool = False,
        workspace_host: str = "",
        workdir: str = "/tmp",
    ) -> RuntimeSpec:
        network = ""
        try:
            network = self._compose_network()
        except Exception:
            network = str(
                getattr(settings, "LLM_PROXY_NETWORK", None) or "peon_default"
            ).strip()
        return RuntimeSpec(
            name=self.container_name(),
            image=self.image(),
            role="learn-lab",
            container_workdir=workdir,
            workspace_host=(workspace_host or "").strip(),
            labels={"peon.role": "learn-lab", "peon.learn_lab": "1"},
            network=network,
            pull_image=pull,
            state_dir=str(runtime_state_dir()),
        )

    def status(self) -> dict[str, Any]:
        return self._runtime().status(self._spec())

    def ensure(self) -> SessionInfo:
        """Wipe any prior lab and create a fresh container (no leftover tools)."""
        name = self.container_name()
        wiped = self.delete()
        if not wiped.get("ok"):
            raise RuntimeError(
                f"Failed to reset Learn lab {name}: {wiped.get('error') or 'unknown'}"
            )
        spec = self._spec(pull=True)
        session = self._runtime().provision(spec)
        self._bind(session)
        return session.info

    def ensure_for_authoring(self, *, workspace_host: str = "") -> SessionInfo:
        """Ensure a lab with the staging root mounted for OpenCode drafts.

        Reuses a running authoring lab when possible so OpenCode is not
        reinstalled on every suggest/write. Install-test ``ensure()`` still
        wipes to a clean image.
        """
        host = (workspace_host or "").strip() or str(self.staging_root())
        Path(host).mkdir(parents=True, exist_ok=True)
        st = self.status()
        spec = self._spec(
            pull=not st.get("exists"),
            workspace_host=host,
            workdir="/workspace",
        )
        if st.get("running"):
            # Remount requires recreate when the prior lab had no workspace bind.
            session = self._runtime().attach(spec)
            # Verify workspace mount exists; if missing, recreate.
            probe = session.exec(
                ["test", "-d", "/workspace"], timeout=20, shell=False
            )
            if probe.ok:
                self._bind(session)
                return session.info
            self.delete()
        elif st.get("exists"):
            self.delete()
        session = self._runtime().provision(spec)
        self._bind(session)
        return session.info

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
        runtime = self._runtime()
        spec = self._spec()
        st = runtime.status(spec)
        if st.get("error") and not st.get("docker"):
            return {"ok": False, "removed": False, "name": name, "error": st.get("error") or ""}

        removed_any = False
        result = runtime.destroy(spec)
        if not result.get("ok") and result.get("error"):
            return {
                "ok": False,
                "removed": False,
                "name": name,
                "error": str(result.get("error") or ""),
            }
        removed_any = bool(result.get("removed"))
        for cid in runtime.find(LEARN_LAB_FILTER):
            runtime.destroy(RuntimeSpec(name=cid, role="learn-lab", image=self.image()))
            removed_any = True

        Session.reset()
        if not removed_any:
            return {"ok": True, "removed": False, "name": name, "reason": "not found"}
        return {"ok": True, "removed": True, "name": name}

    def _bind(self, session) -> None:
        """Bind session + job workdir so exec/provision use the lab cwd."""
        bind_runtime_session(session, default_workdir="/tmp")

    def connect(self) -> SessionInfo:
        """Bind the session to the existing running lab (no create)."""
        name = self.container_name()
        st = self.status()
        if not st.get("running"):
            raise RuntimeError(
                f"Test docker {name!r} is not running — turn on the lab switch first."
            )
        session = self._runtime().attach(self._spec())
        self._bind(session)
        return session.info

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

            bootstrap = Session.current().exec(
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

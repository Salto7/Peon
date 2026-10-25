"""One throwaway Docker container, never more than one — generic lab
infrastructure, not tied to any one feature.

Toolsmith is the current (only) consumer: install-tests for drafted catalog
tools, and — now that OpenCode is baked into its own image
(``Docker/sandbox-lab/``) rather than fetched at authoring time — running
OpenCode for its code-provisioning drafts. Both uses share the same single
container; there is no separate "authoring" lab.

Lifecycle, exposed to the operator as three actions (Toolsmith UI):
``setup`` (ensure running, reuse as-is), ``decommission`` (stop + remove),
``reset`` (decommission then setup — a guaranteed fresh container). Toolsmith
actions that need a *known-clean* state (an install test; an OpenCode task)
call the internal ``_ensure_clean`` instead, which always wipes first —
that guarantee is unrelated to what the operator last clicked.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from agent_runtime.api import RuntimeSession, RuntimeSpec, Session, SessionInfo
from agent_runtime.docker.engine import DockerEngine
from agent_runtime.registry import RuntimeRegistry
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.tools.catalog.catalog import CatalogProvisioner
from orchestrator.utils.service import SharedServiceBase
from orchestrator.utils.strings import is_kebab
from peon.projects.llm_proxy import LlmProxy
from peon.projects.sandbox import bind_runtime_session, runtime_state_dir


class Lab(SharedServiceBase):
    """The one lab container. Toolsmith (install tests + OpenCode authoring)
    is its only user today; nothing here is Toolsmith-specific."""

    def image(self) -> str:
        return str(getattr(settings, "LAB_IMAGE", None) or "peon-sandbox-lab:local").strip()

    def container_name(self) -> str:
        return str(getattr(settings, "LAB_CONTAINER", None) or "peon-lab").strip()

    def staging_root(self) -> Path:
        root = Path(settings.PLATFORM_WORKSPACE_DIR).resolve() / "lab"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _runtime(self):
        registry = RuntimeRegistry.shared()
        registry.register_builtin()
        return registry.get("sandbox")

    def _compose_network(self) -> str:
        """Attach the lab to the Peon Compose network so it can reach litellm."""

        return LlmProxy.shared()._compose_network(DockerEngine.shared())

    def _spec(self) -> RuntimeSpec:
        try:
            network = self._compose_network()
        except Exception:
            network = str(getattr(settings, "LLM_PROXY_NETWORK", None) or "peon_default").strip()
        return RuntimeSpec(
            name=self.container_name(),
            image=self.image(),
            role="lab",
            container_workdir="/workspace",
            workspace_host=str(self.staging_root()),
            labels={"peon.role": "lab"},
            network=network,
            state_dir=str(runtime_state_dir()),
        )

    def status(self) -> dict[str, Any]:
        return self._runtime().status(self._spec())

    def setup(self) -> dict[str, Any]:
        """Ensure the lab is running on the current image. Reuses it as-is
        when it already matches (``DockerRuntime.provision``'s own
        fingerprint check — image, volumes, network); recreates it when it
        doesn't, e.g. a container left over from an older ``LAB_IMAGE``.
        Never wipes a container that's already right.
        """
        spec = self._spec()
        session = self._runtime().provision(spec)
        self._bind(session)
        return {"ok": True, "action": session.info.action, "lab": self.status(), "name": spec.name}

    def decommission(self) -> dict[str, Any]:
        """Stop and remove the lab. Nothing replaces it."""
        spec = self._spec()
        runtime = self._runtime()
        st = runtime.status(spec)
        if st.get("error") and not st.get("docker"):
            return {"ok": False, "removed": False, "name": spec.name, "error": st.get("error") or ""}
        result = runtime.destroy(spec)
        if not result.get("ok") and result.get("error"):
            return {
                "ok": False,
                "removed": False,
                "name": spec.name,
                "error": str(result.get("error") or ""),
            }
        Session.reset()
        removed = bool(result.get("removed"))
        if not removed:
            return {"ok": True, "removed": False, "name": spec.name, "reason": "not found"}
        return {"ok": True, "removed": True, "name": spec.name}

    def reset(self) -> dict[str, Any]:
        """Decommission + setup in one step. Guarantees exactly one, clean lab."""
        info = self._ensure_clean()
        return {"ok": True, "action": "reset", "lab": self.status(), "name": info.name}

    def _ensure_clean(self) -> SessionInfo:
        """Wipe and recreate — the guarantee install tests and OpenCode tasks need.

        Independent of ``setup``/``decommission``/``reset``: a Toolsmith
        action that needs to *know* the lab has no leftover state from a
        previous draft always takes this path, regardless of what the
        operator last clicked in the lab lifecycle controls.
        """
        return self._provision_clean(self._spec())

    def _provision_clean(self, spec: RuntimeSpec) -> SessionInfo:
        result = self._runtime().destroy(spec)
        if not result.get("ok"):
            raise RuntimeError(
                f"Failed to reset lab {spec.name}: {result.get('error') or 'unknown'}"
            )
        Session.reset()
        session = self._runtime().provision(spec)
        self._bind(session)
        return session.info

    def _bind(self, session) -> None:
        """Bind session + job workdir so exec/provision use the lab cwd."""
        bind_runtime_session(session, default_workdir=session.info.workdir or "/workspace")

    def connect(self) -> RuntimeSession:
        """Bind the session to the lab, which must already be running."""
        spec = self._spec()
        st = self._runtime().status(spec)
        if not st.get("running"):
            raise RuntimeError(f"Lab {spec.name!r} is not running.")
        session = self._runtime().attach(spec)
        self._bind(session)
        return session

    def test_tool_install(self, *, yaml_text: str, install_script: str = "") -> dict[str, Any]:
        """Run catalog provision for one recipe inside a freshly-reset lab."""

        text = (yaml_text or "").strip()
        if not text:
            raise ValueError("yaml is required")
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("tool YAML must be a mapping")

        tid = str(raw.get("id") or "").strip().lower()
        if not is_kebab(tid):
            raise ValueError("tool id is required and must be a simple slug")

        with tempfile.TemporaryDirectory(prefix=f"{tid}-", dir=str(self.staging_root())) as tmp:
            catalog_dir = Path(tmp)
            (catalog_dir / f"{tid}.yaml").write_text(text + "\n", encoding="utf-8")
            script = (install_script or "").strip()
            if script:
                (catalog_dir / f"{tid}.sh").write_text(script + "\n", encoding="utf-8")
            catalog = ToolCatalog(root=catalog_dir)
            log_lines: list[str] = []
            try:
                info = self._ensure_clean()
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

            tool = catalog.by_id(tid)
            if tool is None:
                return {
                    "ok": False,
                    "verified": False,
                    "message": f"catalog did not load tool {tid!r}",
                    "log": "\n".join(log_lines),
                    "lab": self.status(),
                    "tool_id": tid,
                }

            ok, msg = CatalogProvisioner(catalog=catalog).provision_binary(tool.binary or tool.id)
            log_lines.append(msg)
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

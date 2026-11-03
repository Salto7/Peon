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

import contextlib
import fcntl
import tempfile
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from agent_runtime.api import SandboxSession, SandboxSpec, Session, SessionInfo, TerminalHandle
from agent_runtime.docker.engine import DockerClient
from agent_runtime.registry import SandboxDriverRegistry
from orchestrator.tools.catalog import ToolCatalog
from orchestrator.tools.catalog.catalog import CatalogProvisioner
from orchestrator.utils.service import SharedServiceBase
from orchestrator.utils.strings import is_kebab
from peon.projects.llm_proxy import LlmProxy
from peon.projects.sandbox import bind_runtime_session, runtime_state_dir


class LabService(SharedServiceBase):
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
        registry = SandboxDriverRegistry.shared()
        registry.register_builtin()
        return registry.get("sandbox")

    def _compose_network(self) -> str:
        """Attach the lab to the Peon Compose network so it can reach litellm."""

        return LlmProxy.shared()._compose_network(DockerClient.shared())

    def _spec(self) -> SandboxSpec:
        try:
            network = self._compose_network()
        except Exception:
            network = str(getattr(settings, "LLM_PROXY_NETWORK", None) or "peon_default").strip()
        return SandboxSpec(
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

    @contextlib.contextmanager
    def _exclusive(self, *, force: bool = False):
        """Cross-process guard around lab lifecycle ops.

        ``setup``/``decommission``/``reset`` run from the worker (Dramatiq
        task) while ``test_tool_install`` runs synchronously from the web
        process — a plain ``threading.Lock`` would only cover one process.
        An operator's Reset click racing a running install test otherwise
        silently kills that test's container mid-install (``docker rm -f``
        doesn't care that something is mid-``apt-get`` in it) — it then
        fails with a confusing "container not running" at verify, not a
        "the lab was reset under you" error. A flock on the lab's own state
        dir (shared by both processes via the workspaces volume) serializes
        every destructive/exclusive op across both.

        ``force=True`` is the operator's explicit override (confirmed in the
        UI first) — it does not try to steal or break the other side's OS
        lock, it just skips taking one itself and acts on the container
        directly. Docker does not care that something else is mid-install;
        the other side simply fails its next step with a clear "lab is
        gone" error instead of hanging forever on a stuck lab.
        """
        if force:
            yield
            return
        lock_path = runtime_state_dir() / "lab.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError(
                    "Lab is busy with another start/reboot/reset/test — "
                    "wait for it to finish, or force this action."
                ) from None
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def setup(self, *, force: bool = False) -> dict[str, Any]:
        """Ensure the lab is running on the current image. Reuses it as-is
        when it already matches (``DockerSandboxDriver.provision``'s own
        fingerprint check — image, volumes, network); recreates it when it
        doesn't, e.g. a container left over from an older ``LAB_IMAGE``.
        Never wipes a container that's already right.
        """
        spec = self._spec()
        with self._exclusive(force=force):
            session = self._runtime().provision(spec)
            self._bind(session)
        return {"ok": True, "action": session.info.action, "lab": self.status(), "name": spec.name}

    def reboot(self, *, force: bool = False) -> dict[str, Any]:
        """Restart the lab container in place — no wipe, just unstick a
        container that's up but misbehaving (a hung install, a dead shell).
        """
        spec = self._spec()
        with self._exclusive(force=force):
            result = self._runtime().restart(spec)
            if not result.get("ok"):
                return {"ok": False, "name": spec.name, "error": result.get("error") or ""}
            Session.reset()
        return {"ok": True, "action": "rebooted", "lab": self.status(), "name": spec.name}

    def decommission(self, *, force: bool = False) -> dict[str, Any]:
        """Stop and remove the lab. Nothing replaces it."""
        spec = self._spec()
        runtime = self._runtime()
        st = runtime.status(spec)
        if st.get("error") and not st.get("docker"):
            return {"ok": False, "removed": False, "name": spec.name, "error": st.get("error") or ""}
        with self._exclusive(force=force):
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

    def reset(self, *, force: bool = False) -> dict[str, Any]:
        """Decommission + setup in one step. Guarantees exactly one, clean lab."""
        with self._exclusive(force=force):
            info = self._provision_clean(self._spec())
        return {"ok": True, "action": "reset", "lab": self.status(), "name": info.name}

    def _ensure_clean(self) -> SessionInfo:
        """Wipe and recreate — the guarantee install tests and OpenCode tasks need.

        Independent of ``setup``/``decommission``/``reset``: a Toolsmith
        action that needs to *know* the lab has no leftover state from a
        previous draft always takes this path, regardless of what the
        operator last clicked in the lab lifecycle controls.

        Not itself lock-guarded — every caller uses the fresh container for
        more than just this call (an install test's provision+verify; an
        OpenCode run's whole session), so *they* hold ``_exclusive()`` for
        their full duration. Locking here too would self-deadlock (``flock``
        does not nest across separate ``open()`` calls, even same-process).
        """
        return self._provision_clean(self._spec())

    def _provision_clean(self, spec: SandboxSpec) -> SessionInfo:
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

    def connect(self) -> SandboxSession:
        """Bind the session to the lab, which must already be running."""
        spec = self._spec()
        st = self._runtime().status(spec)
        if not st.get("running"):
            raise RuntimeError(f"Lab {spec.name!r} is not running.")
        session = self._runtime().attach(spec)
        self._bind(session)
        return session

    def open_terminal(self, *, cols: int = 80, rows: int = 24) -> TerminalHandle:
        """Interactive PTY into the lab container, for troubleshooting a failed
        install test by hand. The lab must already be running (Setup first)."""
        spec = self._spec()
        st = self._runtime().status(spec)
        if not st.get("running"):
            raise RuntimeError(f"Lab {spec.name!r} is not running — Setup it first.")
        return self._runtime().open_terminal(spec, cols=cols, rows=rows)

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
                # Held for the whole provision+verify, not just the reset —
                # a concurrent Setup/Decommission/Reset click (or another
                # test) must not pull the container out from under this run.
                with self._exclusive():
                    info = self._ensure_clean()
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

                    ok, msg = CatalogProvisioner(catalog=catalog).provision_binary(
                        tool.binary or tool.id
                    )
                    log_lines.append(msg)
                    verify = CatalogProvisioner.run_verify(tool)
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "verified": False,
                    "message": str(exc),
                    "log": "\n".join(log_lines),
                    "lab": self.status(),
                    "tool_id": tid,
                }
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

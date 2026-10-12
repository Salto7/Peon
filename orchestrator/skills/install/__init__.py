"""Install Claude Agent Skills packs. Load stays on the provider."""

from orchestrator.skills.install.dragdrop import (
    install_directory,
    install_relative_files,
    install_zip,
)
from orchestrator.skills.install.openskills import install_openskills, pack_dirs

__all__ = [
    "install_directory",
    "install_openskills",
    "install_relative_files",
    "install_zip",
    "pack_dirs",
]

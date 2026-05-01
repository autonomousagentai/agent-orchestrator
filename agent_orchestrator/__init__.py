"""agent-orchestrator: drive a Claude agent fleet against a backlog file.

Top-level imports are lazy so that submodules without the `claude-agent-sdk`
or `PyGithub` extras can be used standalone (e.g. unit tests of the backlog
parser).
"""

__version__ = "0.1.0"
__all__ = ["Config", "Orchestrator"]


def __getattr__(name: str):
    if name == "Config":
        from .config import Config

        return Config
    if name == "Orchestrator":
        from .orchestrator import Orchestrator

        return Orchestrator
    raise AttributeError(name)

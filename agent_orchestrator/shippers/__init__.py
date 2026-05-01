from .base import ShipResult, Shipper
from .directory import DirectoryShipper
from .noop import NoopShipper

__all__ = ["DirectoryShipper", "NoopShipper", "ShipResult", "Shipper"]


def __getattr__(name: str):
    # GitPRShipper depends on PyGithub; load lazily so users without the
    # `git` extra don't need it installed.
    if name == "GitPRShipper":
        from .git_pr import GitPRShipper as _impl

        return _impl
    raise AttributeError(name)

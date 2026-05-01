"""Thin wrapper around PyGithub for the bits the orchestrator actually needs:
open a PR, check CI on a PR's head SHA, squash-merge it, label/comment.

Imported lazily by `GitPRShipper` so users on the no-git path don't need
PyGithub installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from github.PullRequest import PullRequest
    from github.Repository import Repository

log = logging.getLogger(__name__)


@dataclass
class CIStatus:
    overall: str  # 'success', 'pending', 'failure', 'unknown'
    checks: list[tuple[str, str]]


class GitHubClient:
    def __init__(self, token: str, repo: str) -> None:
        from github import Github  # local import keeps PyGithub optional

        self._gh = Github(token)
        self._repo: Repository = self._gh.get_repo(repo)
        self.repo_full_name = repo

    # ---------------------------------------------------------------- PRs

    def open_pr(self, branch: str, base_branch: str, title: str, body: str) -> "PullRequest":
        existing = self.find_open_pr_for_branch(branch)
        if existing:
            log.info("PR for %s already exists: #%d", branch, existing.number)
            return existing
        pr = self._repo.create_pull(
            title=title, body=body, head=branch, base=base_branch, draft=False
        )
        log.info("opened PR #%d: %s", pr.number, title)
        return pr

    def find_open_pr_for_branch(self, branch: str) -> Optional["PullRequest"]:
        owner = self.repo_full_name.split("/")[0]
        head = f"{owner}:{branch}"
        for pr in self._repo.get_pulls(state="open", head=head):
            return pr
        return None

    def get_pr(self, number: int) -> "PullRequest":
        return self._repo.get_pull(number)

    def open_feature_slugs(self, prefix: str = "feature/") -> list[str]:
        slugs: list[str] = []
        for pr in self._repo.get_pulls(state="open"):
            ref = pr.head.ref or ""
            if ref.startswith(prefix):
                slug = ref[len(prefix):].strip()
                if slug:
                    slugs.append(slug)
        return slugs

    # ----------------------------------------------------------- CI status

    def ci_status(
        self, pr: "PullRequest", required_workflow: Optional[str] = None
    ) -> CIStatus:
        sha = pr.head.sha
        commit = self._repo.get_commit(sha)
        runs = list(commit.get_check_runs())
        checks = [(r.name, _check_state(r)) for r in runs]
        if not runs:
            return CIStatus(overall="pending", checks=checks)
        if required_workflow:
            for name, state in checks:
                if name == required_workflow:
                    return CIStatus(overall=state, checks=checks)
            return CIStatus(overall="pending", checks=checks)
        states = [s for _, s in checks]
        if any(s == "failure" for s in states):
            return CIStatus(overall="failure", checks=checks)
        if any(s == "pending" for s in states):
            return CIStatus(overall="pending", checks=checks)
        if all(s == "success" for s in states):
            return CIStatus(overall="success", checks=checks)
        return CIStatus(overall="unknown", checks=checks)

    # -------------------------------------------------------------- merge

    def squash_merge(self, pr: "PullRequest", commit_title: Optional[str] = None) -> bool:
        if pr.merged:
            return True
        if not pr.mergeable:
            log.warning(
                "PR #%d not mergeable yet (mergeable_state=%s)",
                pr.number,
                pr.mergeable_state,
            )
            return False
        result = pr.merge(merge_method="squash", commit_title=commit_title or pr.title)
        return bool(getattr(result, "merged", False))

    # ----------------------------------------------------- labels / comments

    def add_label(self, pr: "PullRequest", label: str) -> None:
        if not label:
            return
        try:
            pr.add_to_labels(label)
        except Exception as e:  # pragma: no cover
            log.warning("could not label PR #%d with %s: %s", pr.number, label, e)

    def comment(self, pr: "PullRequest", body: str) -> None:
        try:
            pr.create_issue_comment(body)
        except Exception as e:  # pragma: no cover
            log.warning("could not comment on PR #%d: %s", pr.number, e)


def _check_state(run) -> str:
    status = (run.status or "").lower()
    conclusion = (run.conclusion or "").lower()
    if status != "completed":
        return "pending"
    if conclusion in ("success", "neutral", "skipped"):
        return "success"
    if conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        return "failure"
    return "unknown"

"""
Phase 2: context triage.

Decides which files are worth fetching full content for, per rating
subagent, BEFORE any subagent runs. Does not fetch file content itself
(other than the README, already returned in full by get_readme) and does
not build the coordinator or subagents — see PHASE2_BRIEF.pdf.

Two locked decisions this module implements:
  - Selection is fixed deterministic rules, not an LLM call.
  - Each subagent (architecture / results_functionality / design_docs)
    gets its own tailored selection, not one shared list.

Token estimation is approximate (bytes // 4, per the brief's suggested
"chars / 4" heuristic) since triage works off file sizes from the tree
listing, not fetched content — getting real content would defeat the
point of triaging first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.github_client import GitHubAPIError, GitHubClient

DEFAULT_TOKEN_BUDGET = 50_000  # tunable, per subagent; not a file-count cap
MAX_FETCHABLE_FILE_SIZE = 1_000_000  # GitHub Contents API inline limit (see github_client.py)

IGNORED_DIR_SEGMENTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "vendor", ".idea", ".mypy_cache", ".pytest_cache",
    ".tox", "target", "bin", "obj", "site-packages",
}
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".avi",
    ".pyc", ".class", ".jar", ".wasm", ".lock",
}
ARCHITECTURE_CONFIG_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "tsconfig.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "Makefile", "CMakeLists.txt", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
}
ROOT_ENTRY_NAMES = {"__init__.py", "index.js", "index.ts"}
ENTRY_POINT_NAMES = {
    "main.py", "app.py", "manage.py", "cli.py", "__main__.py",
    "index.js", "index.ts", "server.js", "server.ts", "main.go", "main.rs",
}
MANIFEST_NAMES = {
    "requirements.txt", "pyproject.toml", "package.json", "Pipfile",
    "Cargo.toml", "go.mod", "go.sum", "pom.xml", "build.gradle", "Gemfile",
    "composer.json",
}
DOC_DIR_NAMES = {"docs", "doc", "documentation", "wiki"}


@dataclass
class RepoMap:
    owner: str
    repo: str
    default_branch: str
    primary_language: str | None
    metadata: dict[str, Any]
    entries: list[dict[str, Any]]  # blob entries only (files, not dirs)
    readme_path: str | None
    readme_content: str | None
    file_count: int
    total_size_bytes: int
    extension_breakdown: dict[str, int]
    max_depth: int
    has_tests: bool
    has_ci: bool
    has_docs: bool


@dataclass
class FileSelection:
    path: str
    reason: str
    estimated_tokens: int


@dataclass
class SelectionPlan:
    subagent: str
    token_budget: int
    files: list[FileSelection] = field(default_factory=list)

    @property
    def estimated_tokens(self) -> int:
        return sum(f.estimated_tokens for f in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subagent": self.subagent,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "files": [
                {"path": f.path, "reason": f.reason, "estimated_tokens": f.estimated_tokens}
                for f in self.files
            ],
        }


def _extension(path: str) -> str: #Splits by /, then grabs last one. Splits by ., then grabs extension
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def _is_ignored(path: str) -> bool: #Checks if a file is to be ignored or not
    segments = path.split("/")[:-1]
    return any(seg in IGNORED_DIR_SEGMENTS for seg in segments)


def _is_binary(path: str) -> bool: #Checks if a file is binary or not
    return _extension(path) in BINARY_EXTENSIONS


def _is_test_path(path: str) -> bool: #Checks if this file is a test file or not
    lower = path.lower()
    segments = lower.split("/")
    name = segments[-1]
    if any(seg in ("test", "tests", "__tests__", "spec") for seg in segments[:-1]):
        return True
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _estimate_tokens(size_bytes: int) -> int:
    return max(1, size_bytes // 4)


def _fetchable(entry: dict[str, Any]) -> bool: #Checks if we can even fetch the file
    size = entry.get("size") or 0
    return size <= MAX_FETCHABLE_FILE_SIZE and not _is_ignored(entry["path"]) and not _is_binary(entry["path"])


def _group_by_top_dir(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        top = e["path"].split("/", 1)[0] if "/" in e["path"] else "(root)"
        groups.setdefault(top, []).append(e) #Sorts the different files into separate value lists by top dir as key
    for g in groups.values(): 
        g.sort(key=lambda e: e["path"]) #Sorts alphabetically so that files are sorted the same way everytime (makes it easier to debug, etc.)
    return groups


def _breadth_spread( #This function determines how exactly we're going to spend our budget and how we're going to spread it across the different files and dirs
    entries: list[dict[str, Any]],
    exclude: set[str],
    budget_remaining: int,
    reason: str,
) -> list[FileSelection]:
    """Round-robin one file per top-level directory so the selection covers
    the repo's structure instead of just its largest files."""
    candidates = [e for e in entries if e["path"] not in exclude and _fetchable(e)]
    groups = _group_by_top_dir(candidates)
    dir_names = sorted(groups)
    cursors = {d: 0 for d in dir_names} #How many files have we taken from each directory? 
    selections: list[FileSelection] = []
    budget = budget_remaining
    progress = True
    while budget > 0 and progress:
        progress = False
        for d in dir_names:
            idx = cursors[d]
            files = groups[d]
            if idx >= len(files):
                continue
            entry = files[idx]
            cursors[d] += 1
            cost = _estimate_tokens(entry.get("size") or 0)
            if cost > budget:
                continue
            selections.append(FileSelection(entry["path"], reason, cost))
            exclude.add(entry["path"])
            budget -= cost
            progress = True
            if budget <= 0:
                break
    return selections


async def build_repo_map(client: GitHubClient, owner: str, repo: str) -> RepoMap:
    metadata = await client.get_repo_metadata(owner, repo)
    default_branch = metadata["default_branch"]
    tree = await client.list_repo_tree(owner, repo, ref=default_branch)

    try:
        readme = await client.get_readme(owner, repo, ref=default_branch)
        readme_path: str | None = readme["path"]
        readme_content: str | None = readme["content"]
    except GitHubAPIError:
        readme_path, readme_content = None, None

    blobs = [e for e in tree["entries"] if e["type"] == "blob"] #Makes sure we only get blobs (files) and not trees (directories)

    extension_breakdown: dict[str, int] = {}
    total_size = 0
    max_depth = 0
    has_tests = False
    has_ci = False
    has_docs = False
    for e in blobs:
        path = e["path"]
        size = e.get("size") or 0
        total_size += size
        max_depth = max(max_depth, path.count("/"))
        ext = _extension(path) 
        extension_breakdown[ext] = extension_breakdown.get(ext, 0) + 1
        if not has_tests and _is_test_path(path):
            has_tests = True
        if not has_ci and path.lower().startswith(".github/workflows/"):
            has_ci = True
        if not has_docs and path.split("/", 1)[0].lower() in DOC_DIR_NAMES:
            has_docs = True

    return RepoMap(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        primary_language=metadata.get("primary_language"),
        metadata=metadata,
        entries=blobs,
        readme_path=readme_path,
        readme_content=readme_content,
        file_count=len(blobs),
        total_size_bytes=total_size,
        extension_breakdown=extension_breakdown,
        max_depth=max_depth,
        has_tests=has_tests,
        has_ci=has_ci,
        has_docs=has_docs,
    )


def select_architecture_files(repo_map: RepoMap, budget: int = DEFAULT_TOKEN_BUDGET) -> SelectionPlan:
    plan = SelectionPlan(subagent="architecture", token_budget=budget)
    selected: set[str] = set()

    for e in repo_map.entries:
        path = e["path"]
        if not _fetchable(e):
            continue
        name = path.rsplit("/", 1)[-1]
        is_root_config = "/" not in path and name in ARCHITECTURE_CONFIG_NAMES
        is_root_entry = name in ROOT_ENTRY_NAMES and path.count("/") <= 1
        if not (is_root_config or is_root_entry):
            continue
        cost = _estimate_tokens(e.get("size") or 0)
        if cost > budget - plan.estimated_tokens:
            continue
        reason = "top-level config file" if is_root_config else "root entry/init file"
        plan.files.append(FileSelection(path, reason, cost))
        selected.add(path)

    remaining = budget - plan.estimated_tokens
    if remaining > 0:
        plan.files.extend(
            _breadth_spread(
                repo_map.entries, selected, remaining,
                "representative sample for structural breadth",
            )
        )
    return plan


def _try_add_file(
    entry: dict[str, Any],
    reason: str,
    plan: SelectionPlan,
    selected: set[str],
    budget: int,
) -> None:
    path = entry["path"]
    if path in selected or not _fetchable(entry):
        return
    cost = _estimate_tokens(entry.get("size") or 0)
    if cost > budget - plan.estimated_tokens:
        return
    plan.files.append(FileSelection(path, reason, cost))
    selected.add(path)


def select_results_files(repo_map: RepoMap, budget: int = DEFAULT_TOKEN_BUDGET) -> SelectionPlan:
    plan = SelectionPlan(subagent="results_functionality", token_budget=budget)
    selected: set[str] = set()

    for e in repo_map.entries:
        name = e["path"].rsplit("/", 1)[-1]
        if name in ENTRY_POINT_NAMES:
            _try_add_file(e, "entry point", plan, selected, budget)
        elif name in MANIFEST_NAMES:
            _try_add_file(e, "manifest/dependency file", plan, selected, budget)
        elif e["path"].lower().startswith(".github/workflows/"):
            _try_add_file(e, "CI config", plan, selected, budget)

    for e in sorted(
        (e for e in repo_map.entries if _is_test_path(e["path"])),
        key=lambda e: e["path"],
    ):
        _try_add_file(e, "test file", plan, selected, budget)

    remaining = budget - plan.estimated_tokens
    if remaining > 0:
        # No static import graph available without fetching content first
        # (that's what triage decides, not what it does) — approximate
        # "files entry points depend on" with a breadth-representative
        # spread of the remaining source tree instead.
        plan.files.extend(
            _breadth_spread(
                repo_map.entries, selected, remaining,
                "additional source file (breadth fallback, no import graph)",
            )
        )
    return plan


def select_docs_files(repo_map: RepoMap, budget: int = DEFAULT_TOKEN_BUDGET) -> SelectionPlan:
    plan = SelectionPlan(subagent="design_docs", token_budget=budget)
    selected: set[str] = set()

    if repo_map.readme_path is not None and repo_map.readme_content is not None:
        cost = _estimate_tokens(len(repo_map.readme_content))
        plan.files.append(FileSelection(repo_map.readme_path, "root README", cost))
        selected.add(repo_map.readme_path)

    for e in repo_map.entries:
        path = e["path"]
        if "/" in path or not _fetchable(e):
            continue
        name = path.rsplit("/", 1)[-1].upper()
        if name.startswith("LICENSE"):
            reason = "license file"
        elif name.startswith("CONTRIBUTING"):
            reason = "contributing guide"
        else:
            continue
        cost = _estimate_tokens(e.get("size") or 0)
        if cost > budget - plan.estimated_tokens:
            continue
        plan.files.append(FileSelection(path, reason, cost))
        selected.add(path)

    remaining = budget - plan.estimated_tokens
    if remaining > 0:
        doc_entries = sorted(
            (
                e for e in repo_map.entries
                if e["path"].split("/", 1)[0].lower() in DOC_DIR_NAMES and _fetchable(e)
            ),
            key=lambda e: e["path"],
        )
        for e in doc_entries:
            if e["path"] in selected:
                continue
            cost = _estimate_tokens(e.get("size") or 0)
            if cost > remaining:
                continue
            plan.files.append(FileSelection(e["path"], "docs folder content", cost))
            selected.add(e["path"])
            remaining -= cost

    return plan


async def triage_repo(
    client: GitHubClient, owner: str, repo: str, budget: int = DEFAULT_TOKEN_BUDGET
) -> dict[str, SelectionPlan]:
    """Build the shared repo map once, then run all three per-subagent
    selections off it. This is what Phase 4's coordinator will call."""
    repo_map = await build_repo_map(client, owner, repo)
    return {
        "architecture": select_architecture_files(repo_map, budget),
        "results_functionality": select_results_files(repo_map, budget),
        "design_docs": select_docs_files(repo_map, budget),
    }


if __name__ == "__main__":
    import asyncio
    import json
    import sys

    from server import config

    async def _main() -> None:
        if len(sys.argv) != 3:
            print("Usage: python -m server.triage <owner> <repo>", file=sys.stderr)
            raise SystemExit(1)
        owner, repo = sys.argv[1], sys.argv[2]
        config.validate()
        client = GitHubClient(config.GITHUB_TOKEN)
        try:
            plans = await triage_repo(client, owner, repo)
        finally:
            await client.aclose()
        print(json.dumps({name: plan.to_dict() for name, plan in plans.items()}, indent=2))

    asyncio.run(_main())

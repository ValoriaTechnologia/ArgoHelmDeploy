#!/usr/bin/env python3
"""Update ArgoCD Application spec.source.targetRevision for a Helm chart."""

import os
import sys
import tempfile
import subprocess
from pathlib import Path

import yaml


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def get_input(name: str, default: str | None = None, *, required: bool = False) -> str:
    key = f"INPUT_{name.upper().replace('-', '_')}"
    val = os.environ.get(key)
    if val is None or val == "":
        if required and default is None:
            raise ValueError(f"Missing required input: {name} (env {key})")
        return default or ""
    return val


def build_auth_url(repo_url: str, token: str) -> str:
    normalized = repo_url.strip()
    if normalized.startswith("git@github.com:"):
        normalized = normalized.replace("git@github.com:", "https://github.com/")
    if not normalized.endswith(".git"):
        normalized = normalized.rstrip("/") + ".git"
    if not normalized.startswith("https://"):
        return repo_url
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(normalized)
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def resolve_application_path(workdir: str, package_path: str, chart_name: str | None) -> tuple[str, dict]:
    resolved = Path(workdir) / package_path
    resolved = resolved.resolve()
    if not resolved.exists():
        fail(f"Path does not exist: {resolved}")
    if resolved.is_dir():
        fail(f"Path must be a file (Application manifest), not a directory: {resolved}")
    if not resolved.is_file():
        fail(f"Path {resolved} is not a file.")
    content = resolved.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)
    if not doc or doc.get("kind") != "Application":
        fail(f"File {resolved} is not an ArgoCD Application manifest.")
    return (str(resolved), doc)


def build_source_selector(
    pkg_source: dict | None,
    chart_name: str | None,
) -> dict[str, str]:
    """Merge packages[].source with optional chart_name input (chart_name overrides chart)."""
    selector: dict[str, str] = {}
    if isinstance(pkg_source, dict):
        for key in ("chart", "repoURL", "path"):
            val = pkg_source.get(key)
            if val is not None and str(val).strip() != "":
                selector[key] = str(val).strip()
    if chart_name:
        selector["chart"] = chart_name
    return selector


def source_matches(source: dict, selector: dict[str, str]) -> bool:
    """Return True if source matches all selector fields (AND)."""
    if not selector:
        return True
    for key, expected in selector.items():
        if source.get(key) != expected:
            return False
    return True


def format_selector(selector: dict[str, str]) -> str:
    if not selector:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in selector.items())


def update_target_revision(doc: dict, version: str, selector: dict[str, str] | None = None) -> None:
    selector = selector or {}
    spec = doc.get("spec") or {}
    source = spec.get("source")
    sources = spec.get("sources")

    if sources and isinstance(sources, list):
        candidates = [s for s in sources if s and isinstance(s, dict)]
        if len(candidates) > 1 and not selector:
            fail(
                "Application has multiple spec.sources; declare packages[].source "
                "(chart, repoURL, and/or path) or pass chart_name to select one."
            )
        matches = [s for s in candidates if source_matches(s, selector)]
        if not matches:
            fail(f"No spec.sources entry matches selector: {format_selector(selector)}")
        if len(matches) > 1:
            fail(
                f"Multiple spec.sources entries match selector: {format_selector(selector)}; "
                "narrow packages[].source."
            )
        matches[0]["targetRevision"] = version
        return

    if not source:
        fail("Application manifest has no spec.source (or spec.sources).")
    if selector and not source_matches(source, selector):
        fail(
            f'spec.source does not match selector: {format_selector(selector)} '
            f'(got chart={source.get("chart")!r}, repoURL={source.get("repoURL")!r}, '
            f'path={source.get("path")!r}).'
        )
    source["targetRevision"] = version


def run_git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def main() -> None:
    repo_url = get_input("repo-url", required=True).strip()
    token = get_input("token", required=True).strip()
    package_file_path = get_input("package-file-path", required=True).strip()
    package_name = get_input("package-name", required=True).strip()
    version = get_input("version", required=True).strip()
    chart_name = (get_input("chart-name", default="").strip() or None)
    branch = (get_input("branch", default="main").strip() or "main")
    environment = get_input("environment", default="").strip()

    if token:
        print(f"::add-mask::{token}", flush=True)

    workdir = tempfile.mkdtemp(prefix="argocd-helm-")
    auth_url = build_auth_url(repo_url, token)

    print("Cloning repository...")
    clone_cwd = os.path.dirname(workdir) or "."
    run_git(
        ["clone", "--branch", branch, "--single-branch", "--depth", "1", auth_url, workdir],
        cwd=clone_cwd,
    )

    package_file_full = Path(workdir) / package_file_path
    if not package_file_full.exists():
        fail(f"Package file not found: {package_file_full}")

    package_content = package_file_full.read_text(encoding="utf-8")
    package_doc = yaml.safe_load(package_content)
    if not package_doc or not isinstance(package_doc.get("packages"), list):
        fail('Package file must contain a top-level "packages" array.')

    pkg = None
    for p in package_doc["packages"]:
        if p and p.get("name") == package_name:
            pkg = p
            break
    if not pkg:
        print(f'Package "{package_name}" not found in {package_file_path}; skipping.')
        return

    pkg_bootstrap = pkg.get("bootstrap") or False
    if pkg_bootstrap:
        print("Bootstrap package found, skipping.")
        return

    pkg_path = pkg.get("path") or "./"
    if "$" in pkg_path:
        if not environment:
            fail("Package path contains $; the environment input is required.")
        pkg_path = pkg_path.replace("$", environment)

    pkg_source = pkg.get("source")
    if pkg_source is not None and not isinstance(pkg_source, dict):
        fail('Package "source" must be a mapping (chart, repoURL, and/or path).')
    selector = build_source_selector(pkg_source, chart_name)

    app_path, app_doc = resolve_application_path(workdir, pkg_path, chart_name)
    update_target_revision(app_doc, version, selector)
    with open(app_path, "w", encoding="utf-8") as f:
        yaml.dump(app_doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    rel_path = Path(app_path).relative_to(workdir)
    print(f"Updated targetRevision to {version} in {rel_path}")

    run_git(["config", "user.name", "github-actions[bot]"], cwd=workdir)
    run_git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=workdir)
    run_git(["add", str(rel_path)], cwd=workdir)

    commit_msg = f"chore(helm): update {package_name} to {version}"
    commit_result = run_git(
        ["commit", "-m", commit_msg],
        cwd=workdir,
        check=False,
    )
    if commit_result.returncode != 0:
        print("No changes to commit (targetRevision already set to this version).")
        return

    run_git(["push", "origin", branch], cwd=workdir)
    print("Pushed changes successfully.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        if e.stdout:
            print(e.stdout, file=sys.stdout)
        sys.exit(e.returncode)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

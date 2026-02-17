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


def update_target_revision(doc: dict, version: str, chart_name: str | None) -> None:
    spec = doc.get("spec") or {}
    source = spec.get("source")
    sources = spec.get("sources")

    if sources and isinstance(sources, list):
        target = None
        if chart_name:
            for s in sources:
                if s and s.get("chart") == chart_name:
                    target = s
                    break
        if target is None:
            target = sources[0] if sources else None
        if not target:
            fail(f'Chart "{chart_name}" not found in spec.sources.')
        target["targetRevision"] = version
        return

    if not source:
        fail("Application manifest has no spec.source (or spec.sources).")
    if chart_name and source.get("chart") != chart_name:
        fail(f'Chart in spec.source is "{source.get("chart")}", not "{chart_name}".')
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
    multi_raw = get_input("multi", default="").strip().lower()
    multi = multi_raw in ("true", "1", "yes")
    environments_str = get_input("environments", default="").strip()

    if multi:
        if not environments_str:
            raise ValueError("When multi is set, environments (comma-separated list) is required.")
        environments = [e.strip() for e in environments_str.split(",") if e.strip()]
        if not environments:
            raise ValueError("environments must contain at least one environment name.")

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

    pkg_path = pkg.get("path") or "./"
    if multi:
        if "$" not in pkg_path:
            fail("In multi mode, package path must contain $ as placeholder for the environment name.")
        targets: list[tuple[str, dict]] = []
        for env in environments:
            path_for_env = pkg_path.replace("$", env)
            app_path, app_doc = resolve_application_path(workdir, path_for_env, chart_name)
            targets.append((app_path, app_doc))
    else:
        app_path, app_doc = resolve_application_path(workdir, pkg_path, chart_name)
        targets = [(app_path, app_doc)]

    updated_paths: list[str] = []
    for app_path, app_doc in targets:
        update_target_revision(app_doc, version, chart_name)
        with open(app_path, "w", encoding="utf-8") as f:
            yaml.dump(app_doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        rel_path = Path(app_path).relative_to(workdir)
        print(f"Updated targetRevision to {version} in {rel_path}")
        updated_paths.append(app_path)

    run_git(["config", "user.name", "github-actions[bot]"], cwd=workdir)
    run_git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=workdir)
    for app_path in updated_paths:
        rel_path = Path(app_path).relative_to(workdir)
        run_git(["add", str(rel_path)], cwd=workdir)

    commit_msg = (
        f"chore(helm): update {package_name} to {version} (envs: {','.join(environments)})"
        if multi
        else f"chore(helm): update {package_name} to {version}"
    )
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

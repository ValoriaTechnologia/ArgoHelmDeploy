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


def find_application_in_dir(dir_path: str, chart_name: str | None) -> tuple[str, dict] | None:
    candidates = []
    path = Path(dir_path)
    for f in path.iterdir():
        if not f.is_file():
            continue
        name_lower = f.name.lower()
        if not (name_lower.endswith(".yaml") or name_lower.endswith(".yml")):
            continue
        try:
            content = f.read_text(encoding="utf-8")
            doc = yaml.safe_load(content)
        except Exception:
            continue
        if not doc or doc.get("kind") != "Application":
            continue
        spec = doc.get("spec") or {}
        source = spec.get("source")
        sources = spec.get("sources")
        if chart_name:
            def match(s: dict) -> bool:
                return s and s.get("chart") == chart_name

            if not (source and match(source)) and not (sources and isinstance(sources, list) and any(match(s) for s in sources)):
                continue
        candidates.append((str(f), doc))
    if not candidates:
        return None
    if len(candidates) > 1 and chart_name:
        for app_path, doc in candidates:
            spec = doc.get("spec") or {}
            if spec.get("source", {}).get("chart") == chart_name:
                return (app_path, doc)
            for s in (spec.get("sources") or []):
                if s.get("chart") == chart_name:
                    return (app_path, doc)
    return candidates[0]


def resolve_application_path(workdir: str, package_path: str, chart_name: str | None) -> tuple[str, dict]:
    resolved = Path(workdir) / package_path
    resolved = resolved.resolve()
    if not resolved.exists():
        fail(f"Path does not exist: {resolved}")
    if resolved.is_file():
        content = resolved.read_text(encoding="utf-8")
        doc = yaml.safe_load(content)
        if not doc or doc.get("kind") != "Application":
            fail(f"File {resolved} is not an ArgoCD Application manifest.")
        return (str(resolved), doc)
    if resolved.is_dir():
        found = find_application_in_dir(str(resolved), chart_name)
        if not found:
            suffix = f' with chart "{chart_name}"' if chart_name else ""
            fail(f"No ArgoCD Application found in directory {resolved}{suffix}.")
        return found
    fail(f"Path {resolved} is neither a file nor a directory.")


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
    repo_url = (os.environ.get("REPO_URL") or "").strip()
    token = (os.environ.get("TOKEN") or "").strip()
    package_file_path = (os.environ.get("PACKAGE_FILE_PATH") or "").strip()
    package_name = (os.environ.get("PACKAGE_NAME") or "").strip()
    version = (os.environ.get("VERSION") or "").strip()
    chart_name = (os.environ.get("CHART_NAME") or "").strip() or None
    branch = (os.environ.get("BRANCH") or "main").strip() or "main"

    if not all([repo_url, token, package_file_path, package_name, version]):
        fail("Required inputs: REPO_URL, TOKEN, PACKAGE_FILE_PATH, PACKAGE_NAME, VERSION.")

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
        fail(f'Package "{package_name}" not found in {package_file_path}.')

    pkg_path = pkg.get("path") or "./"
    app_path, app_doc = resolve_application_path(workdir, pkg_path, chart_name)

    update_target_revision(app_doc, version, chart_name)
    with open(app_path, "w", encoding="utf-8") as f:
        yaml.dump(app_doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    rel_path = Path(app_path).relative_to(workdir)
    print(f"Updated targetRevision to {version} in {rel_path}")

    run_git(["config", "user.name", "github-actions[bot]"], cwd=workdir)
    run_git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=workdir)
    run_git(["add", str(rel_path)], cwd=workdir)

    commit_result = run_git(
        ["commit", "-m", f"chore(helm): update {package_name} to {version}"],
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
    except subprocess.CalledProcessError as e:
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        if e.stdout:
            print(e.stdout, file=sys.stdout)
        sys.exit(e.returncode)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

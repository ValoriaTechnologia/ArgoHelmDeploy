"""Tests for main module: build_auth_url, find_application_in_dir, resolve_application_path, update_target_revision, main."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path when running tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_module


# --- build_auth_url ---


def test_build_auth_url_https_without_dot_git():
    out = main_module.build_auth_url("https://github.com/org/repo", "secret")
    assert "x-access-token:secret@" in out
    assert "github.com" in out
    assert out.endswith(".git") or ".git" in out


def test_build_auth_url_https_with_dot_git():
    out = main_module.build_auth_url("https://github.com/org/repo.git", "tok")
    assert "x-access-token:tok@" in out
    assert "github.com" in out


def test_build_auth_url_git_at_github():
    out = main_module.build_auth_url("git@github.com:org/repo.git", "t")
    assert "https://" in out
    assert "x-access-token:t@" in out
    assert "github.com" in out


def test_build_auth_url_non_https_unchanged():
    out = main_module.build_auth_url("http://example.com/repo.git", "x")
    assert out == "http://example.com/repo.git"
    out2 = main_module.build_auth_url("ssh://git@other.com/repo", "x")
    assert out2 == "ssh://git@other.com/repo"


def test_build_auth_url_strips_whitespace():
    out = main_module.build_auth_url("  https://github.com/a/b.git  ", "t")
    assert "x-access-token:t@" in out


# --- find_application_in_dir ---


def test_find_application_in_dir_empty_returns_none(tmp_path):
    assert main_module.find_application_in_dir(str(tmp_path), None) is None


def test_find_application_in_dir_non_yaml_ignored(tmp_path):
    (tmp_path / "readme.txt").write_text("hello")
    assert main_module.find_application_in_dir(str(tmp_path), None) is None


def test_find_application_in_dir_yaml_not_application_ignored(tmp_path):
    (tmp_path / "other.yaml").write_text("kind: ConfigMap\nmetadata:\n  name: x")
    assert main_module.find_application_in_dir(str(tmp_path), None) is None


def test_find_application_in_dir_single_application(tmp_path):
    app_yaml = """apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: myapp
spec:
  source:
    chart: mychart
    targetRevision: "1.0.0"
"""
    (tmp_path / "app.yaml").write_text(app_yaml)
    result = main_module.find_application_in_dir(str(tmp_path), None)
    assert result is not None
    path, doc = result
    assert "app.yaml" in path
    assert doc.get("kind") == "Application"
    assert doc.get("spec", {}).get("source", {}).get("targetRevision") == "1.0.0"


def test_find_application_in_dir_two_applications_returns_first(tmp_path):
    (tmp_path / "a.yaml").write_text("kind: Application\nspec:\n  source:\n    chart: ca\n    targetRevision: '1'")
    (tmp_path / "b.yaml").write_text("kind: Application\nspec:\n  source:\n    chart: cb\n    targetRevision: '2'")
    result = main_module.find_application_in_dir(str(tmp_path), None)
    assert result is not None
    path, _ = result
    assert "a.yaml" in path or "b.yaml" in path


def test_find_application_in_dir_with_chart_name_matches_source(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "kind: Application\nspec:\n  source:\n    chart: wanted\n    targetRevision: '0'"
    )
    (tmp_path / "y.yaml").write_text(
        "kind: Application\nspec:\n  source:\n    chart: other\n    targetRevision: '0'"
    )
    result = main_module.find_application_in_dir(str(tmp_path), "wanted")
    assert result is not None
    path, doc = result
    assert doc.get("spec", {}).get("source", {}).get("chart") == "wanted"


def test_find_application_in_dir_with_chart_name_matches_sources(tmp_path):
    (tmp_path / "multi.yaml").write_text("""
kind: Application
spec:
  sources:
    - chart: first
      targetRevision: '1'
    - chart: second
      targetRevision: '2'
""")
    result = main_module.find_application_in_dir(str(tmp_path), "second")
    assert result is not None
    _, doc = result
    sources = doc.get("spec", {}).get("sources", [])
    assert any(s.get("chart") == "second" for s in sources)


# --- resolve_application_path ---


def test_resolve_application_path_file_valid(tmp_path):
    app_yaml = "kind: Application\nspec:\n  source:\n    chart: c\n    targetRevision: '1'"
    f = tmp_path / "app.yml"
    f.write_text(app_yaml)
    path, doc = main_module.resolve_application_path(str(tmp_path), "app.yml", None)
    assert path == str(f.resolve())
    assert doc.get("kind") == "Application"


def test_resolve_application_path_file_not_application_exits(tmp_path):
    (tmp_path / "bad.yaml").write_text("kind: ConfigMap")
    with pytest.raises(SystemExit):
        main_module.resolve_application_path(str(tmp_path), "bad.yaml", None)


def test_resolve_application_path_dir_contains_application(tmp_path):
    (tmp_path / "app.yaml").write_text("kind: Application\nspec:\n  source:\n    chart: x\n    targetRevision: '0'")
    path, doc = main_module.resolve_application_path(str(tmp_path), ".", None)
    assert "app.yaml" in path
    assert doc.get("kind") == "Application"


def test_resolve_application_path_nonexistent_exits(tmp_path):
    with pytest.raises(SystemExit):
        main_module.resolve_application_path(str(tmp_path), "nonexistent", None)


# --- update_target_revision ---


def test_update_target_revision_spec_source():
    doc = {"spec": {"source": {"chart": "mychart", "targetRevision": "1.0.0"}}}
    main_module.update_target_revision(doc, "2.0.0", None)
    assert doc["spec"]["source"]["targetRevision"] == "2.0.0"


def test_update_target_revision_spec_sources_no_chart_name():
    doc = {"spec": {"sources": [{"chart": "c1", "targetRevision": "1"}]}}
    main_module.update_target_revision(doc, "2", None)
    assert doc["spec"]["sources"][0]["targetRevision"] == "2"


def test_update_target_revision_spec_sources_with_chart_name():
    doc = {
        "spec": {
            "sources": [
                {"chart": "c1", "targetRevision": "1"},
                {"chart": "c2", "targetRevision": "2"},
            ]
        }
    }
    main_module.update_target_revision(doc, "9", "c2")
    assert doc["spec"]["sources"][0]["targetRevision"] == "1"
    assert doc["spec"]["sources"][1]["targetRevision"] == "9"


def test_update_target_revision_chart_name_mismatch_exits():
    doc = {"spec": {"source": {"chart": "other", "targetRevision": "1"}}}
    with pytest.raises(SystemExit):
        main_module.update_target_revision(doc, "2", "wanted")


def test_update_target_revision_no_source_exits():
    doc = {"spec": {}}
    with pytest.raises(SystemExit):
        main_module.update_target_revision(doc, "1", None)


# --- main() with mocks ---


def test_main_happy_path_updates_application_file(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "packages.yaml").write_text("""packages:
  - name: mypkg
    path: ./
""")
    (workdir / "app.yaml").write_text("""apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: test
spec:
  source:
    chart: mychart
    targetRevision: "1.0.0"
""")

    env = {
        "REPO_URL": "https://github.com/org/repo.git",
        "TOKEN": "secret",
        "PACKAGE_FILE_PATH": "packages.yaml",
        "PACKAGE_NAME": "mypkg",
        "VERSION": "2.0.0",
        "CHART_NAME": "",
        "BRANCH": "main",
    }

    with patch.object(main_module, "tempfile") as m_tempfile:
        m_tempfile.mkdtemp.return_value = str(workdir)
        with patch.object(main_module, "run_git") as m_run_git:
            m_run_git.return_value = MagicMock(returncode=0)
            with patch.dict(os.environ, env, clear=False):
                main_module.main()

    # Application file should have updated targetRevision
    content = (workdir / "app.yaml").read_text()
    assert "2.0.0" in content
    import yaml
    doc = yaml.safe_load(content)
    assert doc["spec"]["source"]["targetRevision"] == "2.0.0"

    # Git should have been called: clone, config (x2), add, commit, push
    assert m_run_git.call_count >= 5
    arg_lists = [c[0][0] for c in m_run_git.call_args_list]
    first_call = arg_lists[0]
    assert first_call[:6] == ["clone", "--branch", "main", "--single-branch", "--depth", "1"]
    assert any("config" in args for args in arg_lists)
    add_calls = [args for args in arg_lists if args and args[0] == "add"]
    assert len(add_calls) == 1
    commit_calls = [args for args in arg_lists if args and args[0] == "commit"]
    assert len(commit_calls) == 1


def test_main_missing_required_input_exits():
    with patch.dict(os.environ, {"REPO_URL": "", "TOKEN": "x", "PACKAGE_FILE_PATH": "p", "PACKAGE_NAME": "n", "VERSION": "1"}, clear=False):
        with pytest.raises(SystemExit):
            main_module.main()


# --- integration test (real clone, mock push) ---


@pytest.mark.integration
def test_integration_real_mock_repo(tmp_path):
    """Clone real ArgoHelmDeploy-Mock repo, run main(), assert application.yaml updated; push is mocked."""
    workdir = tmp_path / "workdir"

    run_git_orig = main_module.run_git

    def run_git_wrapper(args, cwd=None, check=True):
        if args and args[0] == "push":
            return MagicMock(returncode=0)
        return run_git_orig(args, cwd=cwd, check=check)

    env = {
        "REPO_URL": "https://github.com/ValoriaTechnologia/ArgoHelmDeploy-Mock.git",
        "TOKEN": "dummy",
        "PACKAGE_FILE_PATH": "packages.yaml",
        "PACKAGE_NAME": "argo-app",
        "VERSION": "2.0.0",
        "CHART_NAME": "my-chart",
        "BRANCH": "main",
    }

    with patch.object(main_module, "tempfile") as m_tempfile:
        m_tempfile.mkdtemp.return_value = str(workdir)
        with patch.object(main_module, "run_git", run_git_wrapper):
            with patch.dict(os.environ, env, clear=False):
                main_module.main()

    app_file = workdir / "application.yaml"
    assert app_file.exists(), "application.yaml should exist after main()"
    import yaml
    doc = yaml.safe_load(app_file.read_text(encoding="utf-8"))
    assert doc.get("spec", {}).get("source", {}).get("targetRevision") == "2.0.0"
    assert doc.get("spec", {}).get("source", {}).get("chart") == "my-chart"

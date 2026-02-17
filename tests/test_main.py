"""Tests for main module: build_auth_url, resolve_application_path, update_target_revision, main."""

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


def test_resolve_application_path_directory_fails(tmp_path):
    (tmp_path / "app.yaml").write_text("kind: Application\nspec:\n  source:\n    chart: x\n    targetRevision: '0'")
    with pytest.raises(SystemExit):
        main_module.resolve_application_path(str(tmp_path), ".", None)


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
    path: app.yaml
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
        "INPUT_REPO_URL": "https://github.com/org/repo.git",
        "INPUT_TOKEN": "secret",
        "INPUT_PACKAGE_FILE_PATH": "packages.yaml",
        "INPUT_PACKAGE_NAME": "mypkg",
        "INPUT_VERSION": "2.0.0",
        "INPUT_CHART_NAME": "",
        "INPUT_BRANCH": "main",
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
    with patch.dict(os.environ, {"INPUT_REPO_URL": "", "INPUT_TOKEN": "x", "INPUT_PACKAGE_FILE_PATH": "p", "INPUT_PACKAGE_NAME": "n", "INPUT_VERSION": "1"}, clear=False):
        with pytest.raises(ValueError, match="Missing required input"):
            main_module.main()


def test_main_package_not_in_file_skips_without_error(tmp_path, capsys):
    """When the requested package is not in packages file, main() returns without error and prints skip message."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "packages.yaml").write_text("""packages:
  - name: otherpkg
    path: app.yaml
""")
    (workdir / "app.yaml").write_text("kind: Application\nspec:\n  source:\n    chart: x\n    targetRevision: '1'")

    env = {
        "INPUT_REPO_URL": "https://github.com/org/repo.git",
        "INPUT_TOKEN": "secret",
        "INPUT_PACKAGE_FILE_PATH": "packages.yaml",
        "INPUT_PACKAGE_NAME": "missingpkg",
        "INPUT_VERSION": "2.0.0",
        "INPUT_CHART_NAME": "",
        "INPUT_BRANCH": "main",
    }

    with patch.object(main_module, "tempfile") as m_tempfile:
        m_tempfile.mkdtemp.return_value = str(workdir)
        with patch.object(main_module, "run_git") as m_run_git:
            m_run_git.return_value = MagicMock(returncode=0)
            with patch.dict(os.environ, env, clear=False):
                main_module.main()

    out, err = capsys.readouterr()
    assert "missingpkg" in out
    assert "not found" in out
    assert "skipping" in out
    # Application file unchanged
    assert "1" in (workdir / "app.yaml").read_text()
    assert "2.0.0" not in (workdir / "app.yaml").read_text()


def test_main_multi_updates_multiple_env_files(tmp_path):
    """Multi mode: path with $, environments dev,staging; both files updated, single commit."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "packages.yaml").write_text("""packages:
  - name: mypkg
    path: apps/$/application.yaml
""")
    (workdir / "apps" / "dev").mkdir(parents=True)
    (workdir / "apps" / "staging").mkdir(parents=True)
    app_content = "kind: Application\nspec:\n  source:\n    chart: c\n    targetRevision: '1.0.0'"
    (workdir / "apps" / "dev" / "application.yaml").write_text(app_content)
    (workdir / "apps" / "staging" / "application.yaml").write_text(app_content)

    env = {
        "INPUT_REPO_URL": "https://github.com/org/repo.git",
        "INPUT_TOKEN": "secret",
        "INPUT_PACKAGE_FILE_PATH": "packages.yaml",
        "INPUT_PACKAGE_NAME": "mypkg",
        "INPUT_VERSION": "2.0.0",
        "INPUT_CHART_NAME": "",
        "INPUT_BRANCH": "main",
        "INPUT_MULTI": "true",
        "INPUT_ENVIRONMENTS": "dev,staging",
    }

    with patch.object(main_module, "tempfile") as m_tempfile:
        m_tempfile.mkdtemp.return_value = str(workdir)
        with patch.object(main_module, "run_git") as m_run_git:
            m_run_git.return_value = MagicMock(returncode=0)
            with patch.dict(os.environ, env, clear=False):
                main_module.main()

    assert (workdir / "apps" / "dev" / "application.yaml").read_text().count("2.0.0") >= 1
    assert (workdir / "apps" / "staging" / "application.yaml").read_text().count("2.0.0") >= 1
    add_calls = [c[0][0] for c in m_run_git.call_args_list if c[0][0] and c[0][0][0] == "add"]
    assert len(add_calls) == 2
    commit_calls = [c for c in m_run_git.call_args_list if c[0][0] and c[0][0][0] == "commit"]
    assert len(commit_calls) == 1
    # call_args = (args_tuple, kwargs); args_tuple[0] = list passed to run_git
    git_args_list = commit_calls[0][0][0]
    commit_msg = git_args_list[2] if len(git_args_list) > 2 else str(git_args_list)
    assert "envs:" in commit_msg or "dev" in commit_msg


def test_main_multi_without_environments_raises():
    with patch.dict(
        os.environ,
        {
            "INPUT_REPO_URL": "https://x.git",
            "INPUT_TOKEN": "t",
            "INPUT_PACKAGE_FILE_PATH": "p.yaml",
            "INPUT_PACKAGE_NAME": "pkg",
            "INPUT_VERSION": "1",
            "INPUT_MULTI": "true",
            "INPUT_ENVIRONMENTS": "",
        },
        clear=False,
    ):
        with pytest.raises(ValueError, match="environments.*required"):
            main_module.main()


# --- integration test (real clone, mock push) ---


@pytest.mark.integration
@pytest.mark.skip(reason="Mock repo uses directory path; action now requires path to be a file (see plan). Update ArgoHelmDeploy-Mock packages.yaml to use file path to run this test.")
def test_integration_real_mock_repo(tmp_path):
    """Clone real ArgoHelmDeploy-Mock repo, run main(), assert application.yaml updated; push is mocked."""
    workdir = tmp_path / "workdir"

    run_git_orig = main_module.run_git

    def run_git_wrapper(args, cwd=None, check=True):
        if args and args[0] == "push":
            return MagicMock(returncode=0)
        return run_git_orig(args, cwd=cwd, check=check)

    env = {
        "INPUT_REPO_URL": "https://github.com/ValoriaTechnologia/ArgoHelmDeploy-Mock.git",
        "INPUT_TOKEN": "dummy",
        "INPUT_PACKAGE_FILE_PATH": "packages.yaml",
        "INPUT_PACKAGE_NAME": "argo-app",
        "INPUT_VERSION": "2.0.0",
        "INPUT_CHART_NAME": "my-chart",
        "INPUT_BRANCH": "main",
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

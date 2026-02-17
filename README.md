# Update ArgoCD Helm Chart Version

GitHub Action that updates `spec.source.targetRevision` in an ArgoCD Application manifest for a Helm chart. It reads a package file to locate the Application, then updates the version and pushes the change back to the repo.

## Package file format

The action expects a YAML file in the target repo with this structure:

```yaml
packages:
  - name: NOM_PACKAGE
    path: ./
```

- **`name`**: Identifies the package (used with input `package-name`).
- **`path`**: Path to the ArgoCD Application manifest—either a directory (e.g. `./` or `./apps/myapp`) or a direct file path (e.g. `./apps/myapp/application.yaml`). For directories, the action looks for a file with `kind: Application` in that directory.

## Inputs

| Input | Description | Required |
| ----- | ----------- | -------- |
| `repo-url` | HTTPS URL of the ArgoCD Git repository | Yes |
| `token` | Authentication token (PAT or `GITHUB_TOKEN`) with read/write access to the repo | Yes |
| `package-file-path` | Path to the packages YAML file in the repo (e.g. `packages.yaml`) | Yes |
| `package-name` | Name of the package to update (must match `packages[].name`) | Yes |
| `version` | New value for `spec.source.targetRevision` | Yes |
| `chart-name` | Optional. Chart name in `spec.source.chart` when multiple Applications exist in the same path | No |
| `branch` | Branch to clone and push to | No (default: `main`) |

## Example workflow

Use this action from another repository (e.g. a CI pipeline that releases a Helm chart and should bump the version in the ArgoCD repo):

```yaml
name: Update ArgoCD Helm version

on:
  workflow_dispatch:
    inputs:
      package_name:
        description: 'Package name in packages.yaml'
        required: true
      version:
        description: 'New Helm chart version (targetRevision)'
        required: true

jobs:
  update-argocd:
    runs-on: ubuntu-latest
    steps:
      - name: Update ArgoCD Application
        uses: YOUR_ORG/ArgoHelmDeploy@v1
        with:
          repo-url: 'https://github.com/YOUR_ORG/argocd-apps.git'
          token: ${{ secrets.ARGOCD_REPO_TOKEN }}
          package-file-path: 'packages.yaml'
          package-name: ${{ inputs.package_name }}
          version: ${{ inputs.version }}
```

Replace `YOUR_ORG/ArgoHelmDeploy@v1` with your repo and tag (e.g. `ValoriaTechnologia/ArgoHelmDeploy@main` when testing from a branch).

## Behaviour

1. Clones the ArgoCD repo using `repo-url` and `token` (branch from `branch`).
2. Reads the file at `package-file-path` and finds the package whose `name` equals `package-name`.
3. Resolves the package `path` to an ArgoCD Application manifest (file or directory containing `kind: Application`).
4. Sets `spec.source.targetRevision` (or the matching source in `spec.sources` when using `chart-name`) to `version`.
5. Commits the change with message `chore(helm): update <package-name> to <version>` and pushes to the same branch.

If `targetRevision` is already equal to `version`, the action skips the commit and push and exits successfully.

## Implementation

The action is implemented in **Python 3.11** (composite action with `actions/setup-python` and `main.py`). No build step is required; the script runs as-is when the action is used via `uses: org/repo@ref`.

## Tests

Run the test suite with pytest (no network or real Git operations; tests use mocks and temporary files):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

Optional: generate a coverage report with `pytest tests/ -v --cov=main --cov-report=term-missing`.

## Security

- The `token` input is masked in logs (the script emits `::add-mask::` for the runner).
- Use a fine-grained PAT or a dedicated bot account with minimal write access to the ArgoCD repo.

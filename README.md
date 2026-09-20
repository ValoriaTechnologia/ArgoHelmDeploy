# Update ArgoCD Helm Chart Version

GitHub Action that updates `spec.source.targetRevision` in an ArgoCD Application manifest for a Helm chart. It reads a package file to locate the Application, then updates the version and pushes the change back to the repo.

## Package file format

The action expects a YAML file in the target repo with this structure:

```yaml
packages:
  - name: NOM_PACKAGE
    path: ./application.yaml
    source:                    # optional; required when Application has multiple spec.sources
      chart: my-helm-chart     # and/or repoURL / path
```

- **`name`**: Identifies the package (used with input `package_name`).
- **`path`**: Path to the ArgoCD Application manifest **file** (must point to a file with `kind: Application`). Directories are not allowed. If the path contains **`$`**, it is replaced by the action input **`environment`** (required in that case); e.g. `./apps/$/application.yaml` with `environment: dev` → `./apps/dev/application.yaml`.
- **`bootstrap`**: Optional boolean (`true` / `false`). If `true`, the action finds the package but does **not** bump `targetRevision` (it prints a skip message and exits successfully). Use this for bootstrap-style applications that should not receive automated Helm version updates.
- **`source`**: Optional mapping used to select which entry to update when the Application uses `spec.sources` (multi-source). All fields present must match (AND). Supported keys:
  - **`chart`**: Helm chart name
  - **`repoURL`**: source repository URL
  - **`path`**: git path within the repo

  With a single `spec.source` (or a single entry in `spec.sources`), `source` is optional. With **multiple** `spec.sources`, `source` (or the `chart_name` input) is **required**; the action fails if zero or more than one entry matches. There is no silent fallback to the first source.

### Multi-source example

```yaml
# packages.yaml
packages:
  - name: my-app
    path: apps/$/application.yaml
    source:
      chart: my-helm-chart
```

```yaml
# application.yaml (excerpt)
spec:
  sources:
    - repoURL: https://charts.example.com
      chart: my-helm-chart
      targetRevision: 1.0.0
    - repoURL: https://github.com/org/values.git
      path: values/prod
      targetRevision: main
```

Only the Helm source matching `chart: my-helm-chart` is updated.

## Inputs

| Input | Description | Required |
| ----- | ----------- | -------- |
| `repo_url` | HTTPS URL of the ArgoCD Git repository | Yes |
| `token` | Authentication token (PAT or `GITHUB_TOKEN`) with read/write access to the repo | Yes |
| `package_file_path` | Path to the packages YAML file in the repo (e.g. `packages.yaml`) | Yes |
| `package_name` | Name of the package to update (must match `packages[].name`) | Yes |
| `version` | New value for `spec.source.targetRevision` | Yes |
| `chart_name` | Optional. Overrides `packages[].source.chart` to select which source to update | No |
| `branch` | Branch to clone and push to | No (default: `main`) |
| `environment` | Environment name (required when package path contains `$`). The `$` in path is replaced by this value | No (required if path contains `$`) |

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
          repo_url: 'https://github.com/YOUR_ORG/argocd-apps.git'
          token: ${{ secrets.ARGOCD_REPO_TOKEN }}
          package_file_path: 'packages.yaml'
          package_name: ${{ inputs.package_name }}
          version: ${{ inputs.version }}
```

Replace `YOUR_ORG/ArgoHelmDeploy@v1` with your repo and tag (e.g. `ValoriaTechnologia/ArgoHelmDeploy@main` when testing from a branch).

When the package path contains `$`, pass the **`environment`** input (e.g. `environment: 'dev'`). To update several environments, call the action **once per environment** (e.g. with a matrix):

```yaml
strategy:
  matrix:
    environment: [dev, staging, prod]
steps:
  - uses: YOUR_ORG/ArgoHelmDeploy@v1
    with:
      repo_url: '...'
      token: ${{ secrets.ARGOCD_REPO_TOKEN }}
      package_file_path: 'packages.yaml'
      package_name: mypkg
      version: ${{ inputs.version }}
      environment: ${{ matrix.environment }}
```

## Run action on mock repo (E2E)

The workflow [.github/workflows/run-on-mock.yml](.github/workflows/run-on-mock.yml) runs the action for real against [ValoriaTechnologia/ArgoHelmDeploy-Mock](https://github.com/ValoriaTechnologia/ArgoHelmDeploy-Mock): it clones the repo, updates `application.yaml` `targetRevision`, commits and pushes. The mock repo’s `packages.yaml` must use a **file path** (not a directory) to the Application manifest. Trigger it manually (Actions → "Run action on mock repo" → Run workflow, optional input `version`) or on push to `main` when the action or workflow files change. **Required:** add a repository secret `MOCK_REPO_TOKEN` (or `TOKEN_MOCK_REPO` as in the workflow) with a PAT that has write access to the mock repo; otherwise the push step will fail.

## Behaviour

1. Clones the ArgoCD repo using `repo_url` and `token` (branch from `branch`).
2. Reads the file at `package_file_path` and finds the package whose `name` equals `package_name`.
3. If `bootstrap` is `true` for that package, the action prints a skip message and exits successfully (no Application file change, no commit or push).
4. If the package `path` contains `$`, the **`environment`** input is required; `$` is replaced by that value to get the file path. The path must point to a single Application manifest **file** (directories are not allowed).
5. Builds a source selector from `packages[].source` and optional input `chart_name` (which overrides `source.chart`). Sets `targetRevision` on the matching `spec.source` or `spec.sources` entry. Multiple sources without a selector cause a failure.
6. Commits the change with message `chore(helm): update <package_name> to <version>` and pushes to the same branch.

**One file per run.** To update multiple environments, the workflow must call the action **multiple times** (e.g. matrix over `environment`: one job or step per value).

If `targetRevision` is already equal to `version`, the action skips the commit and push and exits successfully.

## Implementation

The action is a **Docker action**: it runs in a container built from the repo’s [Dockerfile](Dockerfile) (Python 3.12, git, dependencies installed). There are no composite steps; the script [main.py](main.py) is executed inside the image when you use `uses: org/repo@ref`.

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

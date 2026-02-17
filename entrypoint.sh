#!/bin/sh
set -e

export REPO_URL="${INPUT_REPO_URL}"
export TOKEN="${INPUT_TOKEN}"
export PACKAGE_FILE_PATH="${INPUT_PACKAGE_FILE_PATH}"
export PACKAGE_NAME="${INPUT_PACKAGE_NAME}"
export VERSION="${INPUT_VERSION}"
export CHART_NAME="${INPUT_CHART_NAME}"
export BRANCH="${INPUT_BRANCH:-main}"

exec python main.py

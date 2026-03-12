#!/bin/bash
set -e

REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
TARGET_BRANCH="${PROJECT_BRANCH:-dev}"

if [ ! -d /project/.git ]; then
    echo "Cloning ${GITHUB_REPO} (branch: ${TARGET_BRANCH}) into /project..."
    if ! git clone --branch "$TARGET_BRANCH" "$REPO_URL" /project; then
        echo "WARNING: Git clone failed — check GITHUB_TOKEN. Bot will start without project."
    fi
else
    echo "Pulling latest changes on ${TARGET_BRANCH}..."
    cd /project
    git remote set-url origin "$REPO_URL"
    if git fetch origin && git checkout "$TARGET_BRANCH" 2>/dev/null; then
        git reset --hard "origin/${TARGET_BRANCH}"
    else
        echo "WARNING: Git pull failed — check GITHUB_TOKEN. Using existing checkout."
    fi
fi

if [ -d /project/.git ]; then
    echo "Project ready at /project ($(cd /project && git log --oneline -1))"
else
    echo "WARNING: No project checkout available."
fi

cd /app
exec python -m bot.main

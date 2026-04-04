---
name: deploy
description: Deployment workflow. Build, test, tag, push, deploy. Supports PyPI, Docker, GitHub releases.
allowed-tools: bash read_file grep
user-invocable: true
---

# Deployment Workflow

## Steps
1. Run tests: `python -m pytest -x -q`
2. Check version in pyproject.toml or setup.py
3. Build: `python -m build` or `docker build .`
4. Deploy based on target ($ARGUMENTS):
   - **pypi**: `twine upload dist/*`
   - **docker**: `docker push`
   - **github**: `gh release create`
   - **server**: `rsync` or `ssh` deploy
5. Tag: `git tag vX.Y.Z && git push --tags`
6. Notify: output deployment summary

## Rules
- ALWAYS run tests before deploying
- NEVER deploy with uncommitted changes
- Check that version was bumped
- Verify the deployment succeeded

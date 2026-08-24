# Release Process

This document describes how to create a new release of PlanetScale Discovery Tools.

## Versioning

We use [Semantic Versioning](https://semver.org/):
- **MAJOR** version: Incompatible API changes
- **MINOR** version: New functionality in a backwards compatible manner
- **PATCH** version: Backwards compatible bug fixes

## Release Process

### 1. Prepare the Release

Ensure all changes are committed and main branch is up to date:

```bash
git checkout main
git pull origin main
```

Run tests locally to ensure everything passes:

```bash
source .venv/bin/activate
python -m pytest tests/
python -m black --check planetscale_discovery tests
python -m flake8 planetscale_discovery tests
python -m mypy planetscale_discovery
```

### 2. Update CHANGELOG.md

Update the CHANGELOG.md file with changes for this release:

```bash
# Edit CHANGELOG.md
# Move items from [Unreleased] to new version section
# Add release date
```

Example:
```markdown
## [Unreleased]

## [1.0.1] - 2025-10-11

### Fixed
- Fix connection timeout for slow databases

### Changed
- Improve error messages for authentication failures
```

Follow [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format:
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for removed features
- **Fixed** for bug fixes
- **Security** for vulnerability fixes

### 3. Bump Version

Use the version bump script:

```bash
# For a patch release (1.0.0 -> 1.0.1)
./bump_version.sh patch

# For a minor release (1.0.0 -> 1.1.0)
./bump_version.sh minor

# For a major release (1.0.0 -> 2.0.0)
./bump_version.sh major

# Or set specific version
./bump_version.sh 1.2.3
```

This will update:
- `VERSION` file
- `setup.py` version

### 4. Review and Commit Changes

```bash
# Review the changes
git diff VERSION setup.py CHANGELOG.md

# Commit the version bump and changelog
git add VERSION setup.py CHANGELOG.md
git commit -m "Release vX.Y.Z"
```

### 5. Create and Push Tag

```bash
# Create annotated tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"

# Push commit and tag together
git push origin main --tags
```

**Important:** You do **not** need to build artifacts locally. GitHub Actions handles this automatically.

### 6. Automated Build & Release

Once the tag is pushed, GitHub Actions will **automatically**:

1. ✅ Build the release tarball (`ps-discovery-X.Y.Z.tar.gz`)
2. ✅ Generate SHA256 checksum
3. ✅ Create a GitHub Release with release notes
4. ✅ Attach release artifacts for download

**No local build required!** Just push the tag.

Monitor the progress at: https://github.com/planetscale/ps-discovery/actions

### 7. Verify Release

After the GitHub Action completes:

1. Go to: https://github.com/planetscale/ps-discovery/releases
2. Verify the release is published with:
   - Release notes
   - `ps-discovery-X.Y.Z.tar.gz`
   - `ps-discovery-X.Y.Z.tar.gz.sha256`

### 8. Test the Release Artifact

Download and test the release tarball:

```bash
# Download the release
wget https://github.com/planetscale/ps-discovery/releases/download/vX.Y.Z/ps-discovery-X.Y.Z.tar.gz
wget https://github.com/planetscale/ps-discovery/releases/download/vX.Y.Z/ps-discovery-X.Y.Z.tar.gz.sha256

# Verify checksum
sha256sum -c ps-discovery-X.Y.Z.tar.gz.sha256

# Extract and test
tar -xzf ps-discovery-X.Y.Z.tar.gz
cd ps-discovery-X.Y.Z
./setup.sh
source venv/bin/activate
python -m planetscale_discovery --help
```

## Manual Release (if needed)

If you need to build a release locally:

```bash
# Build release tarball
./build_release.sh 1.0.0

# Artifacts will be in dist/
ls -lh dist/
```

## Release Artifact Contents

Each release includes:
- Source code (`planetscale_discovery/`)
- Documentation (`README.md`, `CONTRIBUTING.md`, `docs/`)
- Setup scripts (`setup.sh`, `setup.py`, `requirements.txt`)
- Configuration examples (`config-examples/`)
- Tests (`tests/`, `run_tests.py`)
- License (`LICENSE`)
- Installation instructions (`INSTALL.txt`)
- Release notes (`RELEASE_NOTES.txt`)

## Troubleshooting

### Tag already exists
```bash
# Delete local tag
git tag -d vX.Y.Z

# Delete remote tag
git push origin --delete vX.Y.Z

# Recreate tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

### Version mismatch error
Ensure `VERSION` file and `setup.py` have the same version number and match the git tag.

### Release workflow fails
Check GitHub Actions logs at: https://github.com/planetscale/ps-discovery/actions

Common issues:
- VERSION file doesn't match tag
- Build artifacts not found
- Insufficient permissions (check GITHUB_TOKEN)

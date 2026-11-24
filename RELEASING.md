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

Refresh the installer's dependency lock. `./setup.sh` installs only the exact
versions and hashes in `requirements/setup-lock.txt` (and pip from
`requirements/setup-pip.txt`), and the release zip ships both, so the
versions a customer gets are the ones you test here. Regenerate them with
[uv](https://docs.astral.sh/uv/) when a release should pick up newer
dependencies, and whenever a package is added to `setup.sh`, which must also be
added to `requirements/setup.in`. The packages they share with
`requirements-lock.txt` are capped by it, so refresh that lock first.

```bash
uv pip compile requirements/setup.in -o requirements/setup-lock.txt --universal --python-version 3.10 --generate-hashes --no-header --upgrade
uv pip compile requirements/setup-pip.in -o requirements/setup-pip.txt --universal --python-version 3.10 --generate-hashes --no-header --upgrade
uv run --no-project --with packaging==26.3 python requirements/check_setup_lock.py
```

The lock is universal: one file covers Python 3.10 to 3.14 on Linux and macOS,
with environment markers where a platform needs a different version.
`setup.sh` installs wheels only, so `check_setup_lock.py` checks that, on
every supported platform and Python, the lock pins each package under a marker
true for that target, and lists the hash of a wheel that target can install. It
also checks every hash in the lock against PyPI. When a version has no wheel for
a target, cap that package for that platform in `setup.in`, as the file
already does for Intel Macs, and regenerate.

CI runs `check_setup_lock.py`, installs every locked package on Linux for each
supported Python, and runs `tests/unit/test_setup_lock.py`, which fails when
`setup.sh` and the lock disagree on a package or a version bound.

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

This will update the version in:
- `VERSION`
- `setup.py`
- `pyproject.toml`
- `planetscale_discovery/__init__.py`
- `README.md` (the release zip name in Quick Start)
- `docs/workload-bundle.md` (the example `discovery_version`)

The script stops with an error if any of these files was not updated to the new version. The release workflow checks the same files and a `CHANGELOG.md` section for the tag, and stops before it builds anything if one does not match.

### 4. Review and Commit Changes

```bash
# Review the changes
git diff VERSION setup.py pyproject.toml planetscale_discovery/__init__.py README.md docs/workload-bundle.md CHANGELOG.md

# Commit the version bump and changelog
git add VERSION setup.py pyproject.toml planetscale_discovery/__init__.py README.md docs/workload-bundle.md CHANGELOG.md
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

1. ✅ Build the release zip file (`ps-discovery-X.Y.Z.zip`)
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
   - `ps-discovery-X.Y.Z.zip`
   - `ps-discovery-X.Y.Z.zip.sha256`

### 8. Test the Release Artifact

Download and test the release zip file:

```bash
# Download the release
wget https://github.com/planetscale/ps-discovery/releases/download/vX.Y.Z/ps-discovery-X.Y.Z.zip
wget https://github.com/planetscale/ps-discovery/releases/download/vX.Y.Z/ps-discovery-X.Y.Z.zip.sha256

# Verify checksum
sha256sum -c ps-discovery-X.Y.Z.zip.sha256

# Extract and test
unzip ps-discovery-X.Y.Z.zip
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

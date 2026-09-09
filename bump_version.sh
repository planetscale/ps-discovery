#!/bin/bash
#
# Bump version for PlanetScale Discovery Tools
# Usage: ./bump_version.sh [major|minor|patch|X.Y.Z]
#

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 [major|minor|patch|X.Y.Z]"
    echo ""
    echo "Examples:"
    echo "  $0 patch      # 1.0.0 -> 1.0.1"
    echo "  $0 minor      # 1.0.0 -> 1.1.0"
    echo "  $0 major      # 1.0.0 -> 2.0.0"
    echo "  $0 1.2.3      # Set to specific version"
    exit 1
fi

# Read current version
CURRENT_VERSION=$(cat VERSION | tr -d '\n')
echo "Current version: ${CURRENT_VERSION}"

# Parse current version
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# Determine new version
BUMP_TYPE="$1"
case "$BUMP_TYPE" in
    major)
        NEW_VERSION="$((MAJOR + 1)).0.0"
        ;;
    minor)
        NEW_VERSION="${MAJOR}.$((MINOR + 1)).0"
        ;;
    patch)
        NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
        ;;
    *)
        # Assume specific version provided
        if [[ "$BUMP_TYPE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            NEW_VERSION="$BUMP_TYPE"
        else
            echo "❌ Error: Invalid version format. Must be 'major', 'minor', 'patch', or X.Y.Z"
            exit 1
        fi
        ;;
esac

echo "New version: ${NEW_VERSION}"
echo ""
read -p "Update version to ${NEW_VERSION}? (y/N): " confirm

if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "❌ Aborted"
    exit 1
fi

# Update VERSION file
echo "${NEW_VERSION}" > VERSION
echo "✅ Updated VERSION file"

# Update setup.py
sed -i.bak "s/version=\"${CURRENT_VERSION}\"/version=\"${NEW_VERSION}\"/" setup.py
rm setup.py.bak
echo "✅ Updated setup.py"

# Update pyproject.toml
sed -i.bak "s/^version = \"${CURRENT_VERSION}\"/version = \"${NEW_VERSION}\"/" pyproject.toml
rm pyproject.toml.bak
echo "✅ Updated pyproject.toml"

# Update the package __version__
sed -i.bak "s/^__version__ = \"${CURRENT_VERSION}\"/__version__ = \"${NEW_VERSION}\"/" planetscale_discovery/__init__.py
rm planetscale_discovery/__init__.py.bak
echo "✅ Updated planetscale_discovery/__init__.py"

# Fail if any file did not end up at the new version
ESCAPED_NEW_VERSION="${NEW_VERSION//./\\.}"

check_version() {
    grep -Eq "$2" "$1" || {
        echo "❌ Error: $1 was not updated to ${NEW_VERSION}"
        exit 1
    }
}

check_version VERSION                           "^${ESCAPED_NEW_VERSION}$"
check_version setup.py                          "version=\"${ESCAPED_NEW_VERSION}\","
check_version pyproject.toml                    "^version = \"${ESCAPED_NEW_VERSION}\"$"
check_version planetscale_discovery/__init__.py "^__version__ = \"${ESCAPED_NEW_VERSION}\"$"

# Show what changed
echo ""
echo "Changes made:"
git diff VERSION setup.py pyproject.toml planetscale_discovery/__init__.py

echo ""
echo "Next steps:"
echo "1. Review the changes above"
echo "2. Commit: git add VERSION setup.py pyproject.toml planetscale_discovery/__init__.py && git commit -m 'Bump version to ${NEW_VERSION}'"
echo "3. Tag: git tag -a v${NEW_VERSION} -m 'Release v${NEW_VERSION}'"
echo "4. Push: git push origin main && git push origin v${NEW_VERSION}"
echo ""
echo "GitHub Actions will automatically create the release when the tag is pushed."

#!/bin/bash
#
# Build a release tarball of PlanetScale Discovery Tools
# Usage: ./build_release.sh [version]
#
# If version is not provided, reads from VERSION file
#

set -e

# Get version
if [ -n "$1" ]; then
    VERSION="$1"
else
    if [ ! -f VERSION ]; then
        echo "❌ Error: VERSION file not found and no version provided"
        echo "Usage: $0 [version]"
        exit 1
    fi
    VERSION=$(cat VERSION | tr -d '\n')
fi

echo "🚀 Building PlanetScale Discovery Tools Release v${VERSION}"
echo "=============================================="

# Validate version format (semver: X.Y.Z)
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ Error: Invalid version format. Must be semantic version (e.g., 1.0.0)"
    exit 1
fi

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf dist/ps-discovery-${VERSION}
rm -f dist/ps-discovery-${VERSION}.zip
mkdir -p dist

# Create release directory structure
RELEASE_DIR="dist/ps-discovery-${VERSION}"
echo "📦 Creating release structure at ${RELEASE_DIR}..."
mkdir -p "${RELEASE_DIR}"

# Copy source code
echo "📋 Copying source code..."
cp -r planetscale_discovery "${RELEASE_DIR}/"

# Copy documentation
echo "📚 Copying documentation..."
cp README.md "${RELEASE_DIR}/"
cp LICENSE "${RELEASE_DIR}/"
cp CONTRIBUTING.md "${RELEASE_DIR}/"
cp -r docs "${RELEASE_DIR}/" 2>/dev/null || echo "⚠️  No docs/ directory found"
cp -r config-examples "${RELEASE_DIR}/"

# Copy setup scripts and wrapper
echo "🔧 Copying setup and installation scripts..."
cp setup.py "${RELEASE_DIR}/"
cp setup.sh "${RELEASE_DIR}/"
cp ps-discovery "${RELEASE_DIR}/"
cp requirements.txt "${RELEASE_DIR}/"
cp pyproject.toml "${RELEASE_DIR}/"
cp MANIFEST.in "${RELEASE_DIR}/"
cp VERSION "${RELEASE_DIR}/"

# Copy configuration files
echo "⚙️  Copying configuration files..."
cp pytest.ini "${RELEASE_DIR}/" 2>/dev/null || true
cp mypy.ini "${RELEASE_DIR}/" 2>/dev/null || true
cp .bandit "${RELEASE_DIR}/" 2>/dev/null || true

# Note: Tests are not included in release artifacts
# They are available in the source repository for developers

# Create installation instructions
echo "📝 Creating INSTALL.txt..."
cat > "${RELEASE_DIR}/INSTALL.txt" << 'EOF'
PlanetScale Discovery Tools - Installation Instructions
========================================================

Quick Start:
-----------
1. Ensure Python 3.9 or higher is installed:
   python3 --version

2. Run the setup script:
   chmod +x setup.sh
   ./setup.sh

3. Edit sample-config.yaml with your credentials and run discovery:
   ./ps-discovery both --config sample-config.yaml

No virtual environment activation required — the wrapper script
handles it automatically.

For full documentation, see README.md

For cloud provider setup, see docs/providers/ directory
EOF

# Create release notes template
echo "📰 Creating RELEASE_NOTES.txt..."
cat > "${RELEASE_DIR}/RELEASE_NOTES.txt" << EOF
PlanetScale Discovery Tools v${VERSION}
Release Date: $(date +%Y-%m-%d)

## Quick Start

1. Extract the archive:
   \`\`\`
   unzip ps-discovery-${VERSION}.zip
   cd ps-discovery-${VERSION}
   \`\`\`

2. Run the setup script:
   \`\`\`
   chmod +x setup.sh
   ./setup.sh
   \`\`\`

3. Run discovery (see INSTALL.txt for full configuration options):
   \`\`\`
   ./ps-discovery both --config sample-config.yaml
   \`\`\`

## What's New in v${VERSION}

- **CLI renamed**: Command is now \`ps-discovery\` (was \`planetscale-discovery\`)
- **AlloyDB storage discovery**: Storage usage now collected via Cloud Monitoring API
- **Streamlined data collection**: Reduced to only what is needed for migration scoping
- Various bug fixes and reliability improvements

For detailed usage instructions, see README.md
For provider-specific setup, see docs/providers/

Report issues: https://github.com/planetscale/ps-discovery/issues
EOF

# Create zip file
echo "📦 Creating zip file..."
cd dist
zip -r "ps-discovery-${VERSION}.zip" "ps-discovery-${VERSION}"
cd ..

# Calculate checksum
echo "🔐 Generating checksums..."
cd dist
sha256sum "ps-discovery-${VERSION}.zip" > "ps-discovery-${VERSION}.zip.sha256"
cd ..

# Display results
echo ""
echo "✅ Release build complete!"
echo ""
echo "📦 Release artifacts:"
echo "   - dist/ps-discovery-${VERSION}.zip"
echo "   - dist/ps-discovery-${VERSION}.zip.sha256"
echo ""
echo "Zip file size: $(du -h "dist/ps-discovery-${VERSION}.zip" | cut -f1)"
echo ""
echo "To verify:"
echo "  cd dist && sha256sum -c ps-discovery-${VERSION}.zip.sha256"
echo ""
echo "To extract:"
echo "  unzip ps-discovery-${VERSION}.zip"
echo "  cd ps-discovery-${VERSION}"
echo "  ./setup.sh"

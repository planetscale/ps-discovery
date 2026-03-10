#!/bin/bash
# Test setup.sh across different environments
# This script uses Docker or Podman to test the setup script on various Linux distributions

set -e

echo "🧪 PlanetScale Discovery Setup - Multi-Environment Test Suite"
echo "=============================================================="

# Detect container runtime (docker or podman)
if command -v docker &> /dev/null; then
    CONTAINER_CMD="docker"
    echo "Using Docker"
elif command -v podman &> /dev/null; then
    CONTAINER_CMD="podman"
    echo "Using Podman"
elif [ -x "/opt/podman/bin/podman" ]; then
    CONTAINER_CMD="/opt/podman/bin/podman"
    echo "Using Podman (from /opt/podman/bin/podman)"
else
    echo "❌ Error: Neither Docker nor Podman found"
    echo "Please install Docker (https://www.docker.com/) or Podman (https://podman.io/)"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results
PASSED=0
FAILED=0
FAILED_DISTROS=()

# Create temporary test directory
TEST_DIR=$(mktemp -d)
# Don't auto-cleanup so we can inspect logs on failure
# trap "rm -rf $TEST_DIR" EXIT

echo "📦 Preparing test package..."
# Copy package to temp directory (excluding venv and cache)
rsync -a --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' --exclude='discovery_output' . "$TEST_DIR/"

# Test function
test_distro() {
    local distro=$1
    local distro_name=$2
    local python_install_cmd=$3

    echo ""
    echo "================================================"
    echo "Testing: $distro_name"
    echo "================================================"

    # Create Dockerfile
    cat > "$TEST_DIR/Dockerfile.test" <<EOF
FROM $distro

# Install Python and dependencies based on distro
$python_install_cmd

# Copy package
COPY . /app
WORKDIR /app

# Run setup with automatic yes responses
RUN echo -e "n\nn\nn" | bash setup.sh || exit 1

# Verify installation
RUN bash -c "source venv/bin/activate && python3 -m planetscale_discovery --help" || exit 1
EOF

    # Build and run
    if $CONTAINER_CMD build -f "$TEST_DIR/Dockerfile.test" -t ps-discovery-test:$distro_name "$TEST_DIR" > "$TEST_DIR/build-$distro_name.log" 2>&1; then
        echo -e "${GREEN}✅ PASSED: $distro_name${NC}"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo -e "${RED}❌ FAILED: $distro_name${NC}"
        echo "   Log: $TEST_DIR/build-$distro_name.log"
        FAILED=$((FAILED + 1))
        FAILED_DISTROS+=("$distro_name")
        return 1
    fi
}

# Test different distributions
echo ""
echo "Starting tests across multiple distributions..."

# Ubuntu 22.04 (LTS)
test_distro "ubuntu:22.04" "ubuntu-22.04" \
    "RUN apt-get update && apt-get install -y python3 python3-pip python3-venv"

# Ubuntu 24.04 (Latest LTS)
test_distro "ubuntu:24.04" "ubuntu-24.04" \
    "RUN apt-get update && apt-get install -y python3 python3-pip python3-venv"

# Debian 11 (Bullseye)
test_distro "debian:11" "debian-11" \
    "RUN apt-get update && apt-get install -y python3 python3-pip python3-venv"

# Debian 12 (Bookworm)
test_distro "debian:12" "debian-12" \
    "RUN apt-get update && apt-get install -y python3 python3-pip python3-venv"

# Amazon Linux 2023
test_distro "amazonlinux:2023" "amazon-linux-2023" \
    "RUN yum install -y python3 python3-pip && python3 -m ensurepip --upgrade"

# Rocky Linux 9 (RHEL clone)
test_distro "rockylinux:9" "rocky-linux-9" \
    "RUN dnf install -y python3 python3-pip && python3 -m ensurepip --upgrade"

# Alpine Linux (minimal)
test_distro "alpine:3.19" "alpine-3.19" \
    "RUN apk add --no-cache python3 py3-pip bash && python3 -m ensurepip --upgrade"

# Fedora (latest)
test_distro "fedora:39" "fedora-39" \
    "RUN dnf install -y python3 python3-pip"

# Print summary
echo ""
echo "================================================"
echo "Test Summary"
echo "================================================"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "Failed distributions:"
    for distro in "${FAILED_DISTROS[@]}"; do
        echo -e "  ${RED}• $distro${NC}"
        echo "    Log: $TEST_DIR/build-$distro.log"
    done
    echo ""
    echo "Logs preserved in: $TEST_DIR"
fi

echo ""
echo "Test directory: $TEST_DIR"
echo "🎉 Testing complete!"

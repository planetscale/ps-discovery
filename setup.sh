#!/bin/bash
# PlanetScale Discovery Tools - Setup Script
# This script sets up the PlanetScale discovery environment

# Cleanup function for failed installations
cleanup_on_error() {
    echo ""
    echo "❌ Setup failed. Partial installation may exist."
    echo "To clean up and retry, run: rm -rf venv"
    exit 1
}

# Interactive single-choice selector. Renders an arrow-key menu (the UI is drawn
# on stderr) and prints the zero-based index of the chosen option to stdout.
#   idx=$(select_one "Prompt" "Option A" "Option B" ...)
# Callers should only invoke this when stdin/stdout are a TTY; non-interactive
# runs (CI, piping) drive the choice with environment variables instead.
select_one() {
    local prompt="$1"
    shift
    local options=("$@")
    local count=${#options[@]}
    local selected=0 key rest first=1 i

    printf '\e[?25l' >&2  # hide cursor
    while true; do
        if [ "$first" -eq 0 ]; then
            printf '\e[%dA' "$((count + 1))" >&2  # move up to redraw
        fi
        first=0
        printf '%s (use ↑/↓ arrows, Enter to select)\n' "$prompt" >&2
        for i in "${!options[@]}"; do
            if [ "$i" -eq "$selected" ]; then
                printf '\e[36m  ❯ %s\e[0m\e[K\n' "${options[$i]}" >&2
            else
                printf '    %s\e[K\n' "${options[$i]}" >&2
            fi
        done

        IFS= read -rsn1 key
        if [ "$key" = $'\e' ]; then
            # Integer timeout: macOS ships bash 3.2, which rejects fractional -t.
            read -rsn2 -t 1 rest
            key+="$rest"
        fi
        case "$key" in
            $'\e[A' | $'\eOA') selected=$(((selected - 1 + count) % count)) ;;  # up
            $'\e[B' | $'\eOB') selected=$(((selected + 1) % count)) ;;          # down
            '' | $'\n' | $'\r') break ;;                                        # Enter
        esac
    done
    printf '\e[?25h' >&2  # restore cursor
    printf '%s' "$selected"
}

echo "🚀 PlanetScale Discovery Tools Setup"
echo "===================================="
echo "🔧 Validating environment..."

# Check if python3 command exists
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 command not found"
    echo "Please install Python 3.10 or higher from https://www.python.org/downloads/"
    exit 1
fi

# Check Python version
if ! python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>&1); then
    echo "❌ Error: Failed to get Python version"
    echo "Python error: $python_version"
    exit 1
fi

# Parse version (e.g., "3.13" -> major=3, minor=13)
IFS='.' read -r major minor <<< "$python_version"

# Check if Python >= 3.10
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
    echo "❌ Error: Python 3.10 or higher is required"
    exit 1
fi

# Validate directory structure
if [ ! -d "planetscale_discovery" ]; then
    echo "❌ Error: planetscale_discovery/ directory not found"
    echo "This script must be run from the root of the ps-discovery package"
    echo "Current directory: $(pwd)"
    exit 1
fi

if [ ! -f "planetscale_discovery/cli.py" ]; then
    echo "❌ Error: planetscale_discovery/cli.py not found"
    echo "The package structure appears to be incomplete"
    exit 1
fi

# Check available disk space (need at least 500MB for venv and dependencies)
available_space=$(df -k . | tail -1 | awk '{print $4}')
required_space=512000  # 500MB in KB
if [ "$available_space" -lt "$required_space" ]; then
    echo "⚠️  Warning: Low disk space detected"
    echo "Available: $(($available_space / 1024))MB, Recommended: 500MB+"
    echo "Installation may fail if disk space is insufficient"
    read -p "Continue anyway? (y/N): " continue_install
    if [[ ! $continue_install =~ ^[Yy]$ ]]; then
        echo "Setup cancelled"
        exit 0
    fi
fi

# Create virtual environment
if [ ! -d "venv" ]; then
    if ! python3 -m venv venv 2>&1; then
        echo "❌ Error: Failed to create virtual environment"
        echo "This may be due to:"
        echo "  - Missing python3-venv package (try: apt-get install python3-venv)"
        echo "  - Missing ensurepip module (try: python3 -m ensurepip --upgrade)"
        echo "  - Insufficient permissions"
        cleanup_on_error
    fi
else
    # Check if the venv is broken (moved/copied from another location)
    venv_is_broken=false

    if [ -f "venv/bin/python3" ]; then
        # Test if python3 works
        if ! venv/bin/python3 --version &> /dev/null; then
            venv_is_broken=true
        fi
    else
        venv_is_broken=true
    fi

    # Also check if pip exists and works (catches moved venvs)
    if [ "$venv_is_broken" = "false" ]; then
        if [ -f "venv/bin/pip" ]; then
            if ! venv/bin/pip --version &> /dev/null; then
                venv_is_broken=true
            fi
        elif [ -f "venv/bin/pip3" ]; then
            if ! venv/bin/pip3 --version &> /dev/null; then
                venv_is_broken=true
            fi
        else
            # No pip at all means broken or incomplete venv
            venv_is_broken=true
        fi
    fi

    if [ "$venv_is_broken" = "true" ]; then
        echo "⚠️  Existing virtual environment is broken or incomplete; recreating..."
        rm -rf venv
        if ! python3 -m venv venv 2>&1; then
            echo "❌ Error: Failed to recreate virtual environment"
            cleanup_on_error
        fi
    fi
fi

# Activate virtual environment
if ! source venv/bin/activate 2>&1; then
    echo "❌ Error: Failed to activate virtual environment"
    echo "This script requires bash or zsh. Your current shell may not be compatible."
    cleanup_on_error
fi

# Detect which pip command is available in the venv
# Check for venv's pip first, then fall back to using python -m pip
if [ -f "venv/bin/pip" ]; then
    PIP_CMD="venv/bin/pip"
elif [ -f "venv/bin/pip3" ]; then
    PIP_CMD="venv/bin/pip3"
elif python3 -m pip --version &> /dev/null; then
    # Use python -m pip as fallback (ensures we use venv's pip)
    PIP_CMD="python3 -m pip"
else
    echo "❌ Error: pip is not available in the virtual environment"
    echo "This usually means the venv module is not working correctly."
    echo "Try: python3 -m ensurepip --upgrade"
    cleanup_on_error
fi

# Quiet pip down: -qq hides routine progress while still printing real errors,
# and --no-cache-dir avoids the "Cache entry deserialization failed" warnings
# that a stale pip cache emits (not suppressible via -qq). The script prints its
# own per-step status and checks exit codes, so failures are still surfaced.
PIP_FLAGS="-qq --disable-pip-version-check --no-cache-dir"

# Upgrade pip quietly as part of environment validation.
if ! $PIP_CMD install $PIP_FLAGS --upgrade pip 2>&1; then
    echo "❌ Error: Failed to upgrade pip"
    echo "This may be due to:"
    echo "  - Network connectivity issues"
    echo "  - Insufficient disk space"
    echo "  - PyPI service unavailable"
    cleanup_on_error
fi

# Collect every Python dependency and install them in a single pass once the
# engine and provider are chosen. pyyaml is always required.
DEPS=("pyyaml>=6.0.3")
INSTALLED_ENGINES=""

# Database engine selection
echo ""
echo "📋 Database Engine Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Resolve the engine: PSDISCOVERY_ENGINE wins (CI/automation), then an
# interactive arrow-key menu on a TTY, otherwise default to postgres.
engine_choice=""
if [ -n "$PSDISCOVERY_ENGINE" ]; then
    engine_choice="$PSDISCOVERY_ENGINE"
elif [ -t 0 ] && [ -t 1 ]; then
    engine_idx=$(select_one "Which database are you discovering?" "PostgreSQL" "MySQL")
    case "$engine_idx" in
        1) engine_choice="mysql" ;;
        *) engine_choice="postgres" ;;
    esac
else
    engine_choice="postgres"
fi

if [ "$engine_choice" = "mysql" ]; then
    DEPS+=("PyMySQL>=1.1.0")
    INSTALLED_ENGINES="mysql"
else
    DEPS+=("psycopg2-binary>=2.9.11")
    INSTALLED_ENGINES="postgres"
fi

echo ""
echo "📋 Cloud Provider Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Track which cloud provider was selected
INSTALLED_PROVIDERS=""

# Append the chosen provider's dependencies to DEPS and record the selection.
add_provider_deps() {
    local choice=$1
    case "$choice" in
        aws)
            DEPS+=("boto3>=1.40.49" "botocore>=1.40.49")
            INSTALLED_PROVIDERS="aws"
            ;;
        gcp)
            DEPS+=("google-cloud-resource-manager>=1.14.2" "google-cloud-compute>=1.39.0" "google-cloud-monitoring>=2.27.2" "google-cloud-alloydb>=0.5.0" "google-auth>=2.38.0" "google-api-python-client>=2.184.0")
            INSTALLED_PROVIDERS="gcp"
            ;;
        supabase)
            DEPS+=("requests>=2.32.5")
            INSTALLED_PROVIDERS="supabase"
            ;;
        heroku)
            DEPS+=("requests>=2.32.5")
            INSTALLED_PROVIDERS="heroku"
            ;;
        neon)
            DEPS+=("requests>=2.32.5")
            INSTALLED_PROVIDERS="neon"
            ;;
        none | None | NONE | "")
            : # self-managed / direct connection — no extra dependencies
            ;;
        *)
            echo "⚠️  Ignoring unrecognized choice: $choice"
            ;;
    esac
}

# Resolve the provider: PSDISCOVERY_PROVIDER wins (CI/automation), then an
# interactive arrow-key menu on a TTY, otherwise default to none.
provider_choice=""
if [ -n "$PSDISCOVERY_PROVIDER" ]; then
    provider_choice="$PSDISCOVERY_PROVIDER"
elif [ -t 0 ] && [ -t 1 ]; then
    provider_idx=$(select_one "Which hosting provider do you want to scan?" \
        "AWS (RDS / Aurora)" \
        "GCP (Cloud SQL / AlloyDB)" \
        "Supabase" \
        "Heroku Postgres" \
        "Neon" \
        "None (self-managed / direct connection)")
    case "$provider_idx" in
        0) provider_choice="aws" ;;
        1) provider_choice="gcp" ;;
        2) provider_choice="supabase" ;;
        3) provider_choice="heroku" ;;
        4) provider_choice="neon" ;;
        *) provider_choice="none" ;;
    esac
else
    provider_choice="none"
fi

add_provider_deps "$provider_choice"

# Install the engine, provider, and core dependencies in a single pass.
echo ""
echo "📥 Installing dependencies (this can take a minute)..."
if ! $PIP_CMD install $PIP_FLAGS "${DEPS[@]}" 2>&1; then
    echo "❌ Error: Failed to install dependencies"
    echo "This may be due to:"
    echo "  - Network connectivity issues"
    echo "  - A package version not being available for your platform/Python"
    cleanup_on_error
fi

# Create main module entry point
if [ ! -f "planetscale_discovery/__main__.py" ]; then
    if ! cat > planetscale_discovery/__main__.py << 'EOF'
#!/usr/bin/env python3
"""
PlanetScale Discovery Tools - Package Entry Point
"""
from .cli import main

if __name__ == "__main__":
    main()
EOF
    then
        echo "❌ Error: Failed to create __main__.py entry point"
        echo "This may be due to insufficient write permissions"
        cleanup_on_error
    fi
fi

# Create wrapper script
cat > ps-discovery << 'WRAPPER_EOF'
#!/usr/bin/env bash
# PlanetScale Discovery Tools - Wrapper Script
# This script runs the discovery tool using the virtual environment
# without requiring manual activation.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Error: Virtual environment not found."
    echo "Please run ./setup.sh first to set up the environment."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    echo "Error: Python not found in virtual environment."
    echo "The virtual environment may be corrupted. Please run:"
    echo "  rm -rf venv && ./setup.sh"
    exit 1
fi

exec "$SCRIPT_DIR/venv/bin/python3" -m planetscale_discovery "$@"
WRAPPER_EOF
chmod +x ps-discovery

# Test installation
test_output=$(./ps-discovery --help 2>&1)
if [ $? -ne 0 ]; then
    echo "❌ Installation test failed"
    echo ""
    echo "Error output:"
    echo "$test_output"
    echo ""
    echo "This may be due to:"
    echo "  - Missing dependencies"
    echo "  - Import errors in the package"
    echo "  - Corrupted package files"
    cleanup_on_error
fi

# Generate customized configuration based on installed providers
# Build the config-template command with providers and engines
ENGINES_CSV=$(echo "$INSTALLED_ENGINES" | tr ' ' ',' | sed 's/^,//')
# Default to postgres if no engines selected
if [ -z "$ENGINES_CSV" ]; then
    ENGINES_CSV="postgres"
fi

# Write config.yaml so `./ps-discovery` finds it automatically. Never clobber
# an existing config.yaml — write new-config.yaml instead. The .yaml suffix
# matters: config-template and the loader both pick format by file extension.
CONFIG_FILE="config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="new-config.yaml"
    echo "⚠️  config.yaml already exists — writing $CONFIG_FILE to preserve it."
fi

CMD="./ps-discovery config-template --output $CONFIG_FILE --engines $ENGINES_CSV"
if [ -n "$INSTALLED_PROVIDERS" ]; then
    PROVIDERS_CSV=$(echo "$INSTALLED_PROVIDERS" | tr ' ' ',' | sed 's/^,//')
    CMD="$CMD --providers $PROVIDERS_CSV"
fi
config_output=$($CMD 2>&1)

if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to generate sample configuration"
    echo ""
    echo "Error output:"
    echo "$config_output"
    echo ""
    echo "This may be due to:"
    echo "  - Import errors in the package"
    echo "  - Insufficient write permissions"
    cleanup_on_error
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Next Steps:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1️⃣  Edit $CONFIG_FILE with your credentials:"
echo ""
if [[ $INSTALLED_ENGINES == *"postgres"* ]]; then
    echo "   PostgreSQL (database section):"
    echo "     • host, port, database name, username, and password"
fi
if [[ $INSTALLED_ENGINES == *"mysql"* ]]; then
    if [[ $INSTALLED_ENGINES == *"postgres"* ]]; then
        echo ""
    fi
    echo "   MySQL (mysql section):"
    echo "     • host, port, username, and password"
    echo "     • Leave database empty to discover all databases"
fi
echo ""

if [ -n "$INSTALLED_PROVIDERS" ]; then
    echo "   Cloud providers (you selected: $INSTALLED_PROVIDERS):"
    if [[ $INSTALLED_PROVIDERS == *"aws"* ]]; then
        echo "     • AWS: Set your AWS profile or access keys"
    fi
    if [[ $INSTALLED_PROVIDERS == *"gcp"* ]]; then
        echo "     • GCP: Set project_id and service account key path"
    fi
    if [[ $INSTALLED_PROVIDERS == *"supabase"* ]]; then
        echo "     • Supabase: Set your access token"
    fi
    if [[ $INSTALLED_PROVIDERS == *"heroku"* ]]; then
        echo "     • Heroku: Set your API key or HEROKU_API_KEY env var"
    fi
    if [[ $INSTALLED_PROVIDERS == *"neon"* ]]; then
        echo "     • Neon: Set your API key or NEON_API_KEY env var"
    fi
    echo ""
fi

echo "2️⃣  Run discovery:"
echo ""

if [ "$CONFIG_FILE" = "config.yaml" ]; then
    echo "   ./ps-discovery"
else
    echo "   # Review $CONFIG_FILE, then either rename it to config.yaml or"
    echo "   # point at it explicitly:"
    echo "   ./ps-discovery --config $CONFIG_FILE"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📖 For detailed setup instructions, see README.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
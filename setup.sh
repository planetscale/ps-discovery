#!/bin/bash
# PlanetScale Discovery Tools - Setup Script
# This script sets up the PlanetScale discovery environment

# Cleanup function for failed installations
cleanup_on_error() {
    echo ""
    echo "❌ Setup failed. Partial installation may exist."
    echo "To clean up and retry, run: rm -rf venv sample-config.yaml"
    exit 1
}

echo "🚀 PlanetScale Discovery Tools Setup"
echo "===================================="

# Check if python3 command exists
echo "📋 Checking for Python 3..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 command not found"
    echo "Please install Python 3.9 or higher from https://www.python.org/downloads/"
    exit 1
fi

# Check Python version
echo "📋 Checking Python version..."
if ! python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>&1); then
    echo "❌ Error: Failed to get Python version"
    echo "Python error: $python_version"
    exit 1
fi
echo "Python version: $python_version"

# Parse version (e.g., "3.13" -> major=3, minor=13)
IFS='.' read -r major minor <<< "$python_version"

# Check if Python >= 3.9
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
    echo "❌ Error: Python 3.9 or higher is required"
    exit 1
fi

# Validate directory structure
echo "📋 Validating directory structure..."
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
echo "✅ Directory structure validated"

# Check available disk space (need at least 500MB for venv and dependencies)
echo "📋 Checking available disk space..."
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
echo "📦 Creating virtual environment..."
if [ ! -d "venv" ]; then
    if ! python3 -m venv venv 2>&1; then
        echo "❌ Error: Failed to create virtual environment"
        echo "This may be due to:"
        echo "  - Missing python3-venv package (try: apt-get install python3-venv)"
        echo "  - Missing ensurepip module (try: python3 -m ensurepip --upgrade)"
        echo "  - Insufficient permissions"
        cleanup_on_error
    fi
    echo "✅ Virtual environment created"
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
        echo "⚠️  Existing virtual environment is broken or incomplete"
        echo "Recreating virtual environment..."
        rm -rf venv
        if ! python3 -m venv venv 2>&1; then
            echo "❌ Error: Failed to recreate virtual environment"
            cleanup_on_error
        fi
        echo "✅ Virtual environment recreated"
    else
        echo "✅ Virtual environment already exists"
    fi
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
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

# Install dependencies
echo "📥 Installing dependencies..."
if ! $PIP_CMD install --upgrade pip 2>&1; then
    echo "❌ Error: Failed to upgrade pip"
    echo "This may be due to:"
    echo "  - Network connectivity issues"
    echo "  - Insufficient disk space"
    echo "  - PyPI service unavailable"
    cleanup_on_error
fi

# Install core dependencies
echo "Installing core dependencies..."
if ! $PIP_CMD install "pyyaml>=6.0.3" "psycopg2-binary>=2.9.11" 2>&1; then
    echo "❌ Error: Failed to install core dependencies (pyyaml, psycopg2-binary)"
    echo "This may be due to:"
    echo "  - Network connectivity issues"
    echo "  - Package version not available"
    echo "  - Compilation errors (for psycopg2-binary)"
    cleanup_on_error
fi

# Track which cloud providers were installed
INSTALLED_PROVIDERS=""

# Optional: Install AWS dependencies
echo ""
read -p "Do you need AWS RDS/Aurora discovery? (y/N): " install_aws
if [[ $install_aws =~ ^[Yy]$ ]]; then
    echo "Installing AWS dependencies..."
    if ! $PIP_CMD install "boto3>=1.40.49" "botocore>=1.40.49" 2>&1; then
        echo "❌ Error: Failed to install AWS dependencies (boto3, botocore)"
        echo "This may be due to:"
        echo "  - Network connectivity issues"
        echo "  - Package version not available"
        cleanup_on_error
    fi
    echo "✅ AWS dependencies installed"
    INSTALLED_PROVIDERS="$INSTALLED_PROVIDERS aws"
else
    echo "⏭️  Skipping AWS dependencies"
fi

# Optional: Install GCP dependencies
echo ""
read -p "Do you need GCP Cloud SQL/AlloyDB discovery? (y/N): " install_gcp
if [[ $install_gcp =~ ^[Yy]$ ]]; then
    echo "Installing GCP dependencies..."
    if ! $PIP_CMD install "google-cloud-resource-manager>=1.14.2" "google-cloud-compute>=1.39.0" "google-cloud-monitoring>=2.27.2" "google-cloud-alloydb>=0.5.0" "google-auth>=2.38.0" "google-api-python-client>=2.184.0" 2>&1; then
        echo "❌ Error: Failed to install GCP dependencies"
        echo "This may be due to:"
        echo "  - Network connectivity issues"
        echo "  - Package version not available"
        cleanup_on_error
    fi
    echo "✅ GCP dependencies installed"
    INSTALLED_PROVIDERS="$INSTALLED_PROVIDERS gcp"
else
    echo "⏭️  Skipping GCP dependencies"
fi

# Optional: Install Supabase dependencies
echo ""
read -p "Do you need Supabase managed PostgreSQL discovery? (y/N): " install_supabase
if [[ $install_supabase =~ ^[Yy]$ ]]; then
    echo "Installing Supabase dependencies..."
    if ! $PIP_CMD install "requests>=2.32.5" 2>&1; then
        echo "❌ Error: Failed to install Supabase dependencies (requests)"
        echo "This may be due to:"
        echo "  - Network connectivity issues"
        echo "  - Package version not available"
        cleanup_on_error
    fi
    echo "✅ Supabase dependencies installed"
    INSTALLED_PROVIDERS="$INSTALLED_PROVIDERS supabase"
else
    echo "⏭️  Skipping Supabase dependencies"
fi

# Optional: Install Heroku dependencies
echo ""
read -p "Do you need Heroku Postgres discovery? (y/N): " install_heroku
if [[ $install_heroku =~ ^[Yy]$ ]]; then
    echo "Installing Heroku dependencies..."
    if ! $PIP_CMD install "requests>=2.32.5" 2>&1; then
        echo "❌ Error: Failed to install Heroku dependencies (requests)"
        echo "This may be due to:"
        echo "  - Network connectivity issues"
        echo "  - Package version not available"
        cleanup_on_error
    fi
    echo "✅ Heroku dependencies installed"
    INSTALLED_PROVIDERS="$INSTALLED_PROVIDERS heroku"
else
    echo "⏭️  Skipping Heroku dependencies"
fi

# Create main module entry point
echo "🔧 Setting up package structure..."
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
    echo "✅ Main module entry point created"
else
    echo "✅ Main module entry point already exists"
fi

# Create wrapper script
echo "🔧 Creating ps-discovery wrapper script..."
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
echo "✅ Wrapper script created"

# Test installation
echo "🧪 Testing installation..."
test_output=$(./ps-discovery --help 2>&1)
if [ $? -eq 0 ]; then
    echo "✅ Installation test passed"
else
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
echo "📝 Generating customized configuration..."

# Build the config-template command with providers if any were installed
if [ -n "$INSTALLED_PROVIDERS" ]; then
    # Convert space-separated list to comma-separated
    PROVIDERS_CSV=$(echo "$INSTALLED_PROVIDERS" | tr ' ' ',' | sed 's/^,//')
    config_output=$(./ps-discovery config-template --output sample-config.yaml --providers "$PROVIDERS_CSV" 2>&1)
else
    # No cloud providers, generate database-only config
    config_output=$(./ps-discovery config-template --output sample-config.yaml 2>&1)
fi

if [ $? -eq 0 ]; then
    echo "✅ Sample configuration saved to: sample-config.yaml"
else
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
echo "1️⃣  Edit sample-config.yaml with your credentials:"
echo ""
echo "   Required (database section):"
echo "     • host, port, database name"
echo "     • username and password"
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
    echo ""
fi

echo "2️⃣  Run discovery:"
echo ""

if [ -z "$INSTALLED_PROVIDERS" ]; then
    echo "   # Database discovery only"
    echo "   ./ps-discovery database --config sample-config.yaml"
else
    echo "   # Database and cloud discovery"
    echo "   ./ps-discovery both --config sample-config.yaml"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📖 For detailed setup instructions, see README.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
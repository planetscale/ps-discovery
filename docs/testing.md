# Testing Setup Script Across Environments

This document describes how to test the `setup.sh` script across different Linux distributions and environments.

## Quick Test with Docker

We provide a test script that automatically tests setup.sh across multiple Linux distributions:

```bash
./test-setup.sh
```

This will test against:
- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- Debian 11 (Bullseye)
- Debian 12 (Bookworm)
- Amazon Linux 2023
- Rocky Linux 9 (RHEL clone)
- Alpine Linux 3.19
- Fedora 39

## Manual Testing with Docker

To test a specific distribution manually:

### Ubuntu 22.04

```bash
docker run -it --rm -v $(pwd):/app ubuntu:22.04 bash
cd /app
apt-get update && apt-get install -y python3 python3-pip python3-venv
./setup.sh
```

### Amazon Linux 2023

```bash
docker run -it --rm -v $(pwd):/app amazonlinux:2023 bash
cd /app
yum install -y python3 python3-pip
python3 -m ensurepip --upgrade
./setup.sh
```

### Alpine Linux

```bash
docker run -it --rm -v $(pwd):/app alpine:3.19 bash
cd /app
apk add --no-cache python3 py3-pip bash
python3 -m ensurepip --upgrade
./setup.sh
```

## Testing Edge Cases

### Test with no pip installed

```bash
docker run -it --rm -v $(pwd):/app python:3.11-slim bash
cd /app
# Don't install pip, test if setup.sh detects it
./setup.sh
```

### Test with low disk space

```bash
docker run -it --rm -v $(pwd):/app --storage-opt size=200M ubuntu:22.04 bash
cd /app
apt-get update && apt-get install -y python3 python3-venv
./setup.sh
```

### Test with broken venv

```bash
# Create venv, move it, then try setup again
docker run -it --rm -v $(pwd):/app ubuntu:22.04 bash
cd /app
apt-get update && apt-get install -y python3 python3-venv
python3 -m venv /tmp/venv
mv /tmp/venv ./venv
./setup.sh  # Should detect broken venv and recreate
```

### Test without python3-venv package

```bash
docker run -it --rm -v $(pwd):/app python:3.11-slim bash
cd /app
# python3-venv not installed by default
./setup.sh
```

## Testing on Real EC2 Instances

For comprehensive testing on actual AWS infrastructure:

### Amazon Linux 2023

```bash
# Launch instance
aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --instance-type t3.micro \
  --key-name your-key \
  --security-group-ids sg-xxxxx \
  --subnet-id subnet-xxxxx

# SSH in and test
scp -r . ec2-user@instance-ip:~/ps-discovery/
ssh ec2-user@instance-ip
cd ps-discovery
./setup.sh
```

### Ubuntu 22.04

```bash
# Launch instance
aws ec2 run-instances \
  --image-id ami-xxxxx \  # Ubuntu 22.04 AMI
  --instance-type t3.micro \
  --key-name your-key

# SSH in and test
scp -r . ubuntu@instance-ip:~/ps-discovery/
ssh ubuntu@instance-ip
cd ps-discovery
./setup.sh
```

## Testing Different Python Versions

```bash
# Python 3.9
docker run -it --rm -v $(pwd):/app python:3.9-slim bash
cd /app
./setup.sh

# Python 3.10
docker run -it --rm -v $(pwd):/app python:3.10-slim bash
cd /app
./setup.sh

# Python 3.11
docker run -it --rm -v $(pwd):/app python:3.11-slim bash
cd /app
./setup.sh

# Python 3.12
docker run -it --rm -v $(pwd):/app python:3.12-slim bash
cd /app
./setup.sh

# Python 3.13
docker run -it --rm -v $(pwd):/app python:3.13-slim bash
cd /app
./setup.sh
```

## Automated CI Testing

Consider adding GitHub Actions workflow:

```yaml
name: Test Setup Script

on:
  push:
    paths:
      - 'setup.sh'
      - '.github/workflows/test-setup.yml'

jobs:
  test:
    strategy:
      matrix:
        os:
          - ubuntu:22.04
          - ubuntu:24.04
          - debian:11
          - debian:12
          - amazonlinux:2023
          - rockylinux:9
          - alpine:3.19
    runs-on: ubuntu-latest
    container:
      image: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v3
      - name: Install Python
        run: |
          if command -v apt-get &> /dev/null; then
            apt-get update && apt-get install -y python3 python3-pip python3-venv
          elif command -v yum &> /dev/null; then
            yum install -y python3 python3-pip
          elif command -v apk &> /dev/null; then
            apk add --no-cache python3 py3-pip bash
          fi
      - name: Run setup
        run: echo -e "n\nn\nn" | bash setup.sh
      - name: Test installation
        run: |
          source venv/bin/activate
          python3 -m planetscale_discovery --version
```

## Common Issues to Test For

1. **Missing python3-venv package** (Debian/Ubuntu minimal installs)
2. **Missing pip** (some Python installations)
3. **No ensurepip** (some minimal Python builds)
4. **Broken venv from moved directory** (tarball extracted to different location)
5. **Low disk space** (small EC2 instances or containers)
6. **Network issues** (pip install failures)
7. **Permission issues** (trying to write to read-only filesystem)
8. **Shell compatibility** (bash vs zsh vs dash)
9. **Python version too old** (< 3.9)
10. **Missing dependencies** (psycopg2-binary compilation issues)

## Success Criteria

A successful test should:
1. Complete setup without errors
2. Create functional venv
3. Install all required dependencies
4. Generate sample-config.yaml
5. Pass `python3 -m planetscale_discovery --help` test
6. Provide helpful error messages if anything fails

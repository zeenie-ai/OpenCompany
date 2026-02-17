#!/usr/bin/env bash
# MachinaOS Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/trohitg/MachinaOS/main/install.sh | bash
#
# This script installs MachinaOS and its dependencies:
# - Node.js 22+ (via brew/apt/dnf/pacman)
# - Python 3.12+ (via brew/apt/dnf/pacman)
# - uv (Python package manager)

set -e

MIN_NODE_VERSION=22
MIN_PYTHON_VERSION_MINOR=12

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error_exit() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Banner
echo ""
echo -e "${CYAN}  __  __            _     _             ___  ____  ${NC}"
echo -e "${CYAN} |  \\/  | __ _  ___| |__ (_)_ __   __ _/ _ \\/ ___| ${NC}"
echo -e "${CYAN} | |\\/| |/ _\` |/ __| '_ \\| | '_ \\ / _\` | | | \\___ \\ ${NC}"
echo -e "${CYAN} | |  | | (_| | (__| | | | | | | | (_| | |_| |___) |${NC}"
echo -e "${CYAN} |_|  |_|\\__,_|\\___|_| |_|_|_| |_|\\__,_|\\___/|____/ ${NC}"
echo ""
echo "Open-source workflow automation with AI agents"
echo ""

# Detect OS
detect_os() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "macos"
  elif [[ -f /etc/debian_version ]]; then
    echo "debian"
  elif [[ -f /etc/redhat-release ]]; then
    echo "redhat"
  elif [[ -f /etc/arch-release ]]; then
    echo "arch"
  else
    echo "unknown"
  fi
}

OS=$(detect_os)

# =============================================================================
# Dependency Checks and Installation
# =============================================================================

check_node() {
  if command -v node &> /dev/null; then
    version=$(node --version | tr -d 'v')
    major=$(echo "$version" | cut -d. -f1)
    if [ "$major" -ge "$MIN_NODE_VERSION" ]; then
      success "Node.js v$version"
      return 0
    fi
    warn "Node.js v$version is too old (need v$MIN_NODE_VERSION+)"
  fi
  return 1
}

install_node() {
  info "Installing Node.js $MIN_NODE_VERSION..."

  case "$OS" in
    macos)
      if command -v brew &> /dev/null; then
        brew install node@22
      else
        error_exit "Please install Homebrew first: https://brew.sh/"
      fi
      ;;
    debian)
      curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
      sudo apt-get install -y nodejs
      ;;
    redhat)
      curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
      sudo dnf install -y nodejs
      ;;
    arch)
      sudo pacman -S --noconfirm nodejs npm
      ;;
    *)
      error_exit "Please install Node.js 22+ manually from https://nodejs.org/"
      ;;
  esac

  if ! check_node; then
    error_exit "Failed to install Node.js. Please install manually."
  fi
}

check_python() {
  for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
      version=$($cmd --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
      major=$(echo "$version" | cut -d. -f1)
      minor=$(echo "$version" | cut -d. -f2)
      if [ "$major" -ge 3 ] && [ "$minor" -ge "$MIN_PYTHON_VERSION_MINOR" ]; then
        success "Python $version ($cmd)"
        PYTHON_CMD="$cmd"
        return 0
      fi
    fi
  done
  warn "Python 3.$MIN_PYTHON_VERSION_MINOR+ not found"
  return 1
}

install_python() {
  info "Installing Python 3.$MIN_PYTHON_VERSION_MINOR..."

  case "$OS" in
    macos)
      if command -v brew &> /dev/null; then
        brew install python@3.12
      else
        error_exit "Please install Homebrew first: https://brew.sh/"
      fi
      ;;
    debian)
      sudo apt-get update
      sudo apt-get install -y python3.12 python3.12-venv python3-pip
      ;;
    redhat)
      sudo dnf install -y python3.12
      ;;
    arch)
      sudo pacman -S --noconfirm python python-pip
      ;;
    *)
      error_exit "Please install Python 3.12+ manually from https://python.org/"
      ;;
  esac

  if ! check_python; then
    error_exit "Failed to install Python. Please install manually."
  fi
}

check_uv() {
  if command -v uv &> /dev/null; then
    version=$(uv --version | tr -d 'uv ')
    success "uv $version"
    return 0
  fi
  return 1
}

install_uv() {
  info "Installing uv (Python package manager)..."

  # Try pip first
  if [ -n "$PYTHON_CMD" ]; then
    if $PYTHON_CMD -m pip install uv 2>/dev/null; then
      export PATH="$HOME/.local/bin:$PATH"
      if check_uv; then return 0; fi
    fi
  fi

  # Fallback to official installer
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"

  if ! check_uv; then
    error_exit "Failed to install uv"
  fi
}

# =============================================================================
# Main Installation Flow
# =============================================================================

main() {
  echo ""
  info "Checking dependencies..."
  echo ""

  # Check and install dependencies
  check_node || install_node
  check_python || install_python
  check_uv || install_uv

  echo ""
  info "Installing MachinaOS..."
  echo ""

  # Install machinaos from npm
  npm install -g machinaos

  echo ""
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN}  MachinaOS installed successfully!${NC}"
  echo -e "${GREEN}============================================${NC}"
  echo ""
  echo "  Start MachinaOS:"
  echo "    machinaos start"
  echo ""
  echo "  Open in browser:"
  echo "    http://localhost:3000"
  echo ""
}

# Run main
main

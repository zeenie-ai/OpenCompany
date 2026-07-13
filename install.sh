#!/usr/bin/env bash
# OpenCompany Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/install.sh | bash
#
# This script installs OpenCompany and its dependencies:
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
echo -e "${CYAN}  OpenCompany${NC}"
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

# Detect WSL
is_wsl() {
  [[ -n "$WSL_DISTRO_NAME" ]] || [[ -n "$WSL_INTEROP" ]] || grep -qi microsoft /proc/version 2>/dev/null
}

# Configure npm for WSL (fix nvm conflicts and Windows paths)
setup_wsl_npm() {
  if ! is_wsl; then
    return 0
  fi

  info "WSL detected: Checking npm configuration..."

  # If using nvm, remove any conflicting prefix from .npmrc
  if [[ -n "$NVM_DIR" ]] || [[ -d "$HOME/.nvm" ]]; then
    if grep -q '^prefix=' "$HOME/.npmrc" 2>/dev/null; then
      info "Removing conflicting npm prefix (nvm detected)..."
      sed -i '/^prefix=/d' "$HOME/.npmrc"
      # Also remove globalconfig if present
      sed -i '/^globalconfig=/d' "$HOME/.npmrc" 2>/dev/null || true
    fi

    # Source nvm to ensure proper paths
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [[ -s "$NVM_DIR/nvm.sh" ]]; then
      source "$NVM_DIR/nvm.sh"
    fi

    success "npm configured for nvm on WSL"
    return 0
  fi

  # No nvm - check if npm is using Windows path
  local npm_prefix
  npm_prefix=$(npm config get prefix 2>/dev/null || echo "")

  if [[ "$npm_prefix" == /mnt/* ]]; then
    info "Configuring npm to use Linux-native path..."
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    export PATH="$HOME/.npm-global/bin:$PATH"

    # Add to .bashrc if not already there
    if ! grep -q 'npm-global' "$HOME/.bashrc" 2>/dev/null; then
      echo '' >> "$HOME/.bashrc"
      echo '# npm global packages (WSL)' >> "$HOME/.bashrc"
      echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.bashrc"
      info "Added npm-global to PATH in ~/.bashrc"
    fi

    success "npm configured for WSL"
  fi
}

# The pre-rebrand package owns the deprecated `machina` binary too. Remove it
# before installing OpenCompany so npm does not fail with an EEXIST shim clash.
remove_legacy_machinaos() {
  if ! npm list -g --depth=0 machinaos &> /dev/null; then
    return 0
  fi

  info "Removing legacy machinaos package..."
  if npm uninstall -g machinaos &> /dev/null; then
    success "Legacy machinaos package removed"
  elif command -v sudo &> /dev/null; then
    info "Retrying legacy package removal with sudo..."
    sudo npm uninstall -g machinaos
    success "Legacy machinaos package removed"
  else
    error_exit "Unable to remove legacy machinaos. Try: sudo npm uninstall -g machinaos"
  fi
}

# =============================================================================
# Dependency Checks and Installation
# =============================================================================

check_node() {
  # Clear command hash to ensure we find the latest node binary
  hash -r 2>/dev/null || true

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
        # Add node@22 to PATH (brew doesn't link it by default)
        export PATH="/opt/homebrew/opt/node@22/bin:/usr/local/opt/node@22/bin:$PATH"
      else
        error_exit "Please install Homebrew first: https://brew.sh/"
      fi
      ;;
    debian)
      curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
      sudo apt-get install -y nodejs
      # Force rehash PATH to find newly installed node
      hash -r 2>/dev/null || true
      # Source profile to update PATH if needed
      [ -f /etc/profile ] && source /etc/profile 2>/dev/null || true
      ;;
    redhat)
      curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
      sudo dnf install -y nodejs
      hash -r 2>/dev/null || true
      ;;
    arch)
      sudo pacman -S --noconfirm nodejs npm
      hash -r 2>/dev/null || true
      ;;
    *)
      error_exit "Please install Node.js 22+ manually from https://nodejs.org/"
      ;;
  esac

  # Clear hash and verify using full path as fallback
  hash -r 2>/dev/null || true

  # Check using direct path first (NodeSource installs to /usr/bin/node)
  if [ -x /usr/bin/node ]; then
    version=$(/usr/bin/node --version | tr -d 'v')
    major=$(echo "$version" | cut -d. -f1)
    if [ "$major" -ge "$MIN_NODE_VERSION" ]; then
      success "Node.js v$version installed"
      return 0
    fi
  fi

  if ! check_node; then
    error_exit "Failed to install Node.js. Please install manually."
  fi
}

check_python() {
  for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
      version=$($cmd --version 2>&1 | sed -n 's/.*Python \([0-9]*\.[0-9]*\).*/\1/p')
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

  # Configure npm for WSL before installing
  setup_wsl_npm

  # Avoid a collision with the deprecated `machina` compatibility shim.
  remove_legacy_machinaos

  echo ""
  info "Installing OpenCompany..."
  echo ""

  # Install OpenCompany from npm
  # On Linux/WSL without nvm, global npm install needs sudo unless prefix is user-writable
  if npm install -g '@zeenie/opencompany' 2>/dev/null; then
    : # Installed successfully
  elif command -v sudo &> /dev/null; then
    info "Retrying with sudo..."
    sudo npm install -g '@zeenie/opencompany'
  else
    error_exit "npm install -g failed. Try: sudo npm install -g @zeenie/opencompany"
  fi

  echo ""
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN}  OpenCompany installed successfully!${NC}"
  echo -e "${GREEN}============================================${NC}"
  echo ""
  echo "  Start OpenCompany:"
  echo "    company start"
  echo ""
  echo "  Open in browser:"
  echo "    http://localhost:3000"
  echo ""
  echo "  Optional: Enable JS-rendered web scraping:"
  echo "    playwright install chromium"
  echo ""
  echo "  For development from source, install pnpm:"
  echo "    npm install -g pnpm"
  echo ""
  echo "  Run diagnostics:"
  echo "    company doctor"
  echo ""
}

# Run main
main

#!/usr/bin/env bash
# OpenCompany Uninstaller
#
# Usage: curl -fsSL https://raw.githubusercontent.com/zeenie-ai/OpenCompany/main/uninstall.sh | bash

set -e

echo "Uninstalling OpenCompany..."
echo ""

# Remove only OpenCompany's scoped package and the official pre-rebrand
# package. The unrelated unscoped package named `opencompany` is untouched.
remove_global_package() {
  local package_name="$1"
  local display_name="$2"

  if ! npm list -g --depth=0 "$package_name" &> /dev/null; then
    echo "$display_name not installed"
    return 0
  fi

  if npm uninstall -g "$package_name" &> /dev/null; then
    :
  elif command -v sudo &> /dev/null; then
    echo "Retrying $display_name removal with sudo..."
    sudo npm uninstall -g "$package_name"
  else
    echo "Unable to remove $display_name. Try: sudo npm uninstall -g $package_name" >&2
    exit 1
  fi

  echo "$display_name removed"
}

remove_global_package '@zeenie/opencompany' '@zeenie/opencompany'
remove_global_package 'machinaos' 'legacy machinaos package'

echo ""
echo "Done!"
echo ""

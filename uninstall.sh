#!/usr/bin/env bash
# OpenCompany Uninstaller
#
# Usage: curl -fsSL https://raw.githubusercontent.com/zeenie-ai/MachinaOS/main/uninstall.sh | bash

set -e

echo "Uninstalling OpenCompany..."
echo ""

# Uninstall the current package. Also remove the pre-rebrand package when it
# is still installed so upgrades do not leave two global CLI shims behind.
if npm list -g opencompany &> /dev/null; then
  npm uninstall -g opencompany
  echo "opencompany removed"
else
  echo "opencompany not installed"
fi

if npm list -g machinaos &> /dev/null; then
  npm uninstall -g machinaos
  echo "legacy machinaos package removed"
fi

echo ""
echo "Done!"
echo ""

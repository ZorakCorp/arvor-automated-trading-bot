#!/bin/bash
# Push main branch to all configured push URLs on origin.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Pushing to all remotes..."
git push origin main
echo "Done."

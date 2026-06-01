#!/bin/bash
set -euo pipefail
cd /app
pip install -q -r requirements.txt
python -m unittest tests.test_bot -v
python scripts/smoke_test.py
echo "All tests passed."

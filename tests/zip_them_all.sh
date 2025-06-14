#!/bin/bash

set -euo pipefail

for dir in */; do
  dirname="${dir%/}"  # remove trailing slash
  if [ -d "$dirname" ]; then
    zip -r "${dirname}.zip" "$dirname"
  fi
done

echo "✅ All subdirectories zipped."


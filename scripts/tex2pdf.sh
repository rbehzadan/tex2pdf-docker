#!/bin/bash

set -euo pipefail

# Configuration
API_BASE="http://localhost:8000/tex2pdf"

# Check input
if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <latex_project_folder>"
    exit 1
fi

PROJECT_DIR="$1"
if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "❌ Error: '$PROJECT_DIR' is not a directory"
    exit 1
fi

BASENAME=$(basename "$PROJECT_DIR")
ZIP_FILE="${BASENAME}.zip"

# Backup if zip file exists
if [[ -f "$ZIP_FILE" ]]; then
    i=1
    while [[ -f "${ZIP_FILE}.bak$i" ]]; do
        ((i++))
    done
    echo "⚠️  Found existing '$ZIP_FILE'. Backing it up as '${ZIP_FILE}.bak$i'"
    mv "$ZIP_FILE" "${ZIP_FILE}.bak$i"
fi

# Create zip
echo "📦 Creating zip archive '$ZIP_FILE'..."
zip -r "$ZIP_FILE" "$PROJECT_DIR" > /dev/null

# Upload
echo "⬆️  Uploading $ZIP_FILE ..."
JOB_RESPONSE=$(curl -s -X POST -F "zip_file=@${ZIP_FILE}" "${API_BASE}")
JOB_ID=$(echo "$JOB_RESPONSE" | jq -r '.job_id')

echo "🆔 Job ID: $JOB_ID"

# Poll for status
echo "⏳ Polling job status..."
while true; do
    STATUS_RESPONSE=$(curl -s "${API_BASE}/status/${JOB_ID}")
    STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status')

    echo "Status: $STATUS"
    if [[ "$STATUS" == "completed" ]]; then
        break
    elif [[ "$STATUS" == "error" ]]; then
        echo "❌ Job failed"
        echo "$STATUS_RESPONSE"
        exit 1
    fi
    sleep 1
done

# Download PDF
PDF_FILE="${BASENAME}.pdf"
echo "⬇️  Downloading PDF to '$PDF_FILE'..."
curl -s -o "$PDF_FILE" "${API_BASE}/download/${JOB_ID}"
echo "✅ Done! PDF saved as '$PDF_FILE'"


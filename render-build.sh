#!/usr/bin/env bash
# Build script for Render deployment
# NOTE: System dependencies (Tesseract, Poppler) are installed via Dockerfile

set -o errexit  # Exit on error

echo "📦 Installing Python dependencies..."
pip install --upgrade pip

# Clear pip cache and force fresh download
pip cache purge || true
rm -rf ~/.cache/pip || true

# Install without cache
pip install --no-cache-dir -r requirements.txt

echo "🔥 Pre-downloading & exporting reranker model to ONNX (prevents first-query timeout)..."
python3 -c "
from sentence_transformers import CrossEncoder
import os
# Download model and export to ONNX (one-time, 2-3x faster than PyTorch at runtime)
# ONNX = production standard for inference optimization (2024 best practice)
model = CrossEncoder('BAAI/bge-reranker-base', backend='onnx', device='cpu')
print('✅ Reranker model downloaded & exported to ONNX')
"
echo "✅ Reranker model ready (ONNX optimized)"

# Create Google Cloud credentials file from environment variable
if [ ! -z "$GOOGLE_CLOUD_CREDENTIALS_JSON" ]; then
  echo "🔑 Creating Google Cloud credentials file..."
  echo "$GOOGLE_CLOUD_CREDENTIALS_JSON" > /tmp/google-cloud-key.json
  export GOOGLE_APPLICATION_CREDENTIALS="/tmp/google-cloud-key.json"
  echo "✅ Google Cloud credentials saved to /tmp/google-cloud-key.json"
fi

echo "✅ Build complete!"


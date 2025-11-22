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

echo "🔥 Pre-downloading reranker model (prevents first-query timeout)..."
python3 -c "
from sentence_transformers import CrossEncoder
import os
# Download model to cache (will use PyTorch backend at runtime)
# Note: ONNX export disabled due to optimum API incompatibility with sentence-transformers 5.0
model = CrossEncoder('BAAI/bge-reranker-base', device='cpu')
print('✅ Reranker model downloaded to cache')
"
echo "✅ Reranker model ready"

# Create Google Cloud credentials file from environment variable
if [ ! -z "$GOOGLE_CLOUD_CREDENTIALS_JSON" ]; then
  echo "🔑 Creating Google Cloud credentials file..."
  echo "$GOOGLE_CLOUD_CREDENTIALS_JSON" > /tmp/google-cloud-key.json
  export GOOGLE_APPLICATION_CREDENTIALS="/tmp/google-cloud-key.json"
  echo "✅ Google Cloud credentials saved to /tmp/google-cloud-key.json"
fi

echo "✅ Build complete!"


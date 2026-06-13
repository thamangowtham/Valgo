#!/usr/bin/env bash
# Package the Lambda function for deployment.
set -euo pipefail

cd "$(dirname "$0")/.."

BUILD_DIR=dist/build
ZIP_PATH=dist/auth_refresh.zip
mkdir -p dist

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Install Lambda runtime deps for the AWS Lambda Python 3.11 environment
pip install -t "$BUILD_DIR" \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.11 \
    --only-binary=:all: \
    kiteconnect pyotp boto3 requests

# Copy the source
cp valgo_auth_refresh/*.py "$BUILD_DIR/"

# Zip
rm -f "$ZIP_PATH"
(cd "$BUILD_DIR" && zip -r9 "../$(basename "$ZIP_PATH")" . > /dev/null)

echo "==> $ZIP_PATH ready"
ls -lh "$ZIP_PATH"

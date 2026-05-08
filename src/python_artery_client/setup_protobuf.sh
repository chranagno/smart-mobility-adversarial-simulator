#!/bin/bash
# Setup script to compile Protocol Buffer definitions for Python

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTERY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Compiling Protocol Buffer definitions..."

# Compile ControlService.proto for Python
protoc --python_out="$SCRIPT_DIR" \
    --proto_path="$ARTERY_ROOT/src/traci" \
    "$ARTERY_ROOT/src/traci/ControlService.proto"

if [ $? -eq 0 ]; then
    echo "✓ Successfully compiled ControlService.proto"
    echo "  Output: $SCRIPT_DIR/ControlService_pb2.py"
else
    echo "✗ Failed to compile Protocol Buffer definition"
    echo "  Make sure 'protoc' is installed:"
    echo "    Ubuntu/Debian: sudo apt-get install protobuf-compiler"
    echo "    macOS: brew install protobuf"
    exit 1
fi


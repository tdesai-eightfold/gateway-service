#!/usr/bin/env bash
# Export the gateway's self-signed certificate for trusting on your machine.
# Cert is baked into the image at build time - no SSH or running container needed.
#
# Usage:
#   ./export-cert.sh [image] [output]
#   ./export-cert.sh                              # uses gateway-server-v2, outputs gateway-cert.pem
#   ./export-cert.sh ap-hyderabad-1.ocir.io/ns/gateway-server-v2:latest
#
# Then: Keychain Access → File → Import → select output → double-click cert → Trust → Always Trust
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${1:-gateway-server-v2}"
OUTPUT="${2:-$REPO_ROOT/gateway-cert.pem}"

echo "Extracting cert from image: $IMAGE"
docker run --rm "$IMAGE" cat /etc/nginx/ssl/cert.pem > "$OUTPUT"
echo "Exported certificate to $OUTPUT"
echo ""
echo "Keychain Access → File → Import → select $OUTPUT → double-click cert → Trust → Always Trust"

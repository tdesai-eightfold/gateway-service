#!/usr/bin/env bash
# Generate self-signed cert with your server IP in SAN (fixes "Your connection is not private").
# Run before oci-push.sh if your gateway IP is not 127.0.0.1.
#
# Usage:
#   ./generate-cert.sh                      # localhost only
#   ./generate-cert.sh 140.245.209.216     # add OCI instance IP
#   ./generate-cert.sh 1.2.3.4 5.6.7.8    # multiple IPs
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$REPO_ROOT/ssl"

# SAN: always localhost + 127.0.0.1, plus any IPs passed as args
SAN="DNS:localhost,IP:127.0.0.1"
for ip in "$@"; do
  SAN="${SAN},IP:${ip}"
done

echo "Generating cert with SAN: $SAN"
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$REPO_ROOT/ssl/key.pem" -out "$REPO_ROOT/ssl/cert.pem" \
  -subj "/CN=gateway/O=code-server" \
  -addext "subjectAltName=${SAN}"

echo "Created ssl/cert.pem and ssl/key.pem"
echo "Trust on Mac: open ssl/cert.pem → Keychain → Trust Always"
echo "Then rebuild: ./oci-push.sh"

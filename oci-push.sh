#!/usr/bin/env bash
# Build and push v2-ubuntu-base-container image to Oracle Container Image Registry (OCIR).
# Requires: OCI CLI configured (oci cli setup) or OCI_OCIR_NAMESPACE + OCI_AUTH_TOKEN.
#
# Usage:
#   export OCI_OCIR_NAMESPACE="axxxxxxxxxx"   # Tenancy namespace (Profile → Tenancy)
#   export OCI_REGION="ap-tokyo-1"
#   export OCI_AUTH_TOKEN="..."               # From User Settings → Auth Tokens (or use oci cli)
#   ./oci-push.sh
#
# Optional: OCI_OCIR_USER (required if OCI CLI not configured), IMAGE_NAME, TAG, PLATFORM
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-gateway-server-v3}"
OCI_REGION="${OCI_REGION:-ap-tokyo-1}"
OCI_OCIR_NAMESPACE="${OCI_OCIR_NAMESPACE:?Set OCI_OCIR_NAMESPACE (tenancy namespace from OCI Console)}"
# Docker login: use namespace/oracleidentitycloudservice/username or namespace/ocid1.user.oc1..xxx
OCI_USER_OCID="${OCI_USER_OCID:-}"
if [ -z "$OCI_AUTH_TOKEN" ]; then
  echo "Attempting to get auth token from OCI CLI..."
  OCI_AUTH_TOKEN=$(oci iam auth-token list --user-id "$(oci iam user list --compartment-id "$OCI_TENANCY_OCID" --query 'data[0].id' --raw-output 2>/dev/null)" --query 'data[0].token' --raw-output 2>/dev/null) || true
fi
if [ -z "$OCI_AUTH_TOKEN" ]; then
  echo "Set OCI_AUTH_TOKEN (OCI Console → User → Auth Tokens → Generate) or run 'oci setup' and use OCI CLI." >&2
  exit 1
fi

# Tag: same as aws-push (e.g. latest-python)
TAG="${TAG:-latest}"
REGISTRY="${OCI_REGION}.ocir.io"
FULL_IMAGE="${REGISTRY}/${OCI_OCIR_NAMESPACE}/${IMAGE_NAME}:${TAG}"
PLATFORM="${PLATFORM:-linux/amd64}"

# Docker login: username = <tenancy-namespace>/<oci-username> or <tenancy-namespace>/oracleidentitycloudservice/<username>
DOCKER_USER="${OCI_OCIR_USER:-${OCI_OCIR_NAMESPACE}/oracleidentitycloudservice/$(oci iam user list --all --query 'data[0].name' --raw-output 2>/dev/null || true)}"
# Reject placeholder or invalid username (oci-user, empty suffix, or ocid)
if [ -z "$DOCKER_USER" ] || [[ "$DOCKER_USER" == *"ocid1"* ]] || [[ "$DOCKER_USER" == *"/oci-user" ]] || [[ "$DOCKER_USER" == */ ]]; then
  echo "Set OCI_OCIR_USER for OCIR login. Format:" >&2
  echo "  Native OCI user:  export OCI_OCIR_USER=\"${OCI_OCIR_NAMESPACE}/your@email.com\"" >&2
  echo "  Federated user:   export OCI_OCIR_USER=\"${OCI_OCIR_NAMESPACE}/oracleidentitycloudservice/username\"" >&2
  echo "  (Username = your OCI Console login, from Profile menu)" >&2
  exit 1
fi
echo "Logging in to OCIR ${REGISTRY}..."
echo "$OCI_AUTH_TOKEN" | docker login "$REGISTRY" --username "$DOCKER_USER" --password-stdin

echo "Building with buildx and pushing $FULL_IMAGE (platform=$PLATFORM)..."
docker buildx build \
  --no-cache \
  --platform "$PLATFORM" \
  -f "$REPO_ROOT/Dockerfile" \
  -t "$FULL_IMAGE" \
  --push \
  "$REPO_ROOT"
echo "Pushed $FULL_IMAGE"
echo "$FULL_IMAGE"
echo ""
echo "To trust the cert: open ssl/cert.pem (or docker run --rm $FULL_IMAGE cat /etc/nginx/ssl/cert.pem > gateway-cert.pem)"

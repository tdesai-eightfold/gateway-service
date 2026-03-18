#!/bin/sh
set -e

# Resolver for DNS (OCI: 169.254.169.254; AWS: use system resolver or VPC DNS)
export NGINX_RESOLVER="${NGINX_RESOLVER:-169.254.169.254}"
# Workspace domain: OCI=workspace.internal; AWS with container-resolver-dns zone=workspace.internal.container-resolver-dns
if [ "${NGINX_PROVIDER}" = "aws" ]; then
    export NGINX_WORKSPACE_DOMAIN="${NGINX_WORKSPACE_DOMAIN:-workspace.internal.container-resolver-dns}"
else
    export NGINX_WORKSPACE_DOMAIN="${NGINX_WORKSPACE_DOMAIN:-workspace.internal}"
fi
envsubst '${NGINX_RESOLVER} ${NGINX_WORKSPACE_DOMAIN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
envsubst '${NGINX_RESOLVER} ${NGINX_WORKSPACE_DOMAIN}' < /etc/nginx/nginx-http-only.conf.template > /etc/nginx/nginx-http-only.conf

# Use HTTP only when TLS is terminated at external LB (e.g. OCI Load Balancer with ACM)
if [ "${GATEWAY_HTTPS}" = "0" ]; then
    cp /etc/nginx/nginx-http-only.conf /etc/nginx/nginx.conf
fi

# Cert is baked in at build time; generate only if missing (e.g. when mounting over /etc/nginx/ssl)
SSL_DIR="/etc/nginx/ssl"
CERT="${SSL_DIR}/cert.pem"
KEY="${SSL_DIR}/key.pem"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    mkdir -p "$SSL_DIR"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY" -out "$CERT" \
        -subj "/CN=gateway/O=code-server"
fi

exec nginx -g "daemon off;"

# Gateway Server (nginx)

Reverse proxy for code-server workspaces. Routes `/<hash>/...` to `{hash}.workspace.internal:80`.

## HTTPS (default)

- Listens on **80** (redirects to HTTPS) and **443**
- Auto-generates a self-signed certificate on first run
- Use `https://<host>/<hash>/` to access workspaces (required for `crypto.subtle` / webviews)

### Production: use your own certificates

Mount cert and key at startup:

```bash
docker run -d -p 443:443 -p 80:80 \
  -v /path/to/cert.pem:/etc/nginx/ssl/cert.pem:ro \
  -v /path/to/key.pem:/etc/nginx/ssl/key.pem:ro \
  gateway-server-v2
```

## Trust self-signed cert (fix "Your connection is not private")

The cert must include your server IP in SAN. Regenerate with your IP, then trust:

```bash
# 1. Regenerate cert with your gateway IP (fixes hostname mismatch)
./generate-cert.sh 140.245.209.216

# 2. Trust on Mac
open ssl/cert.pem   # Keychain → double-click cert → Trust → Always Trust

# 3. Rebuild and push
./oci-push.sh
```

## HTTP-only (behind TLS-terminating load balancer)

When an external LB (e.g. OCI Load Balancer with ACM) terminates TLS and forwards HTTP:

```bash
docker run -d -p 80:80 -e GATEWAY_HTTPS=0 gateway-server-v2
```

## AWS deployment

For AWS with Route 53 zone `container-resolver-dns`, set `NGINX_PROVIDER=aws` so the gateway resolves `{hash}.workspace.internal.container-resolver-dns`:

```bash
docker run -d -p 80:80 -p 443:443 \
  -e NGINX_PROVIDER=aws \
  gateway-server-v2
```

Or with `aws.env.example`:

```bash
docker run -d -p 80:80 -p 443:443 --env-file aws.env.example gateway-server-v2
```

**Direct nginx (no Docker):**

```bash
export NGINX_PROVIDER=aws
export NGINX_WORKSPACE_DOMAIN="${NGINX_WORKSPACE_DOMAIN:-workspace.internal.container-resolver-dns}"
envsubst '${NGINX_RESOLVER} ${NGINX_WORKSPACE_DOMAIN}' < nginx.conf > /tmp/nginx.conf
# ... copy to /etc/nginx/ and start nginx
```

Override `NGINX_WORKSPACE_DOMAIN` if your zone uses a different suffix. Deploy the gateway in the same VPC as your workspaces.
# gateway-service

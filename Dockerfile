FROM nginx:latest

RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

# Pre-generated cert (ssl/cert.pem) - same cert for all deployments; users trust via ssl/cert.pem
COPY ssl/cert.pem ssl/key.pem /etc/nginx/ssl/

# Starter template picker at /<hash>/starter-template
COPY templates/index.html /usr/share/nginx/html/starter-template.html

COPY nginx.conf /etc/nginx/nginx.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/entrypoint.sh"]

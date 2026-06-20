# ============================================================================
# Public edge nginx — TLS termination + reverse proxy for API and SPA.
# ============================================================================
FROM nginx:1.27-alpine

# gettext = envsubst (templating); openssl = self-signed fallback cert.
RUN apk add --no-cache gettext openssl

COPY nginx.conf /etc/nginx/nginx.conf
COPY includes/ /etc/nginx/includes/
COPY templates/ /etc/nginx/templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80 443

ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

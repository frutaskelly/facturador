# Landing estático — dos sitios por Host (nginx):
#   facturador.mx  → facturador/index.html
#   smartsupply.mx → smartsupply/index.html  (WhatsApp-bot)
FROM nginx:1.27-alpine

# Páginas (cada una en su docroot).
COPY facturador/index.html   /usr/share/nginx/html/facturador/index.html
COPY smartsupply/index.html  /usr/share/nginx/html/smartsupply/index.html

# Config: gzip + cabeceras + vhost por Host + fallback a index.
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

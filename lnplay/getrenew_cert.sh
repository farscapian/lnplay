#!/bin/bash

set -eu
cd "$(dirname "$0")"

# we store TLS certs in a docker volume under /var/lib/docker
docker volume create lnplay-certs

# let's do a refresh of the certificates. Let's Encrypt will not run if it's not time.
CERTBOT_IMAGE_NAME="certbot/certbot:latest"
if ! docker image inspect "$CERTBOT_IMAGE_NAME" &> /dev/null; then
    docker pull -q "$CERTBOT_IMAGE_NAME"
fi

# TODO remove backend_domain_name and put DOMAIN_NAME instead.
GET_CERT_STRING="docker run -t --rm --name certbot -p 80:80 -p 443:443 -v lnplay-certs:/etc/letsencrypt ${CERTBOT_IMAGE_NAME} certonly -v --noninteractive --agree-tos --key-type ecdsa --standalone --expand -d ${BACKEND_FQDN} -d ${FRONTEND_FQDN} --email info@${BACKEND_FQDN}"

# execute the certbot command
eval "$GET_CERT_STRING"
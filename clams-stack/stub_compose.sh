#!/bin/bash

set -ex
cd "$(dirname "$0")"

# this script writes out the docker-compose.yml file.

# close HTTP block
DOCKER_COMPOSE_YML_PATH="$(pwd)/docker-compose.yml"
touch "$DOCKER_COMPOSE_YML_PATH"

RPC_AUTH_TOKEN='polaruser:5e5e98c21f5c814568f8b55d83b23c1c$$066b03f92df30b11de8e4b1b1cd5b1b4281aa25205bd57df9be82caf97a05526'
BITCOIND_COMMAND="bitcoind -server=1 -rpcauth=${RPC_AUTH_TOKEN} -zmqpubrawblock=tcp://0.0.0.0:28334 -zmqpubrawtx=tcp://0.0.0.0:28335 -zmqpubhashblock=tcp://0.0.0.0:28336 -txindex=1 -upnp=0 -rpcbind=0.0.0.0 -rpcallowip=0.0.0.0/0 -rpcport=${BITCOIND_RPC_PORT:-18443} -rest -listen=1 -listenonion=0 -fallbackfee=0.0002 -mempoolfullrbf=1"

for CHAIN in regtest signet testnet; do
    if [ "$CHAIN" = "$BTC_CHAIN" ]; then  
        BITCOIND_COMMAND="$BITCOIND_COMMAND -${BTC_CHAIN}" 
    fi
done

cat > "$DOCKER_COMPOSE_YML_PATH" <<EOF
version: '3.8'
services:

  reverse-proxy:
    image: nginx:latest
EOF


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    ports:
      - 80:80
EOF

if [ "$ENABLE_TLS" = true ]; then
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
      - 443:443
EOF
fi

if [ "$ENABLE_TLS" = true ]; then
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
      - "${CLIGHTNING_WEBSOCKET_EXTERNAL_PORT:-7272}:9863"
EOF
fi


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    networks:
EOF

for (( CLN_ID=0; CLN_ID<$CLN_COUNT; CLN_ID++ )); do
    CLN_ALIAS="cln-${BTC_CHAIN}"
cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
      - clnnet-${CLN_ID}
EOF
done

cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    configs:
      - source: nginx-config
        target: /etc/nginx/nginx.conf
    volumes:
      - clams-browser-app:/browser-app
EOF

if [ "$ENABLE_TLS" = true ]; then
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
      - certs:/certs
EOF
fi




cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF

  bitcoind:
    image: polarlightning/bitcoind:24.0
    hostname: bitcoind
    networks:
      - bitcoindnet
    command: >-
      ${BITCOIND_COMMAND}
EOF

# we persist data for signet, testnet, and mainnet
if [ "$BTC_CHAIN" != regtest ]; then
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    volumes:
      - bitcoind:/home/bitcoin/.bitcoin
EOF
fi

cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    deploy:
      mode: global

EOF









# write out service for CLN; style is a docker stack deploy style,
# so we will use the replication feature
for (( CLN_ID=0; CLN_ID<$CLN_COUNT; CLN_ID++ )); do
    CLN_ALIAS="cln-${BTC_CHAIN}"
    CLN_COMMAND="sh -c \"chown 1000:1000 /opt/c-lightning-rest/certs && lightningd --alias=${CLN_ALIAS} --bind-addr=0.0.0.0 --announce-addr=\${CLIGHTNING_LOCAL_BIND_ADDR:-localhost}:\${CLIGHTNING_WEBSOCKET_EXTERNAL_PORT:-9736} --bitcoin-rpcuser=polaruser --bitcoin-rpcpassword=polarpass --bitcoin-rpcconnect=bitcoind --bitcoin-rpcport=\${BITCOIND_RPC_PORT:-18443} --log-level=debug --dev-bitcoind-poll=20 --dev-fast-gossip --experimental-websocket-port=9736 --plugin=/opt/c-lightning-rest/plugin.js --experimental-offers"

    for CHAIN in regtest signet testnet; do
        CLN_COMMAND="$CLN_COMMAND --network=${BTC_CHAIN}"
    done

    CLN_COMMAND="$CLN_COMMAND\""
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
  cln-${CLN_ID}:
    image: ${CLN_IMAGE}
    hostname: cln-${CLN_ID}
    command: >-
      ${CLN_COMMAND}
EOF


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    volumes:
      - cln-${CLN_ID}:/home/clightning/.lightning
      - cln-${CLN_ID}-certs:/opt/c-lightning-rest/certs
EOF


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    networks:
      - bitcoindnet
      - clnnet-${CLN_ID}
    deploy:
      mode: replicated
      replicas: 1

EOF

done

cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
networks:
  bitcoindnet:
EOF


for (( CLN_ID=0; CLN_ID<$CLN_COUNT; CLN_ID++ )); do
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
  clnnet-${CLN_ID}:
EOF

done


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF

volumes:
EOF

cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF

  bitcoind:
    external: true
    name: bitcoind-${BTC_CHAIN}
EOF


# define the volumes for CLN nodes. regtest and signet SHOULD NOT persist data, but TESTNET and MAINNET MUST define volumes
for (( CLN_ID=0; CLN_ID<$CLN_COUNT; CLN_ID++ )); do
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
  cln-${CLN_ID}:
  cln-${CLN_ID}-certs:
EOF

done


cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF

  clams-browser-app:
    external: true
    name: clams-browser-app
EOF

if [ "$ENABLE_TLS" = true ]; then
    cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
  certs:
    external: true
    name: clams-certs
EOF
fi





####


# if [ "$DEPLOY_LN_WS_PROXY" = true ]; then
#     cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF
    
#   ln-ws-proxy:

#     image: ${LN_WS_PROXY_IMAGE_NAME}
#     networks:
#       - lnwsproxynet
#     expose:
#       - '3000'
# EOF

# fi

cat >> "$DOCKER_COMPOSE_YML_PATH" <<EOF

configs:
  nginx-config:
    file: ${NGINX_CONFIG_PATH}

EOF

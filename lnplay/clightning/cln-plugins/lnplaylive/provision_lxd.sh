#!/bin/bash

set -exu

INVOICE_ID=
EXPIRATION_DATE_UNIX_TIMESTAMP=

# grab any modifications from the command line.
for i in "$@"; do
    case $i in
        --invoice-id=*)
            INVOICE_ID="${i#*=}"
            shift
        ;;
        --expiration-date=*)
            EXPIRATION_DATE_UNIX_TIMESTAMP="${i#*=}"
            shift
        ;;
        *)
        echo "Unexpected option: $1"
        exit 1
        ;;
    esac
done

if [ -z "$INVOICE_ID" ]; then
    echo "ERROR: INVOICE_ID must be set."
    exit 1
fi

if [ -z "$EXPIRATION_DATE_UNIX_TIMESTAMP" ]; then
    echo "ERROR: EXPIRATION_DATE_UNIX_TIMESTAMP must be set."
    exit 1
fi

if ! lxc remote list | grep -q lnplaylive; then
    lxc remote add lnplaylive -q "$LNPLAY_LXD_FQDN_PORT" --password "$LNPLAY_LXD_PASSWORD" --accept-certificate > /dev/null
fi

if ! lxc remote get-default | grep -q lnplaylive; then
    lxc remote switch lnplaylive  > /dev/null
fi

PROJECT_NAME="$INVOICE_ID-$EXPIRATION_DATE_UNIX_TIMESTAMP"
if ! lxc project list | grep -q "$PROJECT_NAME"; then
    lxc project create "$PROJECT_NAME" > /dev/null
fi

if ! lxc project list | grep -q "$PROJECT_NAME (current)"; then
    lxc project switch "$PROJECT_NAME"  > /dev/null
fi

REMOTE_CONF_PATH="$HOME/ss/remotes/$(lxc remote get-default)"
mkdir -p "$REMOTE_CONF_PATH"  > /dev/null

REMOTE_CONF_FILE_PATH="$REMOTE_CONF_PATH/remote.conf"
# need to get the remote.conf in there
cat > "$REMOTE_CONF_FILE_PATH" <<EOF
LXD_REMOTE_PASSWORD=
DEPLOYMENT_STRING=
# REGISTRY_URL=http://registry.domain.tld:5000
EOF

# need to get the project.conf in there
PROJECT_CONF_PATH="$REMOTE_CONF_PATH/projects/$PROJECT_NAME"
mkdir -p "$PROJECT_CONF_PATH"  > /dev/null

PROJECT_CONF_FILE_PATH="$PROJECT_CONF_PATH/project.conf"

# todo, there needs to be some database/file of mac_addresses that can be used.
export VM_MAC_ADDRESS="00:00:AA:00:00:00"
export PRIMARY_DOMAIN="a.lnplay.live"
cat > "$PROJECT_CONF_FILE_PATH" <<EOF
PRIMARY_DOMAIN="${DOMAIN_NAME}"
LNPLAY_SERVER_MAC_ADDRESS=${VM_MAC_ADDRESS}
# LNPLAY_SERVER_CPU_COUNT="4"
# LNPLAY_SERVER_MEMORY_MB="4096"
EOF

# need to get the site.conf in there
cd /sovereign-stack

sleep 15
#./deployment/up.sh

# set the project to default
lxc project switch default  > /dev/null

# set the remote to local.
lxc remote switch local  > /dev/null

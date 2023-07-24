#!/bin/bash

set -eu
cd "$(dirname "$0")"

# this script tears everything down that might be up. It does not destroy data.

. ./defaults.env
. ./load_env.sh

PURGE=false
PRUNE=true

if [ "$DO_NOT_DEPLOY" = true ]; then
    echo "INFO: The DO_NOT_DEPLOY was set to true in your environment file. You need to remove this before this script will execute."
    exit 1
fi

./prompt.sh

# grab any modifications from the command line.
for i in "$@"; do
    case $i in
        --purge)
            PURGE=true
            shift
        ;;
        --no-prune=*)
            PRUNE=false
            shift
        ;;
        *)
        echo "Unexpected option: $1"
        exit 1
    esac
done

# write out service for CLN; style is a docker stack deploy style,
# so we will use the replication feature
STACKS=$(docker stack ls --format "{{.Name}}")
for (( CLN_ID=CLN_COUNT; CLN_ID>=0; CLN_ID-- )); do
    STACK_NAME="roygbiv-cln-${CLN_ID}"
    if echo "$STACKS" | grep -q "$STACK_NAME"; then
        docker stack rm "$STACK_NAME"
        sleep 1
    fi
done

# now bring down the main roygbiv stack.
if echo "$STACKS" | grep -q roygbiv-stack; then
    docker stack rm roygbiv-stack
fi

# wait until all containers are shut down.
while true; do
    COUNT=$(docker ps -q | wc -l)
    if [ "$COUNT" -gt 0 ]; then
        sleep 1
    else
        sleep 3
        break
    fi
done

if [ "$PRUNE" = true ]; then
    # remove any container runtimes.
    docker system prune -f

    rm -f ./channel_templates/node_addrs.txt
    rm -f ./channel_templates/node_pubkeys.txt
    rm -f ./channel_templates/any_offers.txt
fi

# let's delete all volumes EXCEPT roygbiv-certs
if [ "$PURGE" = true ]; then
    ./purge.sh
fi

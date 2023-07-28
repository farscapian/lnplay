#!/bin/bash

set -exu
cd "$(dirname "$0")" || exit 1

mapfile -t pubkeys < ../channel_templates/node_pubkeys.txt
mapfile -t anyoffers < ../channel_templates/any_offers.txt
mapfile -t names < ../roygbiv/names.txt

# start the createprism json string
PRISM_JSON_STRING="["

# do every other keysend/bolt12
for ((CLN_ID=2; CLN_ID<CLN_COUNT; CLN_ID++)); do
    NODE_PUBKEY=${pubkeys[$CLN_ID]}
    NODE_ANYOFFER=${anyoffers[$CLN_ID]}
    PRISM_JSON_STRING="${PRISM_JSON_STRING}{\"name\" : \"${names[$CLN_ID]}\", \"destination\": \"$NODE_ANYOFFER\", \"split\": 1, \"type\":\"bolt12\"},"
done

# close off the json
PRISM_JSON_STRING="${PRISM_JSON_STRING::-1}]"

# create a prism with (n-2) members.
lncli --id=1 createprism label="roygbiv_demo" members="$PRISM_JSON_STRING"

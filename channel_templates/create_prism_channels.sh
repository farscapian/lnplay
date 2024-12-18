#!/bin/bash

set -eu

# the objective of this script is to create channels in the prism format.
# Alice -> Bob, then Bob creates a channels to all subsequent nodes.
# this allows creating Prisms with an arbitrary number of prism recipients.

mapfile -t pubkeys < "$LNPLAY_SERVER_PATH/node_pubkeys.txt"

function checkOutputs {
    # let's wait for an output to exist before we start creating any channels.
    OUTPUT_EXISTS=false
    while [ "$OUTPUT_EXISTS" = false ]; do
        # pool to ensure we have enough outputs to spend with.
        OUTPUT_EXISTS=$(../lightning-cli.sh --id="$1" listfunds | jq '.outputs | length > 0')

        # if at least one output exists in the CLN node, then we know
        # the node has been funded previously, and we can therefore skip
        if [ "$OUTPUT_EXISTS" = true ]; then
            echo "INFO: cln-$1 has sufficient funds." >> /dev/null
            break
        else
            sleep 3
        fi
    done
}
sleep 5

CHANNEL_COUNT=$(../lightning-cli.sh --id=0 listchannels | jq '.channels | length') >> /dev/null
if [ "$CHANNEL_COUNT" = 0 ]; then
    # ensure Alice has outputs to spend from
    checkOutputs 0
    ../lightning-cli.sh --id=0 fundchannel "${pubkeys[1]}" 10000000 >> /dev/null
    echo "Alice opened a 10000000 sat channel to Bob" >> /dev/null
fi


# ensure Bob has outputs to spend from
checkOutputs 1

# next we use fundmultichannel so Bob can create channels to the remaining nodes.
MULTIFUND_CHANNEL_JSON="["

# we increase the CLN count by one here so we can reserve at least one UTXO
# for things like RBF and other things I'm sure.
CLN_COUNT_PLUS_ONE=$((CLN_COUNT + 2))
SEND_AMT=$((100000000 / CLN_COUNT_PLUS_ONE))

# fund each cln node starting at node 2 (Carol)
for ((CLN_ID=2; CLN_ID<CLN_COUNT; CLN_ID++)); do
    NODE_PUBKEY=${pubkeys[$CLN_ID]}
    MULTIFUND_CHANNEL_JSON+="{\"id\": \"$NODE_PUBKEY@cln-${CLN_ID}:9735\",\"amount\": \"$SEND_AMT\""
    MULTIFUND_CHANNEL_JSON="${MULTIFUND_CHANNEL_JSON}},"
done

# close off the json
MULTIFUND_CHANNEL_JSON="${MULTIFUND_CHANNEL_JSON::-1}]"

# execute multifundchannel from bob.
../lightning-cli.sh --id=1 multifundchannel "$MULTIFUND_CHANNEL_JSON" >> /dev/null

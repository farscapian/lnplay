#!/bin/bash

set -exu
cd "$(dirname "$0")"

PRISM_OFFER_LABEL="band_prism_offer"

# grab any modifications from the command line.
for i in "$@"; do
    case $i in
        --prism-offer-label=*)
            PRISM_OFFER_LABEL="${i#*=}"
            shift
        ;;
        *)
        echo "Unexpected option: $1"
        exit 1
        ;;
    esac
done

if [ -z "$PRISM_OFFER_LABEL" ]; then
    echo "ERROR: You must provide a description."
    exit 1
fi

# now create a new BOLT12 any offer and grab the offer_id
PRISM_OFFER=$(../lightning-cli.sh --id=1 listoffers | jq -r ".offers[] | select(.label == \"$PRISM_OFFER_LABEL\") | .bolt12")

if [ -z "$PRISM_OFFER" ]; then
    echo "ERROR: PRISM OFFER was not found."
    exit 1
fi

# fetch an invoice
INVOICE=$(../lightning-cli.sh --id=0 fetchinvoice "$PRISM_OFFER" 1000000 | jq -r '.invoice')

if [ -z "$INVOICE" ]; then
    echo "ERROR: INVOICE is not set."
    exit 1
fi

# pay the bolt12 invoice.
../lightning-cli.sh --id=0 pay "$INVOICE"

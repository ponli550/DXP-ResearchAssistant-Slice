#!/bin/sh
# Publish deck.html to Cloudflare as a static Worker. Needs `wrangler login` once on this machine.
set -e
cd "$(dirname "$0")/.."
mkdir -p deploy/deck/public
cp deck.html deploy/deck/public/index.html
cd deploy/deck
wrangler deploy

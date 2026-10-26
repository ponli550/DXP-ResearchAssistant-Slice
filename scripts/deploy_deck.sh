#!/bin/sh
# Publish deck.html to Cloudflare as a static Worker. Needs `wrangler login` once on this machine.
set -e
cd "$(dirname "$0")/.."
mkdir -p deploy/deck/public
cp deck.html deploy/deck/public/index.html
rm -rf deploy/deck/public/deck-assets && cp -R deck-assets deploy/deck/public/deck-assets
cd deploy/deck
wrangler deploy

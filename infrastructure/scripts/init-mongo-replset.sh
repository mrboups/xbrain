#!/bin/bash
# Init le replica set rs0 (single node) — nécessaire pour les change streams.
# Lancé par MongoDB au premier boot via /docker-entrypoint-initdb.d/
sleep 5
mongosh --quiet --eval "
try {
  rs.status();
  print('rs0 already initialized');
} catch (e) {
  rs.initiate({ _id: 'rs0', members: [{ _id: 0, host: 'librechat-mongo:27017' }] });
  print('rs0 initialized');
}
"

#!/bin/sh
# Creates the separate database n8n uses for its own state.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE DATABASE n8n;
CREATE DATABASE loans;
SQL

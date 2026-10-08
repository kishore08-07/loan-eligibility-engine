#!/bin/sh
# One-shot n8n bootstrap (runs inside the n8n image): create credentials from env, import the
# workflows and publish them. Idempotent: import overwrites by id.
set -e
mkdir -p /tmp/creds
node -e '
const e = process.env;
const creds = [
  { id: "pgcred", name: "Loans Postgres", type: "postgres", data: {
      host: e.APP_DB_HOST, port: Number(e.APP_DB_PORT), database: e.APP_DB_NAME, user: e.APP_DB_USER,
      password: e.APP_DB_PASSWORD, ssl: e.APP_DB_SSL, allowUnauthorizedCerts: e.APP_DB_SSL !== "disable",
      maxConnections: 10 } },
  { id: "smtpcred", name: "Local Mailpit SMTP", type: "smtp", data: {
      host: e.SMTP_HOST, port: Number(e.SMTP_PORT), secure: false, user: "", password: "", disableStartTls: true } },
  { id: "awscred", name: "AWS SES", type: "aws", data: {
      region: e.AWS_SES_REGION, accessKeyId: e.AWS_SES_ACCESS_KEY_ID || "unset",
      secretAccessKey: e.AWS_SES_SECRET_ACCESS_KEY || "unset" } },
];
require("fs").writeFileSync("/tmp/creds/creds.json", JSON.stringify(creds));
'
n8n import:credentials --input=/tmp/creds/creds.json
for f in /init/workflows/*.json; do
  n8n import:workflow --input="$f"
done
for id in wfA_discovery wfB_matching wfC_notify; do
  n8n publish:workflow --id="$id"
done
chown -R node:node /home/node/.n8n 2>/dev/null || true
echo "n8n init done"

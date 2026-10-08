# Deploying to AWS and trying it as a user

One `serverless deploy` creates everything: **S3 → SQS → Lambda → RDS Postgres**, the **HTTP API**, the **SES identities**, and the **EC2 host that runs n8n**. Afterwards you upload one small bundle (the n8n workflows) and click the SES verification links.

```
browser ─► API Gateway/Lambda ─► S3 ─► SQS (+DLQ) ─► ingest Lambda ─► RDS
                                                          │ signed webhook (private IP, port 5678)
                                                          ▼
                                   EC2: n8n (A discovery, B matching, C notification) ─► SES ─► your inbox
```

Cost: RDS and EC2 bill hourly while they exist (no NAT gateway). Do the teardown at the end.

> The stack was deployed and `/health` verified on a real account; the n8n host, SES identities and the emails themselves are defined in `serverless.yml` (the template passes CloudFormation validation) but check them as you go.

## 0. Prerequisites (laptop)

AWS CLI v2 configured (`aws sts get-caller-identity` works), Node 20+, Docker running (builds the Linux wheels), the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) (only for the optional n8n editor), and a Gmail address you control (`abcd@gmail.com` below). Use one region everywhere (SES identities are per region):

```bash
export AWS_REGION=ap-southeast-2 AWS_DEFAULT_REGION=ap-southeast-2     # your region
export STAGE=dev
```

## 1. Configure

```bash
eval "$(bash infra/aws_network_env.sh)"          # prints VPC_ID, SUBNET_A_ID, SUBNET_B_ID, ROUTE_TABLE_ID (default VPC)
cd backend
cp deploy.env.example deploy.env                  # fill it in; it holds secrets and is git-ignored
```

Fill in the network values from the line above, then generate the secrets: `openssl rand -hex 16` (`DB_PASSWORD`), `openssl rand -hex 32` (`WEBHOOK_SECRET`, `N8N_ENCRYPTION_KEY`). Set `SES_SENDER_EMAIL=abcd@gmail.com`, `TEST_RECIPIENT=abcd@gmail.com` and `SES_RECIPIENT_1..3=abcd+1@…, abcd+2@…, abcd+3@…` (emails go to these aliases; Gmail delivers them all to `abcd@gmail.com`). Add `GEMINI_API_KEY` for the crawler and free-text criteria. Every variable is described in `deploy.env.example`.

## 2. Deploy

```bash
npm install --no-save serverless@3 serverless-python-requirements
set -a; . ./deploy.env; set +a
npx serverless deploy --stage $STAGE              # 10-15 min (RDS dominates)
```

(The `provider.runtime` "python3.12" warning from Serverless 3 is harmless.) Then:

```bash
npx serverless invoke -f migrate --stage $STAGE   # {"applied": ["001_init.sql"]}
npx serverless invoke -f seed --stage $STAGE      # {"seeded": 14}
aws cloudformation describe-stacks --stack-name loan-eligibility-engine-$STAGE \
  --query 'Stacks[0].Outputs[?OutputKey==`HttpApiUrl` || OutputKey==`N8nInstanceId`].[OutputKey,OutputValue]' --output text
curl <HttpApiUrl>/health                          # {"status": "ok"}  (API Lambda -> RDS works)
```

## 3. Verify the email addresses

CloudFormation made SES send a verification email to the sender and each recipient. Open the Gmail inbox, click every link, then check:

```bash
aws sesv2 list-email-identities --query 'EmailIdentities[].[IdentityName,VerificationStatus]' --output text   # all SUCCESS
```

(SES sandbox: you can only send to verified addresses, 1 mail/second, 200/day: enough here.)

## 4. Give n8n its workflows

The EC2 host boots, installs Docker and waits for this file:

```bash
cd ..   # repo root
bash infra/upload_n8n_bundle.sh $STAGE
```

Within about a minute n8n starts (it creates its Postgres/SES credentials from the stack's values and publishes workflows A, B, C and the error handler). Check from your laptop:

```bash
aws ssm start-session --target <N8nInstanceId>            # shell on the host (no open ports needed)
#   sudo tail /var/log/n8n-bootstrap.log ; sudo docker ps ; curl -s localhost:5678/healthz
```

Optional editor: `aws ssm start-session --target <N8nInstanceId> --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["5678"],"localPortNumber":["5678"]}'` then open http://localhost:5678 (create the owner account) to see the workflows and their executions.

Optional crawler: in the editor, open **A - Loan Product Discovery** → *Execute workflow* to add products from the lender pages.

## 5. UI and a real run

```bash
cd frontend
echo "VITE_API_URL=<HttpApiUrl>" > .env
npm install && npm run dev          # http://localhost:5173   (set CORS_ORIGIN to this origin in deploy.env, redeploy if you changed it)
```

1. Upload `users.csv`. The browser gets a presigned URL and sends the file **straight to S3**.
2. Watch `PENDING → PROCESSING → COMPLETED` (seconds), then matching `RUNNING → DONE` (about 30–60 s). The matches table and the **optimization funnel** (pairs at each stage, LLM calls) appear.
3. Check the inbox of `abcd@gmail.com`: up to 3 emails, addressed to `abcd+1…3`, none to the CSV addresses.
4. Upload the same file again → `DUPLICATE`. Upload a CSV with a bad row → `COMPLETED_WITH_ERRORS` with an error-report link.

Behind the scenes:

```bash
cd backend
npx serverless logs -f ingest --stage $STAGE --startTime 15m
# optimisation numbers for the latest run (needs DB access: run from the n8n host or any machine inside the VPC)
```

**Failure drill:** on the host `sudo docker compose -f /opt/n8n/docker-compose.aws.yml stop n8n`, upload a *different* CSV: the data loads but the webhook fails; SQS redelivers after 30 min and after 3 tries the message lands in the DLQ (alarm email if `ALERT_EMAIL` is set). Start n8n again and re-trigger at once:

```bash
npx serverless invoke -f resendWebhook --stage $STAGE --data '{"job_id":"<id from the UI>"}'
```

## 6. Teardown

```bash
cd backend && set -a; . ./deploy.env; set +a
BUCKET=loan-eligibility-engine-$STAGE-uploads-$(aws sts get-caller-identity --query Account --output text)
aws s3 rm s3://$BUCKET --recursive                   # a non-empty bucket blocks stack deletion
npx serverless remove --stage $STAGE                 # removes EC2, RDS (final snapshot is kept), SES identities, IAM user
aws rds describe-db-snapshots --query 'DBSnapshots[].DBSnapshotIdentifier' --output text   # delete the snapshot if unwanted
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Deploy fails at `Database` (engine version) | `export DB_ENGINE_VERSION=<version from aws rds describe-db-engine-versions --engine postgres>` |
| Deploy fails on `S3Endpoint` | `ROUTE_TABLE_ID` must be the table of the two subnets; if an S3 gateway endpoint already exists in the VPC, delete that resource from `serverless.yml` |
| Deploy fails on an SES identity | The address is already a verified identity in this region/account: delete it first (`aws sesv2 delete-email-identity`) |
| `/health` 500 | `DB_PASSWORD` mismatch or subnets not in `VPC_ID`: `npx serverless logs -f health` |
| Browser "S3 upload failed (network/CORS)" | `CORS_ORIGIN` ≠ page origin: change and redeploy |
| Job `COMPLETED`, matching never starts | n8n not up yet (did you run `infra/upload_n8n_bundle.sh`? see `/var/log/n8n-bootstrap.log`), then `invoke -f resendWebhook` |
| Matching `FAILED` | Open the failed execution in the n8n editor; fix and `resendWebhook` |
| Matching `DONE`, no email | SES identity not verified (check step 3), or inspect workflow C's execution; unsent matches stay un-notified so a re-run retries |
| Workflow A fails at once | `GEMINI_API_KEY` missing/invalid: set it in `/opt/n8n/.env.aws` and `sudo docker compose -f docker-compose.aws.yml --env-file .env.aws up -d --force-recreate` |

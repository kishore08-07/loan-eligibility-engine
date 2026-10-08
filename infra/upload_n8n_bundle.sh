#!/usr/bin/env bash
# Uploads the n8n bundle (compose file + workflows) that the EC2 host's boot script is waiting for.
#   bash infra/upload_n8n_bundle.sh [stage]     (uses the AWS CLI's current account/region)
set -euo pipefail
cd "$(dirname "$0")/.."
STAGE=${1:-dev}
BUCKET="loan-eligibility-engine-$STAGE-uploads-$(aws sts get-caller-identity --query Account --output text)"
python3 n8n/build_workflows.py >/dev/null            # make sure the JSON matches n8n/code
tar czf /tmp/n8n-bundle.tgz docker-compose.aws.yml n8n/init.sh n8n/workflows
aws s3 cp /tmp/n8n-bundle.tgz "s3://$BUCKET/deploy/n8n-bundle.tgz"
echo "uploaded; the host starts n8n within ~1 minute (log: /var/log/n8n-bootstrap.log on the instance)"

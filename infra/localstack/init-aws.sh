#!/usr/bin/env bash
set -euo pipefail

# LocalStack ready.d hook — creates S3 bucket + SQS queue + DLQ.

ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
BUCKET="${S3_BUCKET:-vridhi-documents}"
QUEUE_NAME="${SQS_QUEUE_NAME:-vridhi-ingest}"
DLQ_NAME="${SQS_DLQ_NAME:-vridhi-ingest-dlq}"
MAX_RECEIVE="${SQS_MAX_RECEIVE_COUNT:-5}"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="$REGION"

awslocal s3 mb "s3://$BUCKET" 2>/dev/null || true

DLQ_URL=$(awslocal sqs create-queue --queue-name "$DLQ_NAME" --query 'QueueUrl' --output text)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

QUEUE_URL=$(awslocal sqs create-queue \
  --queue-name "$QUEUE_NAME" \
  --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"$MAX_RECEIVE\\\"}\"}" \
  --query 'QueueUrl' --output text)

echo "S3 bucket: $BUCKET"
echo "SQS queue: $QUEUE_URL"
echo "SQS DLQ:   $DLQ_URL"
echo "LocalStack init complete."

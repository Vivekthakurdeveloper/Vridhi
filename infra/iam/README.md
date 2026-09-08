# Phase-1 IAM task roles (notes)

Do not use one role for everything.

## Planned roles

| Role | Purpose | Typical permissions |
|------|---------|---------------------|
| `api-task-role` | FastAPI on ECS | Secrets Manager (app secrets), RDS connect, limited S3 read for previews later |
| `worker-task-role` | Sync worker | SQS consume/produce, S3 read/write for connector artifacts, Secrets Manager (connector OAuth) |
| `embedding-task-role` | Embedding worker | SQS consume, S3 read normalized docs, OpenSearch write |
| `deployment-role` | CI/CD deploy | ECR push, ECS update service, least privilege |

API must **not** automatically have `S3 *`, `SQS *`, or `OpenSearch *`.

Terraform/CDK for these roles lands when ECS staging is provisioned.

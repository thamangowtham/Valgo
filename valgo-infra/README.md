# valgo-infra

Terraform for the Valgo platform. Provisions the network (VPC, NAT GW with whitelisted EIP), data layer (ElastiCache Redis, DynamoDB), authentication (Secrets Manager + the daily-refresh Lambda), and compute (ECS cluster, ALBs, EC2 SGs).

## Layout

```
infra/
├── main.tf             root module — wires everything together
├── variables.tf
├── outputs.tf
├── modules/
│   ├── network/        VPC, single-AZ subnets, NAT GW, placement group
│   ├── data/           ElastiCache + 4 DynamoDB tables
│   ├── auth/           Secrets Manager + auth_refresh Lambda + EventBridge cron
│   ├── compute/        ECS cluster, ALB, NLB, EC2 SG (skeleton — expand per-service)
│   └── observability/  (placeholder for CloudWatch alarms + dashboard)
└── envs/
    ├── dev/main.tf
    └── prod/main.tf
```

## Deploy

```bash
# First-time setup of remote state (recommended):
cd envs/dev
cp backend.tf.example backend.tf
# Edit backend.tf with your S3 bucket and DynamoDB lock table names

terraform init
terraform plan
terraform apply
```

## After apply — the critical step

```bash
terraform output whitelist_ip
# e.g. "13.234.45.12"
```

**Register THIS IP with Zerodha** (Kite Connect dashboard → app settings → IP whitelist). Without this, every order will be rejected with "IP not whitelisted" no matter what the rest of the stack does.

## How modules consume each other

The root module (`main.tf`) wires outputs to inputs. The dependency chain is:

```
network ──→ data (needs vpc_id, subnet_ids)
        ──→ compute (needs vpc_id, subnet_ids)
auth   ──┬──→ (Secrets ARNs feed compute via execution role)
         └──→ uses Lambda code from valgo-auth-refresh repo
data   ──→ compute (Redis endpoint, DDB ARNs become container env vars)
```

## Lambda deploy

The auth-refresh Lambda code lives in the `valgo-auth-refresh` repo. To deploy after a code change:

```bash
# In the valgo-auth-refresh repo:
./scripts/package.sh
# Produces dist/auth_refresh.zip

# Copy to where this repo expects it:
cp dist/auth_refresh.zip ../valgo-infra/build/

# Apply:
cd ../valgo-infra/envs/prod
terraform apply -target=module.valgo.module.auth.aws_lambda_function.auth_refresh
```

The Terraform config has `lifecycle { ignore_changes = [filename, source_code_hash] }` so subsequent `terraform apply` calls don't bounce the function unnecessarily.

## What this repo does NOT include

- Per-service ECS task definitions are still skeletons in `modules/compute`. As you ship each service's container image to ECR, add `aws_ecs_task_definition` and `aws_ecs_service` resources for it.
- CloudWatch alarms (feed_disconnected, daily_loss_breached, auth_refresh_failed) — `modules/observability` is empty. Wire up SNS → email/SMS once you've deployed and have real metrics flowing.

## Versioning

Tag releases with semver. Major bump if a `terraform apply` of a new version requires manual migration (e.g., destroying a resource that holds state). Read the changelog before applying.

# Deploy Ariadne to AWS — single EC2, Terraform, ~$30/mo

This module stands up the entire Ariadne lineage platform (Neo4j + Postgres +
4 parsers + gateway + Next.js frontend) on **one EC2 instance** using
`docker-compose`. It's the cheapest deployable shape — appropriate for a
portfolio demo or solo dev environment, not for production.

## What you get

| Resource | What it does | Cost |
|---|---|---:|
| EC2 t3.medium (Amazon Linux 2023) | Runs the whole docker-compose stack | ~$30.37/mo on-demand |
| EBS 30 GB gp3 root volume | OS + Docker images + Neo4j/Postgres data | $2.40/mo |
| Elastic IP (attached) | Stable public address across stop/start | $0 while attached |
| Security group | SSH + ports 3000/8000 locked to your IP only | $0 |
| **Total always-on** | | **~$33/mo** |
| **Total if you stop it nights/weekends** | | **~$12/mo** |

Stopped instances incur only the EBS storage cost — your graph and Postgres
data survive `terraform apply` → stop → start cycles.

## Prerequisites

On your laptop:
- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws configure` with a user that has EC2 + EBS + EIP permissions)
- An SSH client

In your AWS account (one-time, in the Console):
1. **Region.** Pick one close to you (the default in `variables.tf` is `us-east-1`). Note it.
2. **Key pair.** EC2 → Key Pairs → Create key pair → name it (e.g., `ariadne-demo`), type RSA, format `.pem`. **Save the downloaded `.pem` file** — AWS only lets you download it once. Move it to `~/.ssh/ariadne-demo.pem` and `chmod 400` it.
3. **Find your public IP** at https://checkip.amazonaws.com/ — you'll plug this into `terraform.tfvars` as `<your-ip>/32`.

## Deploy

```bash
cd deploy/aws

# 1. Copy the tfvars template and fill in your values
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: set key_pair_name and operator_ip_cidr

# 2. Initialize Terraform (downloads the AWS provider)
terraform init

# 3. Preview what'll be created
terraform plan

# 4. Apply — takes ~2 minutes to provision, then 5-8 minutes for cloud-init
#    inside the EC2 (apt updates, docker compose build of 6 images, container
#    startup, healthchecks).
terraform apply
```

When `apply` finishes you'll see outputs like:

```
frontend_url       = "http://3.84.117.203:3000"
gateway_health_url = "http://3.84.117.203:8000/health"
ssh_command        = "ssh -i ~/.ssh/ariadne-demo.pem ec2-user@3.84.117.203"
```

The frontend URL won't work immediately — cloud-init is still building the
Docker images on the instance.

## Verify the deploy

```bash
# Watch the bootstrap progress (5-8 minutes total)
ssh -i ~/.ssh/ariadne-demo.pem ec2-user@<PUBLIC-IP> 'sudo tail -f /var/log/user-data.log'

# Once you see "[user-data] === Done." check the gateway:
curl http://<PUBLIC-IP>:8000/health
# expect: {"status":"ok","neo4j":"connected","postgres":"connected"}

# Then open the frontend in your browser:
open http://<PUBLIC-IP>:3000
```

## Cost control — stop the instance when you're not using it

```bash
INSTANCE_ID=$(terraform output -raw instance_id)

# Stop (data preserved, EIP stays attached, billing drops to ~$0.40/day)
aws ec2 stop-instances --instance-ids "$INSTANCE_ID"

# Start it again when you need it (the EIP stays the same)
aws ec2 start-instances --instance-ids "$INSTANCE_ID"

# Check current state
aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text
```

The docker-compose stack auto-starts on instance boot because Docker is
enabled (`systemctl enable docker`) and the compose services use the default
restart policy. After `start-instances` give it ~60 seconds for containers
to come back up.

## Updating after a code push

When you push new code to the GitHub repo, the EC2 won't pick it up
automatically. SSH in and pull:

```bash
ssh -i ~/.ssh/ariadne-demo.pem ec2-user@<PUBLIC-IP>
cd ~/ariadne
git pull
cd lineage-platform
docker compose up -d --build
```

`--build` rebuilds any image whose source files changed; unchanged services
restart in seconds.

## Tear it all down

```bash
terraform destroy
```

This removes the EC2, the EBS volume (and your data), the EIP, and the
security group. The key pair stays in your AWS account — Terraform doesn't
own it. Total time: ~90 seconds.

## What this deploy does NOT do

This is intentionally minimal. For a production deploy you'd add, in order:

1. **HTTPS + a domain name.** Install Nginx + Let's Encrypt on the EC2, register a domain (Route 53, $0.50/mo), point an A record at the EIP. Once that's working, lock ports 3000 and 8000 down to the security group's internal scope and only expose 80/443 publicly.
2. **Basic auth on the gateway.** Right now anyone with the URL can run Cypher against your graph. Nginx `auth_basic` is 5 lines. Or graduate to Cognito if you want real user accounts.
3. **EBS snapshots.** AWS Backup or DLM with a daily snapshot + 7-day retention. ~$0.50/mo for a 20 GB working set.
4. **Auto-stop schedule.** EventBridge rule stops the instance at 11 PM and starts it at 8 AM weekdays. Cuts the EC2 bill by ~70%.
5. **Move state to a remote backend.** Right now Terraform state lives in your local `deploy/aws/` directory. Move it to an S3 bucket + DynamoDB lock table as soon as more than one person works on the deploy.
6. **Managed databases.** RDS for Postgres, Neo4j Aura (managed Neo4j SaaS) for the graph. Adds $40-100/mo but gives you point-in-time recovery + automatic patching + Multi-AZ.
7. **Move to ECS Fargate or EKS.** Single EC2 = single point of failure. Containers want an orchestrator with restart guarantees and a load balancer in front.

Each of these is a fork in the road, not a "must do next" — pick what your use case actually needs.

## Troubleshooting

**`terraform apply` fails with "InvalidKeyPair.NotFound"**
Your `key_pair_name` doesn't match an existing key pair **in the region** you specified. Either create one in that region or change `aws_region`.

**The frontend URL returns "connection refused"**
Cloud-init is still running. Wait 5-8 minutes after `terraform apply` finishes. `ssh ... 'sudo tail -f /var/log/user-data.log'` shows what step it's on.

**The frontend loads but can't talk to the gateway**
The frontend was built with the wrong `NEXT_PUBLIC_GATEWAY_URL`. SSH in and rebuild:
```bash
cd ~/ariadne/lineage-platform
cat .env  # check the URL
docker compose up -d --build frontend
```

**SSH fails with "Permission denied (publickey)"**
- Is the `.pem` permission right? `chmod 400 ~/.ssh/ariadne-demo.pem`
- Are you using the right username? Amazon Linux 2023 = `ec2-user`, not `root` or `ubuntu`.

**I changed my home IP and can't SSH anymore**
Update `operator_ip_cidr` in `terraform.tfvars`, run `terraform apply`. Only the security group changes; the instance is untouched.

**Bill is climbing higher than expected**
Check `aws ce get-cost-and-usage` or the Cost Explorer in the Console. The most common surprise is data transfer (NAT gateway, large downloads). This module doesn't create a NAT gateway, so you're probably fine — but a tail of `docker compose logs` revealing the parsers are doing something unexpected is worth a look.

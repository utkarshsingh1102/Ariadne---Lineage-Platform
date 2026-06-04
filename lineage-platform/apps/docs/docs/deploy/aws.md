---
title: AWS
sidebar_label: AWS
---

# Deploy → AWS

A single-EC2 Terraform deployment lives in
[`deploy/aws/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/deploy/aws).

## What it provisions

- **EC2** instance (`t3.medium` default; configurable). Amazon Linux 2023.
- **EBS** root volume, 30 GB gp3.
- **Elastic IP** so the public IP survives stop / start.
- **Security group** with split SSH (operator IP) and demo access
  (configurable CIDR) rules.
- **Cloud-init user data** that installs Docker, clones the repo,
  builds the stack, and brings it up.

## Cost (us-east-1, on-demand)

| State | Approximate hourly | Monthly |
|---|---|---|
| Running (`t3.medium` + EBS + EIP attached) | ~$0.046 | ~$33 |
| Stopped (EBS + EIP attached) | ~$0.0083 | ~$6 |

Stop the instance on nights & weekends to land near the lower number.
[Operations](/deploy/operations) lists the commands.

## Run it

```bash
cd deploy/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set key_pair_name and operator_ip_cidr (your IP /32)
terraform init
terraform apply
```

After ~5 minutes the cloud-init script finishes. The Terraform
outputs print the public IP; the frontend is at
`http://<public_ip>:3000` and the docs at `http://<public_ip>:3002`.

## Updating the deploy

```bash
ssh -i ~/.ssh/<key> ec2-user@<public_ip>
cd ~/ariadne && git pull
cd lineage-platform && sudo docker compose up -d --build <service>
```

## See also

- [Operations](/deploy/operations) — stop / start / log tailing.
- [`deploy/aws/README.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/deploy/aws/README.md) — the source-of-truth runbook.
- [`deploy/aws/command.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/deploy/aws/command.md) — the cheatsheet that lives next to the Terraform.

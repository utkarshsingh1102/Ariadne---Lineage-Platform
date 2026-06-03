###############################################################################
# Ariadne Lineage Platform — single-EC2 demo deployment on AWS
#
# Provisions one EC2 instance (Amazon Linux 2023, t3.medium by default), an
# Elastic IP, a security group that exposes the demo ports to the operator's
# IP only, and a cloud-init script that installs Docker, clones the repo, and
# brings up the docker-compose stack.
#
# This is INTENTIONALLY minimal — see deploy/aws/README.md for the production
# upgrade path (Nginx + Let's Encrypt + Route 53 + EBS snapshots).
###############################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Latest Amazon Linux 2023 AMI for the target region
# ---------------------------------------------------------------------------
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ---------------------------------------------------------------------------
# Security group
# - SSH (22) is ALWAYS locked to the operator's IP.
# - Frontend (3000) + gateway (8000) open to demo_access_cidr, which defaults
#   to the operator's IP. Set demo_access_cidr = "0.0.0.0/0" in tfvars to
#   publish the demo on the public internet.
# ---------------------------------------------------------------------------
locals {
  demo_cidr = coalesce(var.demo_access_cidr, var.operator_ip_cidr)
}

resource "aws_security_group" "this" {
  name        = "${var.project_name}-sg"
  description = "Ariadne lineage platform - single-EC2 demo"

  ingress {
    description = "SSH from operator IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.operator_ip_cidr]
  }

  ingress {
    description = "Frontend (Next.js)"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = [local.demo_cidr]
  }

  ingress {
    description = "Gateway (FastAPI)"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [local.demo_cidr]
  }

  egress {
    description = "All outbound (docker pulls, apt, git clone, etc.)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-sg"
    Project = var.project_name
  }
}

# ---------------------------------------------------------------------------
# EC2 instance — runs the entire docker-compose stack
# ---------------------------------------------------------------------------
resource "aws_instance" "this" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  key_name                    = var.key_pair_name
  vpc_security_group_ids      = [aws_security_group.this.id]
  associate_public_ip_address = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    delete_on_termination = true
    encrypted             = true
  }

  # cloud-init: install docker, clone the repo, write .env with the public
  # DNS, bring the compose stack up.
  user_data = templatefile("${path.module}/user-data.sh", {
    repo_url    = var.repo_url
    repo_branch = var.repo_branch
  })

  # Force replacement when user-data changes (so re-running `terraform apply`
  # after editing the bootstrap actually re-bootstraps the instance).
  user_data_replace_on_change = true

  tags = {
    Name    = "${var.project_name}-ec2"
    Project = var.project_name
  }
}

# ---------------------------------------------------------------------------
# Elastic IP — stable public address that survives stop/start
# ---------------------------------------------------------------------------
resource "aws_eip" "this" {
  instance = aws_instance.this.id
  domain   = "vpc"

  tags = {
    Name    = "${var.project_name}-eip"
    Project = var.project_name
  }
}

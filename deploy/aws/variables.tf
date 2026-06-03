###############################################################################
# Inputs — provide overrides via terraform.tfvars (not committed)
###############################################################################

variable "aws_region" {
  description = "AWS region to deploy into. Pick the one closest to you."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Used as a name prefix on every resource for easy identification + teardown."
  type        = string
  default     = "ariadne-lineage"
}

variable "instance_type" {
  description = "EC2 instance type. t3.medium (2 vCPU / 4 GB) is the floor — Neo4j + Postgres + 4 parsers + gateway + frontend won't fit comfortably on anything smaller."
  type        = string
  default     = "t3.medium"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GiB. 30 is enough for the OS, Docker images (~3 GB), and a small lineage dataset. Increase if you plan to parse a lot."
  type        = number
  default     = 30
}

variable "key_pair_name" {
  description = "Name of an EXISTING EC2 key pair in this region. Create one in the AWS Console (EC2 > Key Pairs) first and download the .pem — Terraform won't create it for you because the private key would otherwise live in state."
  type        = string
}

variable "operator_ip_cidr" {
  description = "Your public IP in CIDR notation, e.g., '203.0.113.42/32'. SSH is ALWAYS locked to this — never set it to 0.0.0.0/0. Find your IP at https://checkip.amazonaws.com/ then append '/32'."
  type        = string

  validation {
    condition     = can(regex("^[0-9.]+/[0-9]+$", var.operator_ip_cidr))
    error_message = "operator_ip_cidr must look like '203.0.113.42/32', not '203.0.113.42' or 'me'."
  }
}

variable "demo_access_cidr" {
  description = "Who can reach the frontend (port 3000) and gateway (port 8000). Defaults to operator_ip_cidr (private demo). Set to '0.0.0.0/0' to expose the demo to the public internet — be aware the gateway has no authentication, so anyone with the URL can run read-only Cypher and trigger parses."
  type        = string
  default     = null

  validation {
    condition     = var.demo_access_cidr == null || can(regex("^[0-9.]+/[0-9]+$", var.demo_access_cidr))
    error_message = "demo_access_cidr must look like '203.0.113.42/32' or '0.0.0.0/0', or be omitted."
  }
}

variable "repo_url" {
  description = "Git URL of the Ariadne repo. Fork it and point this at your fork if you want to deploy your own changes."
  type        = string
  default     = "https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git"
}

variable "repo_branch" {
  description = "Branch to check out on the EC2."
  type        = string
  default     = "main"
}

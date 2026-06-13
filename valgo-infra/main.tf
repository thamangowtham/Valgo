/**
 * Valgo — root Terraform module
 *
 * Personal-scale deploy: single AZ, NAT Gateway with whitelisted EIP,
 * ECS Fargate for stateless services, EC2 for execution nodes.
 *
 * Usage:
 *   cd infra/envs/dev (or prod)
 *   terraform init
 *   terraform plan
 *   terraform apply
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "valgo"
      Environment = var.env
      ManagedBy   = "terraform"
    }
  }
}

module "network" {
  source = "./modules/network"

  env        = var.env
  aws_region = var.aws_region
  vpc_cidr   = "10.0.0.0/16"
  az         = var.availability_zone
}

module "data" {
  source = "./modules/data"

  env                = var.env
  vpc_id             = module.network.vpc_id
  private_subnet_ids = [module.network.private_subnet_id]
  redis_node_type    = var.redis_node_type
}

module "auth" {
  source = "./modules/auth"

  env = var.env
}

module "compute" {
  source = "./modules/compute"

  env                  = var.env
  vpc_id               = module.network.vpc_id
  public_subnet_ids    = [module.network.public_subnet_id]
  private_subnet_ids   = [module.network.private_subnet_id]
  redis_endpoint       = module.data.redis_endpoint
  dynamodb_table_arns  = module.data.dynamodb_table_arns
  secrets_manager_arns = module.auth.secrets_manager_arns
}

# ----------------------------------------------------------------------------
# Outputs visible at the root level
# ----------------------------------------------------------------------------
output "whitelist_this_ip_with_broker" {
  description = "Static EIP on the NAT Gateway. Register THIS with your broker before going live."
  value       = module.network.nat_eip
}

output "redis_endpoint" {
  value = module.data.redis_endpoint
}

output "admin_panel_url" {
  value = module.compute.admin_alb_url
}

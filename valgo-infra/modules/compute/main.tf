/**
 * Compute: ECS Fargate cluster (stateless services) + EC2 execution nodes.
 *
 * This module is intentionally light. Expand per-service definitions
 * (task definitions, services, target groups) as you ship each component.
 */

resource "aws_ecs_cluster" "main" {
  name = "valgo-${var.env}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ALB for public-facing services (admin panel, webhook handler)
resource "aws_security_group" "alb" {
  name   = "valgo-${var.env}-alb-sg"
  vpc_id = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "public" {
  name               = "valgo-${var.env}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

# Internal NLB for execution router (decision engine → router) — low-latency L4
resource "aws_lb" "internal_router" {
  name               = "valgo-${var.env}-router-nlb"
  internal           = true
  load_balancer_type = "network"
  subnets            = var.private_subnet_ids
}

# EC2 launch template for execution nodes — reside in private subnet,
# inside the cluster placement group, share the NAT EIP for broker egress
data "aws_ami" "amzn2" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }
}

resource "aws_security_group" "exec_node" {
  name   = "valgo-${var.env}-exec-node-sg"
  vpc_id = var.vpc_id

  # Inbound from the internal router NLB only
  ingress {
    from_port = 8095
    to_port   = 8095
    protocol  = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Output the public ALB DNS for the admin panel
output "admin_alb_url" {
  value = "http://${aws_lb.public.dns_name}"
}

output "ecs_cluster_arn" { value = aws_ecs_cluster.main.arn }
output "internal_router_dns" { value = aws_lb.internal_router.dns_name }

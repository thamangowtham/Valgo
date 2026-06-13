variable "env" {
  description = "Environment name: dev or prod"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-south-1"
}

variable "availability_zone" {
  description = "AZ to deploy into. Single-AZ for low latency."
  type        = string
  default     = "ap-south-1a"
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.small"
}

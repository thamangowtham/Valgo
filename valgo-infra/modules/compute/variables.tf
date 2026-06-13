variable "env" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "private_subnet_ids" { type = list(string) }
variable "redis_endpoint" { type = string }
variable "dynamodb_table_arns" { type = map(string) }
variable "secrets_manager_arns" { type = map(string) }

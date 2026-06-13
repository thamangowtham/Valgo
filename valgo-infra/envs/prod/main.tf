module "valgo" {
  source = "../.."

  env             = "prod"
  aws_region      = "ap-south-1"
  redis_node_type = "cache.m6g.large"
}

output "whitelist_ip" { value = module.valgo.whitelist_this_ip_with_broker }
output "redis_endpoint" { value = module.valgo.redis_endpoint }
output "admin_panel_url" { value = module.valgo.admin_panel_url }

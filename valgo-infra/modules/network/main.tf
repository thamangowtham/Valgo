/**
 * Network: VPC + single-AZ subnets + NAT Gateway with Elastic IP.
 *
 * The NAT Gateway's EIP is THE static IP that gets whitelisted with the broker
 * per SEBI's April 2026 rules. All execution-node egress to the broker flows
 * through this address.
 */

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = "valgo-${var.env}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "valgo-${var.env}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone       = var.az
  map_public_ip_on_launch = false  # we don't need public IPs on instances
  tags                    = { Name = "valgo-${var.env}-public" }
}

resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 2)
  availability_zone = var.az
  tags              = { Name = "valgo-${var.env}-private" }
}

# NAT Gateway with persistent EIP — this is THE whitelisted IP
resource "aws_eip" "nat" {
  domain = "vpc"
  tags = {
    Name        = "valgo-${var.env}-nat-eip"
    Description = "Whitelisted with broker. DO NOT release."
  }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "valgo-${var.env}-nat" }
  depends_on    = [aws_internet_gateway.main]
}

# Public route table → IGW
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "valgo-${var.env}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Private route table → NAT GW (so all egress carries the whitelisted EIP)
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "valgo-${var.env}-private-rt" }
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

# Cluster placement group — keeps nodes physically close for low latency
resource "aws_placement_group" "exec_nodes" {
  name     = "valgo-${var.env}-exec-nodes"
  strategy = "cluster"
}

# Outputs
output "vpc_id" { value = aws_vpc.main.id }
output "public_subnet_id" { value = aws_subnet.public.id }
output "private_subnet_id" { value = aws_subnet.private.id }
output "nat_eip" { value = aws_eip.nat.public_ip }
output "placement_group_name" { value = aws_placement_group.exec_nodes.name }

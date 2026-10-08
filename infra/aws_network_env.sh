#!/usr/bin/env bash
# Prints the network values serverless.yml needs, for the DEFAULT VPC of the current AWS region.
#   eval "$(bash infra/aws_network_env.sh)"
# Picks two subnets in different AZs and the VPC's main route table. Review the output before using it.
set -euo pipefail
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC_ID" != "None" ] || { echo "echo 'no default VPC in this region' >&2; false"; exit 1; }
# one subnet per AZ, first two AZs
read -r SUBNET_A SUBNET_B < <(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'sort_by(Subnets,&AvailabilityZone)[].[AvailabilityZone,SubnetId]' --output text \
  | awk '!seen[$1]++ {print $2}' | head -2 | paste -sd' ')
ROUTE_TABLE_ID=$(aws ec2 describe-route-tables --filters Name=vpc-id,Values="$VPC_ID" Name=association.main,Values=true \
  --query 'RouteTables[0].RouteTableId' --output text)
echo "export VPC_ID=$VPC_ID SUBNET_A_ID=$SUBNET_A SUBNET_B_ID=$SUBNET_B ROUTE_TABLE_ID=$ROUTE_TABLE_ID"

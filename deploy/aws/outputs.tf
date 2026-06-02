###############################################################################
# Outputs — printed by `terraform apply` and queryable via `terraform output`
###############################################################################

output "instance_id" {
  description = "EC2 instance id. Use this with `aws ec2 stop-instances --instance-ids ...` to save money."
  value       = aws_instance.this.id
}

output "public_ip" {
  description = "Elastic IP. Stable across stop/start."
  value       = aws_eip.this.public_ip
}

output "public_dns" {
  description = "EC2 public DNS name."
  value       = aws_instance.this.public_dns
}

output "ssh_command" {
  description = "Ready-to-paste SSH command. Replace <your-key>.pem with the path to the private key matching key_pair_name."
  value       = "ssh -i ~/.ssh/${var.key_pair_name}.pem ec2-user@${aws_eip.this.public_ip}"
}

output "frontend_url" {
  description = "Open this once user-data finishes (~5-8 minutes after apply)."
  value       = "http://${aws_eip.this.public_ip}:3000"
}

output "gateway_health_url" {
  description = "Quick health check — should return {\"status\":\"ok\",...}."
  value       = "http://${aws_eip.this.public_ip}:8000/health"
}

output "user_data_log_hint" {
  description = "If something looks wrong, SSH in and tail this — it's the cloud-init log."
  value       = "ssh ec2-user@${aws_eip.this.public_ip} 'sudo tail -f /var/log/user-data.log'"
}

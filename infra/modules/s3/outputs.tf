output "bucket_name" {
  description = "Nombre del bucket de artefactos."
  value       = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  description = "ARN del bucket de artefactos."
  value       = aws_s3_bucket.this.arn
}

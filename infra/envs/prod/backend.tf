# Estado remoto en S3 con lock en DynamoDB (ambos creados por infra/bootstrap).
# El bucket lleva el ID de la cuenta, así que se pasa al hacer init:
#   terraform init -backend-config=backend.hcl
# (copia backend.hcl.example a backend.hcl y pon la salida `backend_config` del bootstrap).
terraform {
  backend "s3" {
    key            = "envs/prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "trading-maxxing-tflock"
    encrypt        = true
  }
}

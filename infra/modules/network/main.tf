# Red de trading_maxxing: una VPC con 2 subredes públicas y 2 privadas en 2 zonas.
#
# Decisión de costo: NO hay NAT Gateway. Un NAT cuesta ~0.045 USD/h (~32.85 USD/mes) por
# zona más 0.045 USD por GB procesado; con 2 zonas serían ~66 USD/mes, más de la mitad del
# presupuesto. En su lugar las tareas de Fargate corren en las subredes públicas con IP
# pública (0.005 USD/h cada una, ~3.65 USD/mes) y security groups que no aceptan tráfico
# entrante desde internet (solo el ALB entra, y solo al puerto de la api).
#
# Las subredes privadas no tienen salida a internet. Quedan reservadas para recursos que
# no la necesitan (ElastiCache o RDS en el futuro, o tareas con VPC endpoints).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name}-igw" }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.cidr_block, 8, count.index)
  map_public_ip_on_launch = false # Fargate asigna la IP pública por servicio (assign_public_ip)

  tags = { Name = "${var.name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.cidr_block, 8, count.index + 10)

  tags = { Name = "${var.name}-private-${local.azs[count.index]}", Tier = "private" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Tabla privada sin ruta por defecto: aislada de internet a propósito.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name}-private" }
}

resource "aws_route_table_association" "private" {
  count = 2

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# El security group por defecto de la VPC queda sin reglas para que nadie lo use por error.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name}-default-locked" }
}

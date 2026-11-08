# Secretos de la aplicación en Secrets Manager.
#
# Terraform crea solo el "contenedor" de cada secreto, NO su valor: así las llaves nunca
# quedan en el estado ni en el repo. Los valores se cargan una vez a mano (ver
# infra/README.md, sección "Cargar los secretos"), como JSON con las llaves de var.secrets.
#
# Cada secreto cuesta 0.40 USD/mes.

resource "aws_secretsmanager_secret" "this" {
  for_each = var.secrets

  name                    = "${var.name_prefix}/${each.key}"
  description             = each.value.description
  recovery_window_in_days = var.recovery_window_in_days

  tags = { Secret = each.key }
}

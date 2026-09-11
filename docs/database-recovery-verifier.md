# Verificador de integridad de recuperación

`db_recovery_verify` prepara y compara una evidencia mínima para el ejercicio
T8 de recuperación de PostgreSQL. Trabaja con la base que ya está configurada
para Django y solo registra metadatos agregados:

- el conjunto exacto de migraciones aplicadas y las migraciones de código pendientes;
- el conteo de las tablas del esquema actual, incluida `ClanMembership`;
- los conteos y puntos de `ActionLog` agrupados por estado y atribución de clan;
- una huella SHA-256 por registro histórico, construida con IDs, estado y puntos;
- los conteos de referencias foráneas huérfanas de `ActionLog`;
- la presencia de dos IDs sintéticos de marcador.

El esquema y sus reglas de producto siguen siendo un borrador. Esta herramienta
no ejecuta una restauración, no aplica migraciones, no modifica datos y no
demuestra que PITR, Neon o Cloud Run funcionen. La evidencia debe obtenerse en
una base desechable del ensayo de recuperación y revisarse antes de usarla en
un ambiente compartido.

## Flujo

El marcador `pre` representa un `ActionLog` sintético creado antes del punto T.
El marcador `post` representa un `ActionLog` sintético creado después de T. El
comando de baseline exige que `pre` exista y que `post` todavía no exista. Los
dos argumentos deben ser UUIDs de los registros sintéticos; no se imprimen
otros campos del registro.

Define `PRE_MARKER_ID` con el UUID de un ActionLog sintético existente y `POST_MARKER_ID` con un UUID reservado que todavía no existe. Captura el baseline antes de la operación de recuperación:

```sh
python app/manage.py db_recovery_verify \
  --write-baseline /tmp/recovery-baseline.json \
  --pre-marker-id "$PRE_MARKER_ID" \
  --post-marker-id "$POST_MARKER_ID" \
  --timeout 10
```

Después de restaurar la copia o rama desechable, compara el estado recuperado:

```sh
python app/manage.py db_recovery_verify \
  --baseline /tmp/recovery-baseline.json \
  --timeout 10
```

Una comparación correcta imprime `RECOVERY_VERIFY OK`. Una diferencia imprime
solo códigos estables, como `MIGRATION_SET_MISMATCH`,
`TABLE_COUNT_MISMATCH`, `ACTION_LOG_STATUS_POINTS_MISMATCH`,
`ACTION_LOG_ATTRIBUTION_MISMATCH`, `ACTION_LOG_HISTORY_MISMATCH`,
`ACTION_LOG_FK_MISMATCH`,
`PRE_MARKER_MISSING` o `POST_MARKER_PRESENT`. Los errores de conexión,
esquema incompleto, lectura y escritura del archivo tienen diagnósticos
genéricos; no se incluye el texto del driver, la URL, credenciales, PII ni
valores de filas.

El baseline se valida antes de consultar la base: debe tener una migración
completa, cero referencias huérfanas y marcadores distintos. La ruta de salida
se crea exclusivamente con permisos 0600; si ya existe, el comando falla para
preservar la evidencia anterior.

`--timeout` acepta de `0.1` a `60` segundos. El límite se aplica a la conexión,
`statement_timeout`, `lock_timeout` y al tiempo total del proceso, incluyendo
fallback de direcciones. La transacción se marca como `READ ONLY`; el comando
no llama `migrate`, `makemigrations`, `flush` ni ninguna operación DML. La
ejecución directa requiere Linux o macOS por `SIGALRM`; en Windows se debe
ejecutar dentro de Docker.

## Pruebas locales

Las pruebas usan PostgreSQL 18 en una base y proyecto Compose aislados. El
nombre del proyecto y el puerto se mantienen separados de otros contenedores:

```sh
POSTGRES_PORT=55462 docker-compose -p recovery-verifier up -d db
POSTGRES_PORT=55462 docker-compose -p recovery-verifier run --rm app \
  pytest app/tests/test_recovery_verifier.py
POSTGRES_PORT=55462 docker-compose -p recovery-verifier down -v
```

La limpieza final elimina únicamente los contenedores y volumenes del proyecto
`recovery-verifier`. No uses la base compartida `test_greeniteso` para este
ejercicio.

## Selección de una rama temporal

El guard de ambientes desplegados solo permite los endpoints canónicos de dev/staging/production. Una rama de recuperación temporal debe verificarse desde un proceso operador local separado (`DJANGO_DEPLOYED=false`, `DJANGO_ENV=dev`), con la credencial app heredada de la rama fuente y `DATABASE_URL` del destino explícitamente revisado, incluyendo `sslmode=verify-full`. El rol app tiene permisos DML; es este comando el que impone una transacción READ ONLY. Un rol adicional de solo lectura no se ha provisionado. Carga los valores desde un archivo privado fuera del repositorio o un gestor de secretos; no pegues la URL en el comando ni cambies la configuración del servicio desplegado. Ejecuta únicamente este verificador, que impone transacción de solo lectura.

Coordina una ventana sin otras escrituras entre la captura del baseline y el punto T elegido; después crea el marcador posterior y restaura a T. Conserva junto al baseline el ID de rama, el timestamp UTC y la duración del ensayo. El timestamp del JSON describe la captura y no sustituye la evidencia del punto de restauración de Neon. Cambiar tráfico a un endpoint restaurado requiere actualizar y revisar el inventario canónico de Backend e Infra; este comando no realiza ese cambio.

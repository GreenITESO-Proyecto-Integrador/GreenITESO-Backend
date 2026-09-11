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

Captura el baseline antes de la operación de recuperación:

```sh
python app/manage.py db_recovery_verify \
  --write-baseline /tmp/recovery-baseline.json \
  --pre-marker-id <uuid-pre-t> \
  --post-marker-id <uuid-post-t> \
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

# Smoke de PostgreSQL

`db_smoke` comprueba el acceso de Django al PostgreSQL configurado para el
proceso. Tiene un límite de tiempo finito y ejecuta una transacción
`REPEATABLE READ` marcada como `READ ONLY` con estas lecturas mínimas:

1. `SELECT 1` para comprobar la conexión.
2. `User.objects.count()` y `ActionLog.objects.count()` para comprobar las
   tablas principales del esquema T9a.

No ejecuta `migrate`, `makemigrations`, `flush` ni ninguna operación de
escritura. El comando es de diagnóstico y no carga datos.

En releases, `--check-grants` agrega una verificación de solo lectura: exige
que existan todas las tablas de modelos Django gestionados (incluidas las
M2M autogeneradas), `USAGE` pero no `CREATE` en `public`,
`SELECT`/`INSERT`/`UPDATE`/`DELETE` pero no `TRUNCATE` en todas las tablas
públicas, y `USAGE` en todas las secuencias públicas. Es una inspección de
privilegios, no un intento de escritura. Una conexión con el dueño/superusuario
fallará deliberadamente con `GRANT_MISMATCH`; usar el rol app pooled.
El migrador debe configurar `ALTER DEFAULT PRIVILEGES FOR ROLE` para que
tablas y secuencias nuevas mantengan estos grants después de futuras
migraciones. El rol app actual conserva DML incluso sobre `django_migrations`;
esto refleja los grants observados, no una certificación de privilegio mínimo.
Revisar esa excepción antes de production sin cambiar permisos de un ambiente
compartido fuera de una ventana coordinada.

## Uso local

Con el PostgreSQL 18 local levantado y las migraciones aplicadas:

```sh
python app/manage.py db_smoke --timeout 5
```

También se puede ejecutar dentro del servicio `app` de Compose:

```sh
docker compose exec -T app python app/manage.py db_smoke --timeout 5
```

`--timeout` acepta de `0.1` a `30` segundos y por defecto usa 5 segundos.
Ese valor limita la conexión mediante `connect_timeout`, y dentro de la
transacción de lectura fija temporalmente `statement_timeout` y `lock_timeout`.
Los dos últimos se aplican con `set_config(..., true)` después de abrir la
conexión, por lo que no dependen de opciones de arranque que un pooler
transaccional pueda rechazar o descartar. El tiempo total del proceso también
incluye resolución y fallback de direcciones. Un tiempo agotado se reporta como
`CONNECTION_FAILURE`.
El límite total usa `SIGALRM`, por lo que la ejecución directa requiere Linux
o macOS; en Windows se debe usar el contenedor Docker documentado abajo.

La configuración debe seleccionar explícitamente `DJANGO_ENV`,
`DJANGO_SECRET_KEY`, `DJANGO_DEPLOYED`, `DJANGO_CONNECTION_ROLE`,
`DJANGO_ALLOWED_HOSTS` y `DATABASE_URL`. El comando no elige un ambiente por
sí solo y no imprime `DATABASE_URL`, credenciales, nombres de usuario ni datos
de filas.

## Diagnóstico

Una salida exitosa contiene `DB_SMOKE OK`, el resultado de `SELECT 1`, los dos
conteos agregados y `ssl_cliente`. Los conteos no contienen PII.

- `CONNECTION_FAILURE`: no se pudo abrir o mantener la conexión dentro del
  límite. Revisar el ambiente y la configuración de conexión fuera del log
  del comando.
- `MISSING_MIGRATIONS`: PostgreSQL respondió, pero falta una tabla o columna
  requerida por el esquema. Aplicar las migraciones revisadas con el rol
  migrator mediante el pipeline o el procedimiento local documentado; el
  smoke no las aplica automáticamente.
- `READ_FAILURE`: la conexión funcionó, pero una consulta de lectura falló.
- `GRANT_MISMATCH` (con `--check-grants`): falta una tabla gestionada o un
  permiso esperado, o el rol posee `CREATE`/`TRUNCATE`. Comprobar el rol app y
  los grants por defecto del migrador; no usar el dueño de la base como app.

El estado TLS se obtiene del cliente libpq mediante
`connection.connection.pgconn.ssl_in_use` (psycopg 3). En Neon, `pg_stat_ssl` puede mostrar `false`
para una conexión que sí usa TLS entre el cliente y el proxy; por eso esa vista
del servidor no se usa como evidencia de TLS del cliente.

Esta comprobación local no certifica Cloud Run, Neon, roles remotos ni los tres
ambientes. Infra debe ejecutar y conservar esa evidencia por ambiente, sin
incluir secretos ni datos personales.

## Verificación

Las pruebas de integración usan PostgreSQL 18 y una base aislada. Ejecutar:

```sh
docker compose up -d db
docker compose run --rm app python app/manage.py migrate --no-input
docker compose run --rm app pytest app/tests/test_db_smoke.py
```

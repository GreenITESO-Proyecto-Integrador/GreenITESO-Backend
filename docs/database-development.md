# Desarrollo de base de datos

Esta guía es el contrato de trabajo para los tres equipos de Backend. Un clon nuevo debe poder trabajar sin una cuenta de Neon ni credenciales compartidas: cada persona usa PostgreSQL 18 local y CI crea una base PostgreSQL desechable. Neon solo se usa para los ambientes desplegados `dev`, `staging` y `production`.

## Arranque de un clon nuevo

Requisitos: Git, Docker Desktop (con Compose) y, opcionalmente, VS Code Dev Containers.

```sh
git clone https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Backend.git
cd GreenITESO-Backend
cp .env.example .env             # obligatorio; nunca se commitea
make compose-up
# en otra terminal:
make migrate
make makemigrations-check
make test
```

El contrato de foundation usa `compose.yaml`, PostgreSQL 18 y pytest dentro del contenedor. `make compose-down` detiene los contenedores y conserva el volumen local. El ejemplo fija `DJANGO_ENV=dev`, `DJANGO_DEPLOYED=false` y `DJANGO_CONNECTION_ROLE=app`; incluye una clave local, hosts y credenciales desechables. No uses URLs de Neon para desarrollo local.

El `DATABASE_URL` local apunta al servicio PostgreSQL de Compose. CI crea una base aislada; no se ejecutan pruebas contra `staging` ni `production`.

## Local, devcontainer y ambientes desplegados

`DJANGO_ENV` identifica uno de los tres ambientes; `DJANGO_DEPLOYED` distingue
la máquina local del servicio desplegado. `dev` desplegado exige las mismas
reglas de SSL y separación de credenciales que staging/production.

| Proceso | Configuración | Credencial |
| --- | --- | --- |
| Local/CI | `DJANGO_ENV=dev`, `DJANGO_DEPLOYED=false`, `DJANGO_CONNECTION_ROLE=app` | PostgreSQL local, sin Neon |
| Cloud Run app | ambiente explícito, `DJANGO_DEPLOYED=true`, rol `app` | solo `DATABASE_URL` pooled del rol app |
| Job de migración | ambiente explícito, `DJANGO_DEPLOYED=true`, comando `make -C app migrate-direct` | solo `DATABASE_URL_UNPOOLED` directo del rol migrator; el comando selecciona rol `direct` |

La app desplegada no recibe la contraseña de migraciones. Ambos procesos
reciben su `DJANGO_SECRET_KEY` y `DJANGO_ALLOWED_HOSTS`; SSL es obligatorio.
Las conexiones empiezan con `CONN_MAX_AGE=0` y sin cursores del lado del servidor.
Las fechas son conscientes de zona; `TIME_ZONE=America/Mexico_City` y `USE_TZ=True`.
Esto configura el reloj, pero no implementa ni ratifica la regla de límite diario P10.

Para Dev Containers: copia `.env.example` a `.env` antes de **Reopen in Container**.
En la terminal del contenedor usa `make -C app migrate`,
`make -C app makemigrations-check` y `make -C app test`; no necesitas Docker
dentro del contenedor. Desde el host, usa los comandos de la sección anterior.

Dos checkouts simultáneos necesitan distintos `COMPOSE_PROJECT_NAME`,
`APP_PORT` y `POSTGRES_PORT`. Los puertos se publican solo en `127.0.0.1`.
`make compose-down` conserva los datos. Para una base nueva al cambiar a una
historia incompatible, usa otro nombre de proyecto y puertos; no borres un
volumen que otro checkout utilice. Si cambias usuario/contraseña local después
de inicializar el volumen, PostgreSQL conserva los valores anteriores.

Esta base inicial todavía no incluye modelos de dominio, datos demo ni login.
`migrate` no crea `auth_user`: T9 debe añadir primero el usuario personalizado.
El recorrido aquí prueba infraestructura y la ruta `/`; la aplicación con datos
requiere T9/T11/T12. Ningún `createsuperuser` o login Firebase se declara listo.

## Qué vive en qué lugar

El esquema relacional y sus cambios viven exclusivamente en modelos y migraciones de Django. `neon.ts`, Neon Auth y los buckets de Neon no son parte de este proyecto. La autenticación acordada es Firebase Authentication; el almacenamiento GCS privado es la propuesta P1 y su integración sigue pendiente. Una rama de Git tampoco transporta filas de PostgreSQL; cada ambiente recibe las migraciones revisadas.

El usuario de Django se decide antes de la primera migración que lo referencie. E2 es dueño de identidad, `User`, `UserProfile`, `Clan` y `ClanMembership`; E1 de acciones, catálogo y gamificación; E3 de campañas, feed y notificaciones. Si un modelo cruza dominios, el dueño del modelo referenciado revisa la dependencia y el PR declara su migración inicial.

P3, P8, P9, P10 y P11 siguen siendo decisiones de producto pendientes. Las implementaciones exploratorias deben permanecer en borradores; no se fusionan ni se aplican en ambientes compartidos sin registrar la decisión. En particular, P3 afecta la atribución histórica; P8 el podio congelado; P9 el `campaign_id`; P10 la ventana diaria; y P11 quién determina el clan privado activo.

## Flujo de modelos y migraciones

1. Actualiza el modelo en tu rama local y ejecuta `makemigrations` dentro del contenedor.
2. Revisa el archivo generado y sus dependencias; no renombres ni borres una migración que ya llegó a un ambiente compartido.
3. Ejecuta desde una base vacía `migrate`, las pruebas y `makemigrations --check --dry-run`.
4. Comprueba una actualización desde la revisión de integración anterior, no solo una instalación limpia.
5. Commitea el modelo y la migración juntos. En el PR explica el cambio de datos, compatibilidad entre versiones vieja/nueva y el procedimiento de reversión o corrección hacia adelante.

Cuando dos ramas tienen hojas de migración incompatibles, pausa el merge: rebasea sobre la rama de integración, conserva ambas operaciones y crea una migración de merge solo si son compatibles. Si hay operaciones que dependen de un orden o de datos existentes, el dueño de cada modelo acuerda una migración de reconciliación. Nunca edites una migración aplicada ni uses `--fake` para esconder divergencia.

La promoción planificada de ambientes es `dev` → `staging` → `production`; los nombres de ramas Git y sus workflows se mantienen en Backend y no deben confundirse con las ramas Neon. La promoción de código no promueve datos: el job de despliegue ejecuta una vez las migraciones con la imagen de release y la conexión directa de migración, antes de enviar tráfico a la nueva revisión. Gunicorn/Cloud Run no ejecuta `migrate` al arrancar cada instancia.

## Lista de revisión para un PR de esquema

- ¿El modelo pertenece al dominio dueño y usa `settings.AUTH_USER_MODEL` cuando corresponde?
- ¿La migración funciona desde cero y desde la revisión de integración anterior?
- ¿Se declararon dependencias entre apps y se revisó la hoja de migraciones?
- ¿Las restricciones están en PostgreSQL, además de validarse en Django? Revisa unicidades, `CheckConstraint`, nullabilidad y `on_delete`.
- ¿Se preservan las referencias históricas de `ActionLog`? Los puntos y la atribución no se recalculan desde membresías actuales.
- ¿Se explican índices para consultas reales, datos de producción y convivencia temporal entre la revisión vieja y la nueva?
- ¿CI pasó `makemigrations --check --dry-run`, `migrate` en PostgreSQL vacío y las pruebas?

Las restricciones entre varias filas (por ejemplo, máximo de clanes privados o el orden de locks) pertenecen al servicio transaccional y sus pruebas de concurrencia. Una opción `choices` o un `CheckConstraint` de una sola fila no reemplaza esa lógica.

## Conexiones

La aplicación desplegada usa la URL pooled de su propio ambiente; el job de migración usa la URL directa. Ambas se inyectan desde Secret Manager/GitHub Environments y nunca se pegan en el repositorio, issues o logs. Para inspecciones de Neon, el comando debe identificar el destino explícitamente:

```sh
neon branches list --project-id "$NEON_PROJECT_ID" --output json
neon databases list --project-id "$NEON_PROJECT_ID" --branch dev --output json
```

La URL de conexión se obtiene por el mecanismo de secretos del ambiente; no
se imprime en la terminal ni se pega en un archivo de onboarding.

En ambientes desplegados, el parser exige `sslmode=verify-full` y configura
`sslrootcert=system` para que libpq 17+ use el almacén de CA del sistema. Una
ruta `sslrootcert` explícita solo se conserva cuando proviene de la URL
revisada del ambiente. Si la URL incluye `channel_binding=require`, el valor
se conserva. `verify-full` valida tanto la cadena de confianza como el nombre
del servidor; consulta la [documentación de SSL de PostgreSQL 18](https://www.postgresql.org/docs/18/libpq-ssl.html)
y la guía de [prevención de suplantación](https://www.postgresql.org/docs/18/preventing-server-spoofing.html).

No se ejecuta `neon env pull` durante el onboarding. Si una tarea de Infra necesita consultar el ambiente, conserva el `.env` local y usa `--no-env-pull`; no reemplaces accidentalmente las credenciales locales ni apuntes a `production`. Los roles de la aplicación y los roles de PostgreSQL son conceptos distintos.

## Simulacro reproducible de conflicto (T10)

El [ensayo con PostgreSQL 18](migration-conflict-rehearsal.md) reproduce dos
hojas incompatibles para el grafo, conserva ambas operaciones mediante un
merge compatible y verifica columnas y datos. Se ejecuta en una base desechable
propia, sin tocar las migraciones del proyecto.

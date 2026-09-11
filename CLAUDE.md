# CLAUDE.md

GreenITESO Backend es una API Django REST. Estas reglas complementan el código y la documentación del repositorio.

## Desarrollo

El entorno soportado es Python 3.14 dentro del Dev Container o Docker Compose. PostgreSQL 18 es obligatorio para desarrollo y CI. El flujo mínimo es:

```sh
cp .env.example .env
make compose-up
# en otra terminal:
make migrate
make makemigrations-check
make test
```

`make compose-down` conserva el volumen local. No agregues SQLite como fallback: las pruebas de `TransactionTestCase`, `SELECT FOR UPDATE` y la semántica de restricciones deben ejecutarse en PostgreSQL.

## Esquema y migraciones

El esquema lo declaran los modelos y las migraciones de Django. Antes de la primera migración dependiente debe existir el `AUTH_USER_MODEL` acordado por E2. Cada PR que cambia modelos incluye la migración, sus dependencias, la prueba desde una base vacía y la prueba de actualización desde la revisión de integración anterior.

No edites ni renombres migraciones aplicadas, no uses `--fake` para ocultar drift y no ejecutes `makemigrations` ni `migrate` desde el arranque de Gunicorn. El job de release aplica una vez las migraciones con la conexión directa antes de cambiar el tráfico.

Los dueños de dominio son E2 (identidad, perfiles y clanes), E1 (acciones, catálogo y gamificación) y E3 (campañas, feed y notificaciones). Coordina las dependencias cruzadas antes de crear una migración de merge. P3/P8/P9/P10/P11 siguen pendientes; el código que explore sus propuestas permanece en borrador y no se aplica a ambientes compartidos.

## Servicios externos

La autenticación acordada de staging/production es Firebase Authentication. GCS privado es la propuesta P1 y su integración sigue pendiente. Neon solo aporta PostgreSQL en `dev`, `staging` y `production`; no se agrega `neon.ts`, Neon Auth, Neon Object Storage ni una rama cloud automática por PR. La aplicación usa una URL pooled y la migración una URL directa, siempre desde secretos del ambiente.

## Calidad

```sh
ruff check app/
ruff format --check app/
make makemigrations-check
make test
```

No pongas credenciales, tokens, URLs completas de conexión, fotos ni payloads personales en el repositorio o en los logs. Antes de abrir un PR, revisa también `.gitignore`, la migración generada y el diff de SQL cuando una restricción o `on_delete` sea relevante.

Usa Conventional Commits para commits y títulos de PR (`feat:`, `fix:`, `docs:`). Conserva las reglas de `app/pyproject.toml`: firmas tipadas, nombres PEP 8, imports ordenados y checks de Django/Bugbear.

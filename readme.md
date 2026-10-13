# GreenITESO Backend

API Django REST de GreenITESO. El desarrollo diario usa PostgreSQL 18 local en Docker; no necesitas iniciar sesión en Neon para clonar, migrar o probar el proyecto.

## Primer arranque

```sh
git clone https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Backend.git
cd GreenITESO-Backend
cp .env.example .env             # obligatorio; no lo comitees
make compose-up
# en otra terminal:
make migrate
make makemigrations-check
make test
```

También puedes abrir el repositorio en el Dev Container. La guía completa de modelos, migraciones y revisión de PR está en [docs/database-development.md](docs/database-development.md).

## Decisiones de datos

- Django migrations es la única fuente ejecutable del esquema.
- CI y cada desarrollador usan una base PostgreSQL aislada; SQLite no es compatible con las pruebas de concurrencia.
- Neon tiene solamente las ramas cloud `dev`, `staging` y `production`; las ramas de Git no contienen datos.
- Microsoft Entra ID es el proveedor de login institucional @iteso.mx (T2-10; ver [docs/auth-microsoft-entra.md](docs/auth-microsoft-entra.md)). GCS privado es la propuesta P1 y aún requiere integración.

No compartas URLs con contraseñas, tokens, fotos o datos reales en commits, issues o logs.

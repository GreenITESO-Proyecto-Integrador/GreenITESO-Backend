# Avatar de perfil y Cloud Storage (T2-21)

`PATCH /api/v1/profile/me/` permite editar `bio`, `preferences`,
`visibility` y `avatar_url`. El binario del avatar nunca pasa por este
backend ni se persiste en la base de datos — solo su URL.

## Decisión del equipo (2026-09-26)

- El almacenamiento acordado es **Google Cloud Storage**. La cuenta/bucket
  **todavía no están provisionados** (P1 en `CLAUDE.md`); esta historia
  implementa la parte que no depende de esa infraestructura.
- La URL del objeto se persiste en Neon (Postgres), en una columna nueva:
  `UserProfile.avatar_url` (`accounts_user_profile.avatar_url`).

## Flujo actual (sin bucket todavía)

1. El cliente sube el archivo a donde sea que hoy resuelva ese problema
   (fuera del alcance de este backend mientras el bucket no exista).
2. El cliente llama `PATCH /api/v1/profile/me/` con `{"avatar_url": "..."}`.
3. El backend valida el string (ver más abajo) y lo guarda tal cual.

## Qué se valida hoy, y qué no

`accounts.serializers._validate_avatar_url` rechaza:

- Cualquier esquema que no sea `https`.
- Cualquier extensión de archivo distinta de `.jpg`, `.jpeg`, `.png`,
  `.webp` (case-insensitive, ignorando query string).

Lo que **no** se valida server-side hoy: el tipo real de archivo ni su
tamaño (el límite acordado con el equipo es ≤5MB). Hacerlo requeriría que
el backend descargue la URL que el cliente le dio para inspeccionarla —
eso es un vector de SSRF (Server-Side Request Forgery) sobre una URL
arbitraria, así que se descartó deliberadamente en vez de agregarse sin
salvaguardas.

## Flujo real, una vez que exista el bucket de GCS

El patrón estándar y seguro para este caso (que evita el SSRF de arriba y sí
permite validar tipo/tamaño antes de que el archivo exista) es:

1. El cliente pide al backend una URL de subida firmada (*signed upload
   URL*), declarando tipo de contenido y tamaño.
2. El backend valida esa declaración contra los límites acordados
   (≤5MB, jpg/jpeg/png/webp) **antes** de firmar, y solo entonces emite la
   URL firmada de GCS.
3. El cliente sube el archivo directamente a GCS con esa URL firmada.
4. El cliente confirma la URL final del objeto contra
   `PATCH /api/v1/profile/me/`, igual que hoy.

Esto necesita `django-storages` (o el SDK de GCS directamente) más las
credenciales del bucket, ninguno de los cuales existe todavía en este
repositorio. Es trabajo de una historia futura, una vez que la cuenta de
GCS esté lista.

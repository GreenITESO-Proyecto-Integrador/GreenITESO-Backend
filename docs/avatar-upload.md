# Fotos de perfil y avatares de clan

Las fotos de usuario (#170, T2-21) y los avatares de clan (#164) usan el mismo
almacenamiento privado, `core.image_storage`. El cliente sube el archivo, pero
nunca elige la llave del objeto. La llave siempre se genera en el backend como
`<prefijo>/<id del dueño>/<uuid>.<ext>`.

## Almacenamiento

- **Desarrollo local** (`DJANGO_DEPLOYED=false`): los archivos van a
  `MEDIA_ROOT`. Un `GCS_EVIDENCE_BUCKET` en el entorno se ignora, así que el
  desarrollo nunca escribe en la nube por accidente.
- **Servicio desplegado** con `GCS_EVIDENCE_BUCKET`: los archivos van a ese
  bucket privado compartido (`greeniteso-<ambiente>-objects`, el mismo de la
  evidencia de acciones), bajo su propio prefijo.
- Validación: firma del archivo (JPG, PNG o WEBP), que el `Content-Type`
  declarado coincida y un máximo de **5 MB**.
- Al reemplazar o quitar una imagen, el archivo anterior se borra **después del
  commit**, así que un request revertido nunca pierde la imagen actual.

## Foto de perfil (#170)

| Endpoint | Quién | Respuesta |
|---|---|---|
| `POST /api/v1/profile/me/avatar/` (multipart, campo `avatar`) | el propio usuario | `200` con el perfil ecológico |
| `DELETE /api/v1/profile/me/avatar/` | el propio usuario | `200` con el perfil; sin foto |
| `GET /api/v1/avatars/<user_id>/` | cualquiera, sin token | `302` a una URL firmada (desplegado) o la imagen (local); `404` si no hay foto subida |
| `PATCH /api/v1/profile/me/` con `avatar_url` | el propio usuario | `200`; guarda un enlace https externo |

- La llave es `avatars/<user_id>/<uuid>.<ext>` y se guarda en
  `UserProfile.avatar_object_key`. El cliente nunca la ve.
- **Solo hay una fuente a la vez.** Subir un archivo borra el enlace pegado
  (`avatar_url`), y pegar un enlace borra el archivo subido.
- `POST /profile/me/avatar/` está limitado a 10 por minuto por usuario
  (`ScopedRateThrottle`, scope `profile_avatar`).

### `avatar_url` en las respuestas

`avatar_url` es lo que el cliente pone en un `<img>`. Con una foto subida es la
URL absoluta de `GET /api/v1/avatars/<user_id>/?v=<versión>`; con un enlace
pegado es ese enlace; sin foto es `""`.

- La URL de `/avatars/` es **estable**: no expira, así que sirve guardada en el
  cliente. Cada vez que se pide, el backend redirige a una URL firmada de
  lectura que dura 10 minutos. El parámetro `v` cambia con cada subida, así que
  una foto nueva nunca queda oculta por una imagen en caché.
- No pide token porque un `<img>` no puede mandar el header `Authorization`, y
  la foto se muestra dondequiera que aparece el usuario.

`avatar_url` aparece en:

- `GET/PATCH /api/v1/profile/me/`
- el usuario de `POST /api/v1/auth/login/` y `GET /api/v1/users/me/` (navbar)
- `members[]` de `GET /api/v1/clans/{id}/` (#191)
- `author` de `GET /api/v1/feed/`
- `GET /api/v1/rankings/users/`, que solo lista perfiles públicos

### Por qué no `POST /api/uploads/`

Ese endpoint (#192, feed) responde con una URL firmada de **1 hora** y de más
de 1000 caracteres. Guardada como `avatar_url`, la foto desaparecería a la hora
y además rebasa el límite de 500 caracteres del campo. Tampoco tiene modo local.

### Enlaces pegados

`accounts.serializers._validate_avatar_url` solo acepta `https` y las
extensiones `.jpg`, `.jpeg`, `.png` y `.webp`. El backend no descarga el
enlace para revisar su tipo o tamaño, porque pedir una URL arbitraria del
cliente desde el servidor sería un vector de SSRF.

## Avatar de clan (#164)

- `POST /api/v1/clans/{id}/avatar/` (multipart, campo `avatar`): solo el líder
  del clan. La llave es `clan-avatars/<clan_id>/<uuid>.<ext>`.
- `DELETE /api/v1/clans/{id}/avatar/`: quita el avatar.
- `avatar_object_key` es de solo lectura en `POST /clans/` y
  `PATCH /clans/{id}/`; un valor enviado por el cliente se ignora.
- Servir la imagen del clan sigue pendiente (#203). Puede usar el mismo
  `core.image_storage.image_response` que la foto de perfil.

# Imágenes del feed

`POST /api/v1/uploads/` (multipart, campo `image`) guarda la imagen de una
publicación y responde `200 {"url": "..."}`. El cliente pone esa URL en
`image_url` al crear el post.

## Reglas

- Solo usuarios autenticados, con un límite de `feed_image_upload` de 10/min.
- JPG, PNG o WEBP de máximo 5 MB. El tipo se detecta por la firma del archivo
  (`core.image_storage.validate_image`) y debe coincidir con el `Content-Type`
  declarado. Pillow verifica que el archivo se pueda decodificar, así que un
  archivo truncado o corrupto da 400.
- Las fotos JPEG de varios celulares, que Pillow reporta como `MPO`, se aceptan.
- Un error del almacenamiento responde 500 sin dejar archivos a medias.

## Almacenamiento y lectura

La llave es `feed/<user_id>/<uuid>.<ext>` y la genera el backend. Se guarda en
`MEDIA_ROOT` en local y en `GCS_EVIDENCE_BUCKET` cuando el servicio está
desplegado, igual que los avatares.

La URL devuelta es `…/api/v1/uploads/<user_id>/<uuid>.<ext>`, que sirve
`FeedImageView`:

- En el servicio desplegado responde `302` a una URL firmada de 10 minutos.
- En local transmite el archivo.
- Si no hay imagen responde `404`.

Es pública, porque un `<img>` no envía el token, y responde con
`Cache-Control: private, max-age=300`.

### Por qué no se guarda una URL firmada

Firmada con IAM en Cloud Run, la URL mide unos 880 caracteres y no cabe en
`Post.image_url` (500), así que crear el post daba 400. Además caducaba
(máximo 7 días) y la imagen desaparecía del feed. La URL estable mide unos
110 caracteres, no caduca y se firma de nuevo en cada lectura.

Los posts creados antes de este cambio conservan su URL firmada anterior.

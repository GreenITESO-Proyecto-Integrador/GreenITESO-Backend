# Propuesta de catálogo v1 para decisión de Product (NO APROBADA)

Estado: **PROPOSAL**. Este documento no es una fixture `APPROVED`, no autoriza
sembrar Neon ni representa valores oficiales del ITESO. Fernando debe aprobar
explícitamente cada valor y la política de validación antes de generar una
fixture versionada y su pin SHA-256.
Fuente consultada el 2026-09-25. Este documento propone un **piloto parcial**, no
un catálogo completo de carreras o acciones.

## Fuentes y alcance

- [ITESO Sustentable](https://sustentabilidad.iteso.mx/) describe como líneas
  institucionales movilidad, agua y residuos. Estas líneas respaldan los
  **temas**, no el nombre exacto de las acciones, los puntos, límites,
  validación ni factores de impacto.
- [Carreras ITESO](https://carreras.iteso.mx/) publica los nombres de
  Ingeniería en Desarrollo de Software, Ingeniería Ambiental y Tecnologías
  Sustentables y Diseño. Se proponen como etiquetas de clanes
  institucionales, no como evidencia de participación de esas carreras.

## Valores propuestos, todos sujetos a aprobación

| Código estable propuesto | Categoría/acción visible propuesta | Puntos propuestos | Límite diario propuesto | Validación propuesta | Factores de impacto propuestos |
| --- | --- | ---: | ---: | --- | --- |
| `mobility-bike-trip` | Movilidad / Viaje al campus en bicicleta | 5 | 1 | `NONE` (declaración) | CO₂ 0, agua 0, plástico 0 |
| `water-refill-bottle` | Agua / Rellenar botella reutilizable | 2 | 3 | `NONE` (declaración) | CO₂ 0, agua 0, plástico 0 |
| `waste-sort-recyclables` | Residuos / Separar residuos reciclables | 3 | 1 | `PHOTO` (auditoría manual) | CO₂ 0, agua 0, plástico 0 |

Los factores quedan en cero porque no se ha ratificado una metodología para
estimar ahorro atribuible por acción. Cero **no codifica** «sin medición» en
el modelo actual: la API y los logs lo muestran como número y conservan un
snapshot histórico. Antes de activar las acciones, Product debe decidir si
ocultar esas métricas, añadir un estado «no medido», o aprobar una metodología
cuantitativa. La foto de residuos requiere GCS privado, políticas de
privacidad/retención y un flujo de auditoría antes de activarla. Se propone
`is_active=false` para las tres acciones al sembrar el catálogo inicial.
Activarlas más tarde requerirá nueva aprobación, nueva versión de fixture/pin
y un mecanismo de actualización revisado; el comando actual rechaza cambios
de filas existentes. Las dos acciones declarativas admiten abuso. P10 fue
ratificada por Fernando el 2026-09-25 **solo en cuanto a la ventana de día
calendario `America/Mexico_City`**; su implementación local aún no está
fusionada ni desplegada, y sigue por decidir si los registros `REJECTED`
consumen el límite. No activar acciones que otorguen puntos hasta probar ese
control en el release objetivo.

Clanes institucionales propuestos (públicos):

| Clave estable propuesta | Nombre visible (fuente ITESO) |
| --- | --- |
| `software-engineering` | Ingeniería en Desarrollo de Software |
| `environmental-engineering` | Ingeniería Ambiental y Tecnologías Sustentables |
| `design` | Diseño |

El loader exige, para la fixture exacta, códigos y nombres de categoría,
descripciones no vacías de acción, descripción y campo `career` de cada clan,
iconos opcionales y metadata `approval` con persona, referencia y hora. Los
textos finales, iconos y formato de `career` son decisiones de Product/E1/E2,
no se infieren de la tabla anterior. Los códigos e IDs derivados son estables;
cambiarlos luego no es una simple corrección de etiqueta.

El onboarding actual crea clanes institucionales a partir de carrera en texto
libre. Eso permite duplicados y puede causar colisiones con los tres clanes
propuestos. E2 y Product deben definir el mapeo canónico, el tratamiento de
otras carreras y variantes ortográficas antes de liberar clanes compartidos.

Antes de aprobar: Product debe confirmar los nombres de acciones, puntos,
límites, validaciones, estado inactivo inicial, representación de factores
sin medición, textos, iconos, los clanes y si tres carreras bastan para el
piloto. E1 revisa acciones y E2 clanes; E3 participa si un cambio afecta
campañas o sus contratos. **La aprobación debe recaer en la fixture exacta**,
no en esta tabla: registrar persona, referencia y hora, revisar el JSON con
los equipos, calcular SHA-256 de sus bytes y configurar el pin del Environment.
Los datos demo (usuarios, campañas, logs) permanecen fuera de staging y
production.

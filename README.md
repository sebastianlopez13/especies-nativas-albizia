# Albizia guachapele — Predicción de Idoneidad Climática

Modelo de nicho ecológico con reentrenamiento automático, servido a través de una aplicación web interactiva sobre Databricks.

## ¿Qué hace?

Estima la idoneidad climática de *Albizia guachapele* a partir de la variable bioclimática `bio_9`, y se actualiza solo cada vez que llegan nuevos registros de presencia — sin necesidad de reentrenar ni redesplegar manualmente.

## Componentes

| Componente | Descripción |
|---|---|
| **Pipeline de datos y modelo** | `notebooks/00_pipeline_automatico.py` — consolida presencias, extrae variables, entrena y registra el modelo en Unity Catalog |
| **Automatización** | Job de Databricks Workflows con trigger `File arrival` sobre la carpeta de presencias |
| **Modelo** | GLM cuadrático, registrado y versionado en `especies_nativas.models.albizia_guachapele_bio9` |
| **Servicio** | Databricks Model Serving — endpoint `albizia-guachapele-bio9` |
| **Aplicación web** | App en Gradio, desplegada con Databricks Apps |

## Flujo en una línea

```
Nuevo CSV de presencia → Job automático → Modelo reentrenado y versionado → Endpoint actualizado → App sirve el resultado más reciente
```

## Documentación

La documentación técnica completa — arquitectura, pipeline paso a paso, despliegue de la app, permisos y solución de problemas — está en:

📄 [`docs/PIPELINE.md`](docs/PIPELINE.md)


Aspecto de la app. Su uso es muy simple:
se le da click dentro de la zona a estimar (rectangulo rojo del mapa),y se espera que el modelo muestre su restultado en el rectangulo inferior

📄 [`docs/vista_app.md`](docs/vista_app.md

## Estado actual

```
Presencias procesadas : 156
AUC (test)             : 0.7896
Versión del modelo     : 5
```

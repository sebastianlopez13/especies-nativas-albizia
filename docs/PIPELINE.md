# Sistema de Predicción de Idoneidad Climática — *Albizia guachapele*

Sistema de modelado de nicho ecológico con reentrenamiento automático, servido a través de una aplicación web interactiva sobre infraestructura de Databricks.

## Tabla de contenidos

1. [Resumen del sistema](#1-resumen-del-sistema)
2. [Arquitectura](#2-arquitectura)
3. [Organización de los datos](#3-organización-de-los-datos)
4. [Pipeline de datos y modelo](#4-pipeline-de-datos-y-modelo)
5. [Automatización del reentrenamiento](#5-automatización-del-reentrenamiento)
6. [Aplicación web](#6-aplicación-web)
7. [Despliegue de la aplicación](#7-despliegue-de-la-aplicación)
8. [Permisos requeridos](#8-permisos-requeridos)
9. [Cómo agregar nuevos registros de presencia](#9-cómo-agregar-nuevos-registros-de-presencia)
10. [Solución de problemas](#10-solución-de-problemas)
11. [Resultados de referencia](#11-resultados-de-referencia)

---

## 1. Resumen del sistema

El sistema estima la **idoneidad climática** de *Albizia guachapele* a partir de la variable bioclimática `bio_9` (temperatura media del trimestre más seco), usando un modelo estadístico entrenado con registros de presencia georreferenciados y background muestreado del área de estudio.

El sistema está compuesto por tres partes que operan de forma integrada:

- **Un pipeline de datos y modelado** que transforma registros crudos de presencia en un modelo entrenado y versionado.
- **Un mecanismo de reentrenamiento automático** que se activa cada vez que se agregan nuevos datos de presencia, sin intervención manual.
- **Una aplicación web** que consulta el modelo más reciente en tiempo real para generar predicciones.

## 2. Arquitectura

```
                     ┌─────────────────────────────┐
                     │   Datos de presencia (CSV)  │
                     │   bronze/presencias/         │
                     └──────────────┬──────────────┘
                                    │  (trigger: llegada de archivo)
                                    ▼
                     ┌─────────────────────────────┐
                     │   Job de reentrenamiento     │
                     │   (Databricks Workflows)     │
                     └──────────────┬──────────────┘
                                    ▼
        ┌───────────┬───────────┬───────────┬───────────────┐
        ▼           ▼           ▼           ▼               ▼
     Bronze      Silver       Gold     Entrenamiento     Registro
  (consolidación (extracción  (split   (comparación de   (Unity Catalog
   y validación)  ambiental +  train/    modelos y        Model Registry)
                  background)  test)     selección)
                                                               │
                                                               ▼
                                                  ┌─────────────────────────┐
                                                  │   Serving Endpoint       │
                                                  │  albizia-guachapele-bio9 │
                                                  └────────────┬────────────┘
                                                               ▼
                                                  ┌─────────────────────────┐
                                                  │   Aplicación web         │
                                                  │   (Databricks Apps)      │
                                                  └─────────────────────────┘
```

**Componentes en Unity Catalog:**

| Recurso | Ubicación |
|---|---|
| Datos crudos de presencia | `/Volumes/especies_nativas/especies_nativas/bronze/presencias/` |
| Raster de variable bioclimática | `/Volumes/especies_nativas/especies_nativas/bronze/bio9_HISTORICO.tif` |
| Tabla Silver | `especies_nativas.silver.dataset_bio9` |
| Tabla Gold | `especies_nativas.gold.dataset_bio9_final` |
| Modelo registrado | `especies_nativas.models.albizia_guachapele_bio9` |
| Serving Endpoint | `albizia-guachapele-bio9` |

## 3. Organización de los datos

```
bronze/
├── bio9_HISTORICO.tif           # raster de la variable bioclimática (bio_9)
└── presencias/                  # carpeta de entrada del pipeline
    ├── presencias_lote1.csv
    ├── presencias_lote2.csv
    └── presencias_YYYY-MM-DD.csv
```

Cada archivo CSV de presencia debe tener las columnas: `species`, `Latitude`, `Longitude`. El pipeline consolida automáticamente **todos** los archivos presentes en la carpeta en cada ejecución, por lo que agregar datos nuevos consiste simplemente en depositar un archivo adicional con nombre único.

## 4. Pipeline de datos y modelo

Implementado en el notebook `notebooks/00_pipeline_automatico.py`, ejecuta las siguientes etapas de forma secuencial:

### Bronze — Ingesta y validación

Consolida todos los CSV de la carpeta de presencias, valida que tengan las columnas requeridas, elimina duplicados y nulos, y descarta los puntos que caen fuera de la extensión del raster.

### Silver — Extracción ambiental y background

Para cada punto de presencia se extrae el valor de `bio_9` del raster. Se genera un conjunto de background (pseudo-ausencias) muestreando todos los píxeles válidos del raster, excluyendo un buffer de aproximadamente 1&nbsp;km alrededor de cada presencia para evitar autocorrelación espacial excesiva.

### Gold — Partición de datos

Se aplica una partición estratificada 80/20 en train/test, preservando la proporción de presencias y background en ambos conjuntos.

### Entrenamiento

Se comparan tres modelos candidatos sobre el conjunto de entrenamiento:

| Modelo | Configuración |
|---|---|
| GLM cuadrático | Regresión logística con términos polinómicos de grado 2 sobre `bio_9` |
| Random Forest | 100 árboles, profundidad máxima 4, regularizado |
| Gradient Boosting | 50 estimadores, profundidad 2, tasa de aprendizaje baja |

El modelo de producción es el **GLM cuadrático**: su curva de respuesta es monotónica y ecológicamente interpretable, evitando los picos artificiales que pueden producir los modelos de árboles con este volumen de datos y una sola covariable. La tabla comparativa completa se registra como evidencia en cada ejecución.

Un umbral mínimo de calidad (`AUC ≥ 0.70`) actúa como control automático: si el modelo entrenado no lo supera, el pipeline se detiene y **no** se promueve una nueva versión a producción.

### Registro del modelo

El modelo aprobado se reentrena con el 100% de los datos disponibles y se registra como una nueva versión en el **Model Registry de Unity Catalog**, exponiendo `predict_proba()` para servir un índice continuo de idoneidad climática en lugar de una clasificación binaria. Las versiones anteriores se conservan íntegramente.

### Actualización del Serving Endpoint

El endpoint `albizia-guachapele-bio9` se actualiza automáticamente para apuntar a la versión recién registrada, mediante una llamada REST a la API de Model Serving.

## 5. Automatización del reentrenamiento

El pipeline se ejecuta automáticamente mediante un **Job de Databricks Workflows** con un trigger de tipo **File arrival**:

- **Job:** `pipeline_automatico_albizia_guachapele`
- **Tarea:** ejecuta `00_pipeline_automatico` sobre cómputo serverless
- **Trigger:** llegada de archivo nuevo en `/Volumes/especies_nativas/especies_nativas/bronze/presencias/`

Cuando se agrega un archivo nuevo a esa carpeta, el Job se dispara sin intervención manual, ejecuta el ciclo completo Bronze → Silver → Gold → Entrenamiento → Registro → Serving, y dentro de pocos minutos la aplicación web queda sirviendo el modelo actualizado.

## 6. Aplicación web

La aplicación (construida con Gradio) permite consultar el índice de idoneidad climática de forma interactiva, llamando al Serving Endpoint en tiempo real.

**Diseño de autenticación:** la app se autentica ante la API de Databricks mediante REST y tokens, sin depender del SDK oficial, lo que mantiene el despliegue liviano y portable entre entornos:

```python
def get_access_token():
    if "DATABRICKS_CLIENT_ID" in os.environ and "DATABRICKS_CLIENT_SECRET" in os.environ:
        # Modo desplegado: credenciales del service principal de la app
        resp = requests.post(f"{HOST}/oidc/v1/token",
            auth=(os.environ["DATABRICKS_CLIENT_ID"], os.environ["DATABRICKS_CLIENT_SECRET"]),
            data={"grant_type": "client_credentials", "scope": "all-apis"})
        return resp.json()["access_token"]
    else:
        # Modo local: token de la CLI ya autenticada
        result = subprocess.run(["databricks", "auth", "token",
            "--host", HOST, "--profile", PROFILE],
            capture_output=True, text=True)
        return json.loads(result.stdout)["access_token"]
```

**Llamada al modelo:**

```python
requests.post(
    f"{HOST}/serving-endpoints/albizia-guachapele-bio9/invocations",
    headers={"Authorization": f"Bearer {token}"},
    json={"dataframe_records": [{"bio_9": valor_ingresado}]},
    timeout=60,  # los endpoints pueden tener cold start tras inactividad
)
```

**Puerto de la aplicación:** Databricks Apps inyecta el puerto en tiempo de ejecución; la app lo lee dinámicamente en lugar de fijarlo:

```python
port = int(os.environ.get("DATABRICKS_APP_PORT", 8080))
demo.launch(server_name="0.0.0.0", server_port=port)
```

**`requirements.txt`:**

```
gradio==4.44.1
plotly==5.24.1
requests==2.32.3
pandas==2.2.3
```

**`app.yaml`:**

```yaml
command: ["python", "app.py"]
```

## 7. Despliegue de la aplicación

**Preparación del entorno (una sola vez):**

```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh
databricks auth login --host https://TU-WORKSPACE.cloud.databricks.com
databricks current-user me --profile TU_PERFIL
```

**Prueba local antes de desplegar:**

```bash
pip install --no-cache-dir -r requirements.txt
export DATABRICKS_CONFIG_PROFILE=TU_PERFIL
python3 app.py
```

**Ciclo de despliegue** (crear una vez, repetir subida + deploy en cada cambio de código):

```bash
# Crear la app (una sola vez)
databricks apps create albizia-guachapele-app --profile TU_PERFIL

# Subir el código al workspace
databricks workspace import-dir . /Workspace/Users/TU_USUARIO/albizia-guachapele-app \
  --profile TU_PERFIL --overwrite

# Desplegar
databricks apps deploy albizia-guachapele-app \
  --source-code-path /Workspace/Users/TU_USUARIO/albizia-guachapele-app \
  --profile TU_PERFIL
```

**Logs y monitoreo:**

```bash
databricks apps logs albizia-guachapele-app --tail-lines 100 --profile TU_PERFIL
```

## 8. Permisos requeridos

La aplicación corre bajo su propio **service principal**, que necesita permisos explícitos sobre cada recurso que consulta:

| Recurso | Permiso necesario |
|---|---|
| Catalog `especies_nativas` | `USE CATALOG` |
| Schema `models` | `USE SCHEMA` |
| Modelo `albizia_guachapele_bio9` | `EXECUTE` |
| Serving Endpoint `albizia-guachapele-bio9` | `Can Query` |

Tras cualquier cambio de permisos, es necesario volver a ejecutar `apps deploy` para que la aplicación recargue su estado.

## 9. Cómo agregar nuevos registros de presencia

1. Preparar un CSV con las columnas `species`, `Latitude`, `Longitude`.
2. Subirlo a `/Volumes/especies_nativas/especies_nativas/bronze/presencias/` con un **nombre único** (no sobrescribir archivos existentes).
3. El Job de reentrenamiento se dispara automáticamente al detectar el archivo nuevo.
4. En unos minutos, el modelo se reentrena, se registra una nueva versión y el Serving Endpoint queda actualizado.
5. La aplicación web sirve el modelo más reciente sin requerir ningún cambio de código ni redepliegue.

## 10. Solución de problemas

| Síntoma | Causa | Acción |
|---|---|---|
| El Job no se ejecuta al subir un archivo | El trigger `File arrival` detecta archivos nuevos, no modificaciones de uno existente | Subir el archivo con un nombre distinto |
| El pipeline se detiene con `ValueError` de AUC | Los datos agregados degradaron la capacidad predictiva del modelo | Revisar la calidad y consistencia de los nuevos registros de presencia |
| La app responde con datos desactualizados | El endpoint aún no terminó de propagar la nueva versión | Esperar unos minutos y verificar el estado en Serving |
| Error de permisos en la app | Falta un permiso explícito del service principal sobre algún recurso | Revisar la tabla de la sección 8 y el mensaje de error, que indica el recurso exacto |

## 11. Resultados de referencia

```
PIPELINE COMPLETADO
-------------------
Presencias procesadas : 156
Background generado   : 1208
AUC (validación cruzada): 0.7829
AUC (test)             : 0.7896
Versión del modelo     : 5
Endpoint actualizado   : albizia-guachapele-bio9
```

import os
import json
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuration
WORKSPACE_URL = os.environ.get("DATABRICKS_HOST", "https://dbc-28cdcc52-d3e9.cloud.databricks.com")
if not WORKSPACE_URL.startswith("http"):
    WORKSPACE_URL = "https://" + WORKSPACE_URL
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
ENDPOINT_NAME = "albizia-guachapele-bio9"

# Raster parameters (from notebook metadata)
RASTER_LEFT = -76.05
RASTER_TOP = 6.70
RASTER_RIGHT = -75.63333333333333
RASTER_BOTTOM = 6.35
PIXEL_SIZE = 0.008333333333333333
RASTER_WIDTH = 50
RASTER_HEIGHT = 42
NODATA_THRESHOLD = -1.0e37

# Global state
raster_array = None
presence_points = []
_db_client = None


def get_client():
    global _db_client
    if _db_client is None:
        from databricks.sdk import WorkspaceClient
        _db_client = WorkspaceClient()
    return _db_client


def _get_token():
    try:
        client = get_client()
        if hasattr(client.config, "token") and client.config.token:
            return client.config.token
        if hasattr(client.config, "authenticate"):
            auth = client.config.authenticate()
            if isinstance(auth, str) and auth.startswith("Bearer "):
                return auth[7:]
            return str(auth)
    except Exception:
        pass
    return os.environ.get("DATABRICKS_TOKEN", "")


def get_bio9(lat, lon):
    if raster_array is None:
        return None, "Raster no disponible. Verifique los permisos del volumen."
    if not (RASTER_LEFT <= lon <= RASTER_RIGHT and RASTER_BOTTOM <= lat <= RASTER_TOP):
        return None, "Ubicacion fuera del area de estudio"
    col = int((lon - RASTER_LEFT) / PIXEL_SIZE)
    row = int((RASTER_TOP - lat) / PIXEL_SIZE)
    col = max(0, min(col, RASTER_WIDTH - 1))
    row = max(0, min(row, RASTER_HEIGHT - 1))
    value = float(raster_array[row, col])
    if value <= NODATA_THRESHOLD:
        return None, "No hay datos climaticos para esta ubicacion"
    return value, None


def call_model(bio9_value):
    try:
        client = get_client()
        response = client.serving_endpoints.query(name=ENDPOINT_NAME, inputs=[[bio9_value]])
        predictions = response.predictions
        if predictions is None and response.outputs:
            predictions = response.outputs.get("predictions", [])
        if isinstance(predictions, list):
            return float(predictions[0])
        import numpy as np
        return float(np.array(predictions).flatten()[0])
    except Exception as e:
        print("SDK query failed: {}".format(e))
        import requests
        token = _get_token()
        url = WORKSPACE_URL.rstrip("/") + "/serving-endpoints/" + ENDPOINT_NAME + "/invocations"
        resp = requests.post(
            url,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json={"inputs": [[bio9_value]]},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data["predictions"][0])


def classify(prob):
    if prob >= 0.70:
        return "ALTA"
    elif prob >= 0.40:
        return "MEDIA"
    else:
        return "BAJA"


# Initialization - load embedded data from data.json
print("Inicializando Albizia Guachapele app...")

try:
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
    import numpy as np
    raster_flat = data["raster"]
    raster_array = np.array(raster_flat, dtype=np.float32).reshape(data["raster_shape"])
    raster_array = np.where(np.isnan(raster_array), NODATA_THRESHOLD, raster_array)
    presence_points = data["points"]
    print("Raster loaded: shape={}".format(raster_array.shape))
    print("Presence points loaded: {}".format(len(presence_points)))
except Exception as e:
    print("Error loading data: {}".format(e))


@app.route("/")
def home():
    return HTML_TEMPLATE


@app.route("/presence")
def presence():
    return jsonify(presence_points)


@app.route("/predict", methods=["POST"])
def predict():
    data = request.json
    lat = float(data["lat"])
    lon = float(data["lon"])
    bio9, error = get_bio9(lat, lon)
    if error:
        return jsonify({"error": error}), 400
    try:
        probability = call_model(bio9)
    except Exception as e:
        return jsonify({"error": "Error al consultar el modelo: " + str(e)}), 500
    return jsonify({
        "lat": lat,
        "lon": lon,
        "bio9": round(bio9, 1),
        "suitability": round(probability * 100, 1),
        "classification": classify(probability),
    })


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Albizia Guachapele</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#f0f2f5;color:#333}
.container{max-width:680px;margin:20px auto;padding:0 16px}
.card{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden;margin-bottom:16px}
.header{background:linear-gradient(135deg,#2e7d32,#1b5e20);color:white;padding:28px 24px;text-align:center}
.header h1{font-size:22px;margin-bottom:6px;letter-spacing:1.5px;font-weight:700}
.header p{font-size:14px;opacity:0.85}
#map{height:380px;width:100%}
.results{padding:20px 24px}
.results h3{font-size:12px;text-transform:uppercase;color:#999;margin-bottom:14px;letter-spacing:1px;font-weight:700}
.row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid #f0f0f0}
.row:last-child{border-bottom:none}
.label{font-weight:600;color:#555;font-size:15px}
.value{font-weight:700;font-size:15px}
.badge{padding:4px 14px;border-radius:6px;font-weight:700;font-size:13px;letter-spacing:0.5px}
.alta{background:#c8e6c9;color:#1b5e20}
.media{background:#ffe0b2;color:#e65100}
.baja{background:#ffcdd2;color:#b71c1c}
.loading{text-align:center;padding:24px;color:#888;font-size:15px}
.loading::after{content:'';display:inline-block;width:18px;height:18px;border:3px solid #ddd;border-top:3px solid #2e7d32;border-radius:50%;animation:spin 0.8s linear infinite;margin-left:8px;vertical-align:middle}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.error-box{text-align:center;padding:20px;color:#c62828;background:#ffebee;border-radius:8px;font-size:14px;font-weight:500}
.hint{text-align:center;padding:20px;color:#888;font-size:14px}
</style>
</head>
<body>
<div class="container">
<div class="card"><div class="header"><h1>ALBIZIA GUACHAPELE</h1><p>Evaluacion de idoneidad climatica</p></div></div>
<div class="card"><div id="map"></div></div>
<div class="card" id="hint-card"><div class="hint">📍 Haga clic en el mapa para evaluar la idoneidad climatica</div></div>
<div class="card" id="results-card" style="display:none"><div class="results"><h3>Resultados de la evaluacion</h3><div id="results-content"></div></div></div>
</div>
<script>
var map=L.map('map').setView([6.50,-75.84],12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap',maxZoom:19}).addTo(map);
var bounds=[[6.35,-76.05],[6.70,-75.6333]];
L.rectangle(bounds,{color:'#2e7d32',weight:2,fillOpacity:0.03,dashArray:'5,5'}).addTo(map);
map.fitBounds(bounds);
fetch('/presence').then(function(r){return r.json()}).then(function(points){
points.forEach(function(p){
L.circleMarker([p.lat,p.lon],{radius:3,color:'#1b5e20',fillColor:'#4caf50',fillOpacity:0.6,weight:1}).addTo(map);
});
}).catch(function(){});
var marker=null;
map.on('click',function(e){
var lat=e.latlng.lat,lon=e.latlng.lng;
if(marker)map.removeLayer(marker);
marker=L.marker([lat,lon]).addTo(map);
document.getElementById('hint-card').style.display='none';
document.getElementById('results-card').style.display='block';
document.getElementById('results-content').innerHTML='<div class="loading">Evaluando ubicacion</div>';
fetch('/predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat:lat,lon:lon})})
.then(function(r){return r.json()})
.then(function(data){
if(data.error){document.getElementById('results-content').innerHTML='<div class="error-box">'+data.error+'</div>';return;}
var cls=data.classification.toLowerCase();
document.getElementById('results-content').innerHTML=
'<div class="row"><span class="label">Latitud</span><span class="value">'+data.lat.toFixed(4)+'</span></div>'+
'<div class="row"><span class="label">Longitud</span><span class="value">'+data.lon.toFixed(4)+'</span></div>'+
'<div class="row"><span class="label">BIO9</span><span class="value">'+data.bio9+' \u00B0C</span></div>'+
'<div class="row"><span class="label">Idoneidad</span><span class="value">'+data.suitability+'%</span></div>'+
'<div class="row"><span class="label">Clasificacion</span><span class="badge '+cls+'">'+data.classification+'</span></div>';
})
.catch(function(){document.getElementById('results-content').innerHTML='<div class="error-box">Error de conexion</div>';});
});
</script>
</body>
</html>'''


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)))

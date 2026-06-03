# AgriAI

AgriAI is a Sabah crop suitability Web GIS prototype. This version focuses on the interactive Leaflet map, FastAPI backend, and PostgreSQL/PostGIS raster extraction. The Random Forest crop prediction model is intentionally left as a future integration point.

## Project Structure

```text
AgriAI/
|-- backend/
|   |-- main.py
|   `-- predict.py
|-- frontend/
|   |-- index.html
|   |-- style.css
|   `-- script.js
`-- README.md
```

## Current Features

- Leaflet.js map centered on Sabah with an OpenStreetMap basemap.
- Click any map location to request environmental raster values.
- Search by latitude and longitude, zoom to the coordinate, and run the same environmental lookup.
- Select one environmental raster layer at a time for map visualization.
- Adjust raster layer opacity in the browser.
- Display automatic legends and layer metadata for the selected raster.
- Calculate MVP crop suitability scores for Rice, Maize, and Banana.
- Highlight the recommended crop with a confidence level.
- Generate red/yellow/green crop suitability heatmap grids for the current map view.
- FastAPI endpoint: `GET /location?lon={longitude}&lat={latitude}`.
- FastAPI endpoint: `GET /predict?lon={longitude}&lat={latitude}`.
- FastAPI endpoint: `GET /suitability/heatmap?crop={crop}&west={west}&south={south}&east={east}&north={north}`.
- FastAPI raster metadata endpoint: `GET /raster/{layer_name}`.
- FastAPI raster tile endpoint: `GET /raster/{layer_name}/tiles/{z}/{x}/{y}.png`.
- PostGIS raster lookup using `ST_Intersects()` and `ST_Value()`.
- Clean JSON responses with `null` for missing or nodata raster values.
- Backend PostGIS extraction logic is separated from AI prediction logic in `backend/predict.py`.

## Database Assumptions

- Database name: `agriai`
- Schema: `raw_data`
- Raster column name: `rast`
- Input click coordinates: EPSG:4326 longitude and latitude

Required raster tables:

```text
raw_data.ph_0_5cm
raw_data.soc_0_5cm
raw_data.cec_0_5cm
raw_data.clay_0_5cm
raw_data.nitrogen_0_5cm
raw_data.sand_0_5cm
raw_data.bulkdensity_0_5cm
raw_data.rainfall
raw_data.temperature
raw_data.dem
raw_data.slope
raw_data.land_cover
```

If your raster column is not named `rast`, update the `RasterLayer` configuration in `backend/main.py`.

## Setup

Install Python dependencies:

```powershell
.\.venv\Scripts\activate
pip install fastapi uvicorn psycopg2-binary
```

Configure database access with environment variables:

```powershell
$env:AGRIAI_DB_NAME = "agriai"
$env:AGRIAI_DB_SCHEMA = "raw_data"
$env:AGRIAI_DB_USER = "postgres"
$env:AGRIAI_DB_PASSWORD = "your_password"
$env:AGRIAI_DB_HOST = "localhost"
$env:AGRIAI_DB_PORT = "5432"
```

You can also use one connection string:

```powershell
$env:DATABASE_URL = "postgresql://postgres:your_password@localhost:5432/agriai"
```


## Run

Start the FastAPI server from the project root:

```powershell
uvicorn backend.main:app --reload --port 8001
```

Open:

```text
http://127.0.0.1:8001
```

API documentation is available at:

```text
http://127.0.0.1:8001/docs
```

Example API request:

```text
http://127.0.0.1:8001/location?lon=117.15&lat=5.35
```

Example response:

```json
{
  "ph": 52,
  "soc": 30,
  "cec": 18,
  "clay": 25,
  "nitrogen": 10,
  "sand": 40,
  "bulkdensity": 1.2,
  "rainfall": 26.4,
  "temperature": 27.5,
  "dem": 350,
  "slope": 4.2,
  "land_cover": 20
}
```

Crop suitability request:

```text
http://127.0.0.1:8001/predict?lon=117.15&lat=5.35
```

Example response shape:

```json
{
  "coordinates": {
    "lon": 117.15,
    "lat": 5.35
  },
  "environmental_data": {
    "ph": 52,
    "soc": 30,
    "cec": 18,
    "clay": 25,
    "nitrogen": 10,
    "sand": 40,
    "bulkdensity": 1.2,
    "rainfall": 26.4,
    "temperature": 27.5,
    "dem": 350,
    "slope": 4.2,
    "land_cover": 40
  },
  "suitability_scores": {
    "rice": 85,
    "maize": 72,
    "banana": 91
  },
  "recommended_crop": "banana",
  "confidence": {
    "score": 76,
    "level": "High",
    "available_features": 12,
    "total_features": 12
  },
  "model_type": "rule_based_v1"
}
```

Suitability heatmap request:

```text
http://127.0.0.1:8001/suitability/heatmap?crop=rice&west=115.3&south=4.1&east=119.3&north=7.4&rows=18&cols=18
```

The heatmap endpoint returns a GeoJSON `FeatureCollection` of grid cells. Each cell contains `score`, `class`, and `color` properties for Leaflet rendering:

```text
green = high suitability
yellow = moderate suitability
red = low suitability
```

Raster layer metadata request:

```text
http://127.0.0.1:8001/raster/ph
```

Example response shape:

```json
{
  "key": "ph",
  "label": "pH",
  "source": "SoilGrids",
  "resolution": "250 m",
  "crs": "EPSG:4326",
  "extent": "Sabah, Malaysia",
  "tile_url": "/raster/ph/tiles/{z}/{x}/{y}.png",
  "legend": []
}
```

Supported raster layer names:

```text
ph
soc
cec
clay
nitrogen
sand
bulkdensity
rainfall
temperature
dem
slope
land_cover
```

Leaflet consumes the PNG tile endpoint through `L.tileLayer()`. This keeps raster visualization separate from the `/location` point-query endpoint and prepares the app for large rasters, tile caching, or a future dedicated tile server. The current PostGIS tile SQL uses `ST_TileEnvelope()`, `ST_Transform()`, `ST_Clip()`, `ST_Resize()`, `ST_ColorMap()`, and `ST_AsPNG()`.

## Prediction Architecture

The MVP uses transparent rule-based scoring in `backend/predict.py`.

Current flow:

```text
User clicks map
-> FastAPI extracts PostGIS raster values
-> prepare_features() normalizes raster storage units
-> predict_crop_suitability() scores Rice, Maize, and Banana
-> API returns environmental values, scores, recommended crop, and confidence
```

Random Forest integration should replace the internals of:

```text
backend/predict.py
```

Keep these function names stable so the FastAPI and Leaflet layers do not need to change:

```text
prepare_features()
generate_suitability_score()
predict_crop_suitability()
```

Future API endpoints that can be added:

- `GET /crop/{crop_name}`
- `GET /statistics`

Planned website areas:

- Homepage with project overview.
- Interactive map page with environmental variables and crop recommendations.
- Crop information page with growing requirements.
- Analytics dashboard with charts and suitability summaries.
- Optional admin tools for raster uploads, model updates, and dataset management.

## Deployment Direction

The current structure can be extended for Docker, Ubuntu Linux Server, and cloud deployment on AWS, Azure, or DigitalOcean. Before production deployment, restrict CORS origins in `backend/main.py`, move secrets into environment variables, and add database connection pooling.

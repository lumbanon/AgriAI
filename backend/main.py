import os
import math
import binascii
import struct
import threading
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from psycopg2 import OperationalError, sql

from .predict import (
    SUPPORTED_CROPS,
    classify_suitability,
    predict_crop_suitability,
    suitability_color,
)


# FastAPI application setup.
app = FastAPI(
    title="AgriAI Sabah Crop Suitability API",
    description="Extracts environmental raster values from PostGIS for clicked map locations.",
    version="0.1.0",
)

# Development-friendly CORS. Restrict this list before production deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@dataclass(frozen=True)
class RasterLayer:
    """Maps public API field names to PostGIS raster tables and map metadata."""

    key: str
    table: str
    label: str
    source: str
    resolution: str
    legend: tuple[dict[str, str], ...]
    color_map: str
    crs: str = "EPSG:4326"
    extent: str = "Sabah, Malaysia"
    raster_column: str = "rast"
    srid_override: int | None = None
    minzoom: int = 6
    maxzoom: int = 14
    resampling: str = "Bilinear"
    color_map_method: str = "INTERPOLATE"
    tile_pixels: int = 256


def legend_item(label: str, color: str, value: str = "") -> dict[str, str]:
    """Small helper keeps legend response shape consistent for the frontend."""

    return {"label": label, "color": color, "value": value}


DATABASE_SCHEMA = os.getenv("AGRIAI_DB_SCHEMA", "raw_data")
TILE_CACHE_VERSION = os.getenv("AGRIAI_TILE_CACHE_VERSION", "20260603_rainfall_preview")
TILE_RENDER_CONCURRENCY = int(os.getenv("AGRIAI_TILE_RENDER_CONCURRENCY", "2"))
TILE_RENDER_SEMAPHORE = threading.BoundedSemaphore(TILE_RENDER_CONCURRENCY)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TILE_CACHE_DIR = Path(os.getenv("AGRIAI_TILE_CACHE_DIR", PROJECT_ROOT / "tile_cache"))

# Keep all raster metadata in one place so more variables can be added later.
# Update the source, resolution, CRS, and color ramps if your input datasets differ.
RASTER_LAYERS: tuple[RasterLayer, ...] = (
    RasterLayer(
        key="ph",
        table="ph_0_5cm",
        label="pH",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("4.0 - acidic", "#c84d38", "4.0"),
            legend_item("5.5", "#e8c15d", "5.5"),
            legend_item("7.0 - neutral", "#4f9b6e", "7.0"),
            legend_item("8.5 - alkaline", "#2f6687", "8.5"),
        ),
        # Includes pH and SoilGrids pH x 10 ranges so either storage convention renders.
        color_map=(
            "nv 0 0 0 0\n"
            "4 200 77 56 220\n"
            "5.5 232 193 93 220\n"
            "7 79 155 110 220\n"
            "8.5 47 102 135 220\n"
            "40 200 77 56 220\n"
            "55 232 193 93 220\n"
            "70 79 155 110 220\n"
            "85 47 102 135 220"
        ),
    ),
    RasterLayer(
        key="soc",
        table="soc_0_5cm",
        label="SOC",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low SOC", "#d8c7a1", "0"),
            legend_item("Medium SOC", "#9b7044", "50"),
            legend_item("High SOC", "#4b2f1d", "100+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 216 199 161 220\n"
            "50 155 112 68 220\n"
            "100 75 47 29 220\n"
            "300 41 25 17 220"
        ),
    ),
    RasterLayer(
        key="cec",
        table="cec_0_5cm",
        label="CEC",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low CEC", "#f5e8a3", "0"),
            legend_item("Medium CEC", "#e1a34f", "20"),
            legend_item("High CEC", "#8f3f2d", "40+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 245 232 163 220\n"
            "200 225 163 79 220\n"
            "400 143 63 45 220\n"
            "800 92 38 28 220"
        ),
    ),
    RasterLayer(
        key="clay",
        table="clay_0_5cm",
        label="Clay",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low clay", "#f4d9a7", "0"),
            legend_item("Medium clay", "#cf8f4f", "30"),
            legend_item("High clay", "#884331", "60+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 244 217 167 220\n"
            "300 207 143 79 220\n"
            "600 136 67 49 220\n"
            "1000 83 38 30 220"
        ),
    ),
    RasterLayer(
        key="nitrogen",
        table="nitrogen_0_5cm",
        label="Nitrogen",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low nitrogen", "#edf2b1", "0"),
            legend_item("Medium nitrogen", "#8eba55", "500"),
            legend_item("High nitrogen", "#2f6f3e", "1000+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 237 242 177 220\n"
            "500 142 186 85 220\n"
            "1000 47 111 62 220\n"
            "3000 22 73 42 220"
        ),
    ),
    RasterLayer(
        key="sand",
        table="sand_0_5cm",
        label="Sand",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low sand", "#7b5a3a", "0"),
            legend_item("Medium sand", "#d2ae6d", "50"),
            legend_item("High sand", "#f0dfaa", "100"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 123 90 58 220\n"
            "300 210 174 109 220\n"
            "600 240 223 170 220\n"
            "1000 248 238 199 220"
        ),
    ),
    RasterLayer(
        key="bulkdensity",
        table="bulkdensity_0_5cm",
        label="Bulk Density",
        source="SoilGrids",
        resolution="250 m",
        legend=(
            legend_item("Low bulk density", "#efe4d0", "0.8"),
            legend_item("Medium bulk density", "#b98f68", "1.3"),
            legend_item("High bulk density", "#62412f", "1.8+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "80 239 228 208 220\n"
            "130 185 143 104 220\n"
            "180 98 65 47 220\n"
            "220 49 35 29 220"
        ),
    ),
    RasterLayer(
        key="rainfall",
        table="rainfall",
        label="Rainfall",
        source="Climate raster dataset",
        resolution="1 km",
        legend=(
            legend_item("Low rainfall", "#f0d37a", "< 1,800 mm/year"),
            legend_item("Medium rainfall", "#74add1", "1,800-3,000 mm/year"),
            legend_item("High rainfall", "#1b5aa6", "> 3,000 mm/year"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 240 211 122 220\n"
            "18 240 211 122 220\n"
            "30 116 173 209 220\n"
            "45 27 90 166 220\n"
            "800 240 211 122 220\n"
            "1800 240 211 122 220\n"
            "3000 116 173 209 220\n"
            "4500 27 90 166 220"
        ),
    ),
    RasterLayer(
        key="temperature",
        table="temperature",
        label="Temperature",
        source="Climate raster dataset",
        resolution="1 km",
        legend=(
            legend_item("Cool", "#31688e", "Cool"),
            legend_item("Warm", "#f2c14e", "Warm"),
            legend_item("Hot", "#c84d38", "Hot"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "1600 49 104 142 220\n"
            "2400 242 193 78 220\n"
            "3200 200 77 56 220\n"
            "4000 103 27 43 220"
        ),
    ),
    RasterLayer(
        key="dem",
        table="dem",
        label="DEM",
        source="Elevation raster dataset",
        resolution="30 m",
        legend=(
            legend_item("Low elevation", "#3f9f6b", "Low"),
            legend_item("Medium elevation", "#c9a85d", "Medium"),
            legend_item("High elevation", "#f2f0e6", "High"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 63 159 107 220\n"
            "750 201 168 93 220\n"
            "1500 133 97 69 220\n"
            "3000 242 240 230 220\n"
            "4500 255 255 255 220"
        ),
        minzoom=10,
    ),
    RasterLayer(
        key="slope",
        table="slope",
        label="Slope",
        source="Derived from DEM",
        resolution="30 m",
        crs="EPSG:32650",
        legend=(
            legend_item("0-5 degrees", "#5aa469", "0-5"),
            legend_item("5-15 degrees", "#d9bf5b", "5-15"),
            legend_item("15-30 degrees", "#e68a3a", "15-30"),
            legend_item("30 degrees+", "#b64033", "30+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 90 164 105 220\n"
            "5 217 191 91 220\n"
            "15 230 138 58 220\n"
            "30 182 64 51 220\n"
            "60 103 27 43 220"
        ),
        srid_override=32650,
        minzoom=11,
        maxzoom=13,
        tile_pixels=128,
    ),
    RasterLayer(
        key="land_cover",
        table="land_cover",
        label="Land Cover",
        source="Land cover raster dataset",
        resolution="10-30 m",
        legend=(
            legend_item("Water", "#2b83ba", "10"),
            legend_item("Forest / vegetation", "#1a9850", "20"),
            legend_item("Cropland / grassland", "#d9ef8b", "30"),
            legend_item("Built or bare", "#d73027", "40+"),
        ),
        color_map=(
            "nv 0 0 0 0\n"
            "0 120 120 120 210\n"
            "10 43 131 186 220\n"
            "20 26 152 80 220\n"
            "30 217 239 139 220\n"
            "40 215 48 39 220\n"
            "50 166 97 26 220\n"
            "80 49 54 149 220\n"
            "100 230 230 230 220"
        ),
        resampling="NearestNeighbor",
        color_map_method="NEAREST",
        minzoom=11,
        maxzoom=13,
        tile_pixels=128,
    ),
)
RASTER_LAYER_LOOKUP = {layer.key: layer for layer in RASTER_LAYERS}


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build one PNG chunk for the transparent fallback tile."""

    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", binascii.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def create_transparent_png(width: int = 256, height: int = 256) -> bytes:
    """Create a transparent PNG tile without adding Pillow/GDAL dependencies."""

    png_signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    empty_rows = b"".join(
        b"\x00" + (b"\x00\x00\x00\x00" * width)
        for _ in range(height)
    )

    return (
        png_signature
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(empty_rows))
        + png_chunk(b"IEND", b"")
    )


TRANSPARENT_TILE = create_transparent_png()


def get_database_connection() -> psycopg2.extensions.connection:
    """Create a PostgreSQL connection from environment variables."""

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        dbname=os.getenv("AGRIAI_DB_NAME", "agriai"),
        user=os.getenv("AGRIAI_DB_USER", "postgres"),
        password=os.getenv("AGRIAI_DB_PASSWORD", "123456"),
        host=os.getenv("AGRIAI_DB_HOST", "localhost"),
        port=os.getenv("AGRIAI_DB_PORT", "5432"),
    )


def get_raster_layer_or_404(layer_name: str) -> RasterLayer:
    """Validate a public raster layer name from API routes."""

    layer = RASTER_LAYER_LOOKUP.get(layer_name)
    if layer is None:
        supported_layers = ", ".join(sorted(RASTER_LAYER_LOOKUP))
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported raster layer '{layer_name}'. Supported layers: {supported_layers}.",
        )

    return layer


def build_raster_expression(
    layer: RasterLayer,
    table_alias: str | None = None,
) -> sql.Composed | sql.Identifier:
    """Return a safe SQL expression for a layer's raster, including SRID overrides."""

    raster_expression = (
        sql.Identifier(table_alias, layer.raster_column)
        if table_alias is not None
        else sql.Identifier(layer.raster_column)
    )
    if layer.srid_override is not None:
        raster_expression = sql.SQL("ST_SetSRID({raster_column}, {srid})").format(
            raster_column=raster_expression,
            srid=sql.Literal(layer.srid_override),
        )

    return raster_expression


def normalize_color_map_for_postgis(color_map: str) -> str:
    """Sort numeric color-map stops descending, as PostGIS expects for interpolation."""

    color_stops: list[tuple[float, str]] = []

    for raw_line in color_map.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        first_token = line.split(maxsplit=1)[0]
        try:
            numeric_value = float(first_token.rstrip("%"))
        except ValueError:
            # Drop nonnumeric directives such as "nv"; out-of-range pixels remain transparent.
            continue

        color_stops.append((numeric_value, line))

    color_stops.sort(key=lambda item: item[0], reverse=True)
    return "\n".join(line for _, line in color_stops)


def normalize_raster_value(value: Any) -> float | int | None:
    """Convert database values into JSON-safe numbers while preserving NULLs."""

    if value is None:
        return None

    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        return None

    if numeric_value.is_integer():
        return int(numeric_value)

    return numeric_value


def fetch_layer_value(
    cursor: psycopg2.extensions.cursor,
    layer: RasterLayer,
    lon: float,
    lat: float,
) -> float | int | None:
    """Read one raster value at a WGS84 longitude/latitude point."""

    raster_expression = build_raster_expression(layer)

    query = sql.SQL(
        """
        WITH clicked_point AS (
            SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS geom
        ),
        candidate_tiles AS (
            SELECT
                {raster_expression} AS rast,
                CASE
                    WHEN ST_SRID({raster_expression}) > 0
                        THEN ST_Transform(clicked_point.geom, ST_SRID({raster_expression}))
                    ELSE ST_SetSRID(clicked_point.geom, 0)
                END AS geom
            FROM {schema_name}.{table_name}, clicked_point
        ),
        sampled_values AS (
            SELECT ST_Value(rast, 1, geom, true) AS value
            FROM candidate_tiles
            WHERE ST_Intersects(rast, geom)
        )
        SELECT value
        FROM sampled_values
        ORDER BY value IS NULL
        LIMIT 1;
        """
    ).format(
        schema_name=sql.Identifier(DATABASE_SCHEMA),
        table_name=sql.Identifier(layer.table),
        raster_expression=raster_expression,
    )

    cursor.execute(query, (lon, lat))
    row = cursor.fetchone()

    if row is None:
        return None

    return normalize_raster_value(row[0])


def extract_environmental_data_from_cursor(
    cursor: psycopg2.extensions.cursor,
    lon: float,
    lat: float,
) -> dict[str, float | int | None]:
    """Collect all environmental variables using an existing database cursor."""

    return {
        layer.key: fetch_layer_value(cursor, layer, lon, lat)
        for layer in RASTER_LAYERS
    }


def extract_environmental_data(lon: float, lat: float) -> dict[str, float | int | None]:
    """Collect all environmental variables for one map click."""

    try:
        with get_database_connection() as connection:
            with connection.cursor() as cursor:
                return extract_environmental_data_from_cursor(cursor, lon, lat)
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check PostgreSQL/PostGIS settings.",
        ) from exc
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PostGIS raster query failed: {exc.pgerror or str(exc)}",
        ) from exc


def validate_crop_or_404(crop: str) -> str:
    """Validate a supported crop name for API routes."""

    crop_key = crop.lower()
    if crop_key not in SUPPORTED_CROPS:
        supported_crops = ", ".join(SUPPORTED_CROPS)
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported crop '{crop}'. Supported crops: {supported_crops}.",
        )

    return crop_key


def build_prediction_response(
    lon: float,
    lat: float,
    environmental_data: dict[str, float | int | None],
) -> dict[str, Any]:
    """Combine raw PostGIS values with AI-ready suitability prediction output."""

    prediction = predict_crop_suitability(environmental_data)
    return {
        "coordinates": {"lon": lon, "lat": lat},
        "environmental_data": environmental_data,
        "features": prediction["features"],
        "suitability_scores": prediction["scores"],
        "recommended_crop": prediction["recommended_crop"],
        "confidence": prediction["confidence"],
        "model_type": prediction["model_type"],
    }


def build_heatmap_feature(
    west: float,
    south: float,
    east: float,
    north: float,
    lon: float,
    lat: float,
    crop: str,
    score: int,
) -> dict[str, Any]:
    """Create one GeoJSON polygon cell for the suitability heatmap."""

    suitability_class = classify_suitability(score)
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]],
        },
        "properties": {
            "crop": crop,
            "score": score,
            "class": suitability_class,
            "color": suitability_color(score),
            "lon": lon,
            "lat": lat,
        },
    }


def build_suitability_heatmap(
    crop: str,
    west: float,
    south: float,
    east: float,
    north: float,
    rows: int,
    cols: int,
) -> dict[str, Any]:
    """Generate a viewport-scale crop suitability GeoJSON grid."""

    if west >= east:
        raise HTTPException(status_code=400, detail="West bound must be less than east bound.")
    if south >= north:
        raise HTTPException(status_code=400, detail="South bound must be less than north bound.")

    crop_key = validate_crop_or_404(crop)
    cell_width = (east - west) / cols
    cell_height = (north - south) / rows
    features: list[dict[str, Any]] = []

    try:
        with get_database_connection() as connection:
            with connection.cursor() as cursor:
                for row in range(rows):
                    cell_south = south + (row * cell_height)
                    cell_north = cell_south + cell_height
                    lat = cell_south + (cell_height / 2)

                    for col in range(cols):
                        cell_west = west + (col * cell_width)
                        cell_east = cell_west + cell_width
                        lon = cell_west + (cell_width / 2)
                        environmental_data = extract_environmental_data_from_cursor(cursor, lon, lat)

                        if not any(value is not None for value in environmental_data.values()):
                            continue

                        prediction = predict_crop_suitability(environmental_data)
                        score = prediction["scores"][crop_key]
                        features.append(
                            build_heatmap_feature(
                                west=cell_west,
                                south=cell_south,
                                east=cell_east,
                                north=cell_north,
                                lon=lon,
                                lat=lat,
                                crop=crop_key,
                                score=score,
                            )
                        )
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check PostgreSQL/PostGIS settings.",
        ) from exc
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PostGIS raster query failed while generating heatmap: {exc.pgerror or str(exc)}",
        ) from exc

    return {
        "type": "FeatureCollection",
        "crop": crop_key,
        "rows": rows,
        "cols": cols,
        "features": features,
        "legend": [
            {"class": "high", "label": "High suitability", "color": suitability_color(90)},
            {"class": "moderate", "label": "Moderate suitability", "color": suitability_color(60)},
            {"class": "low", "label": "Low suitability", "color": suitability_color(30)},
        ],
    }


def raster_layer_metadata(layer: RasterLayer) -> dict[str, Any]:
    """Return frontend-ready metadata for a Leaflet raster tile layer."""

    return {
        "key": layer.key,
        "label": layer.label,
        "source": layer.source,
        "resolution": layer.resolution,
        "crs": layer.crs,
        "extent": layer.extent,
        "bounds": [115.3, 4.1, 119.3, 7.4],
        "minzoom": layer.minzoom,
        "maxzoom": layer.maxzoom,
        "tile_pixels": layer.tile_pixels,
        "tile_url": f"/raster/{layer.key}/tiles/{{z}}/{{x}}/{{y}}.png?v={TILE_CACHE_VERSION}",
        "disk_cache": True,
        "heavy_layer": layer.tile_pixels < 256,
        "legend": list(layer.legend),
        "attribution": f"Raster data: {layer.source}",
    }


def validate_tile_coordinates(z: int, x: int, y: int) -> None:
    """Reject impossible tile coordinates before PostGIS work starts."""

    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="Tile zoom must be between 0 and 22.")

    tile_count = 2**z
    if x < 0 or y < 0 or x >= tile_count or y >= tile_count:
        raise HTTPException(status_code=400, detail="Tile x/y is outside the zoom grid.")


def tile_cache_path(layer: RasterLayer, z: int, x: int, y: int) -> Path:
    """Build the disk cache path for one rendered PNG tile."""

    validate_tile_coordinates(z=z, x=x, y=y)
    return TILE_CACHE_DIR / layer.key / str(z) / str(x) / f"{y}.png"


def write_tile_cache(cache_path: Path, tile: bytes) -> None:
    """Persist a generated tile so repeated layer switches avoid PostGIS work."""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=cache_path.parent,
        prefix=f".{cache_path.stem}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_file.write(tile)
        temp_path = Path(temp_file.name)

    temp_path.replace(cache_path)


def read_or_generate_cached_tile(layer: RasterLayer, z: int, x: int, y: int) -> bytes:
    """Read a tile from disk, or render and cache it on the first request."""

    cache_path = tile_cache_path(layer=layer, z=z, x=x, y=y)
    if cache_path.exists():
        return cache_path.read_bytes()

    tile = render_raster_tile(layer=layer, z=z, x=x, y=y)
    write_tile_cache(cache_path, tile)
    return tile


def render_raster_tile(layer: RasterLayer, z: int, x: int, y: int) -> bytes:
    """Return a raster PNG tile, using a small process-local development cache."""

    return render_raster_tile_cached(layer.key, z, x, y)


@lru_cache(maxsize=int(os.getenv("AGRIAI_TILE_CACHE_SIZE", "512")))
def render_raster_tile_cached(layer_key: str, z: int, x: int, y: int) -> bytes:
    """Render one Web Mercator PNG tile from a configured PostGIS raster layer."""

    layer = RASTER_LAYER_LOOKUP[layer_key]
    validate_tile_coordinates(z=z, x=x, y=y)
    raster_expression = build_raster_expression(layer, table_alias="source_rasters")

    # Tile requests are intentionally separate from /location point extraction.
    # This keeps future caching, pregenerated tiles, or a dedicated tile server easy to add.
    # Each source raster is resampled onto the exact Leaflet tile grid before
    # ST_Union(), avoiding alignment errors for tiled source datasets.
    query = sql.SQL(
        """
        WITH tile AS (
            SELECT ST_TileEnvelope(%s, %s, %s) AS geom_3857
        ),
        template AS (
            SELECT
                ST_AddBand(
                    ST_MakeEmptyRaster(
                        %s,
                        %s,
                        ST_XMin(geom_3857),
                        ST_YMax(geom_3857),
                        (ST_XMax(geom_3857) - ST_XMin(geom_3857)) / %s,
                        -((ST_YMax(geom_3857) - ST_YMin(geom_3857)) / %s),
                        0,
                        0,
                        3857
                    ),
                    '32BF'::text,
                    -32768::double precision,
                    -32768::double precision
                ) AS rast,
                geom_3857
            FROM tile
        ),
        source_tiles AS (
            SELECT
                {raster_expression} AS source_rast,
                CASE
                    WHEN ST_SRID({raster_expression}) = 3857
                        THEN template.geom_3857
                    WHEN ST_SRID({raster_expression}) > 0
                        THEN ST_Transform(template.geom_3857, ST_SRID({raster_expression}))
                    ELSE ST_SetSRID(ST_Transform(template.geom_3857, 4326), 0)
                END AS geom_source,
                template.rast AS template_rast,
                template.geom_3857
            FROM {schema_name}.{table_name} AS source_rasters, template
            WHERE ST_Intersects(
                {raster_expression},
                CASE
                    WHEN ST_SRID({raster_expression}) = 3857
                        THEN template.geom_3857
                    WHEN ST_SRID({raster_expression}) > 0
                        THEN ST_Transform(template.geom_3857, ST_SRID({raster_expression}))
                    ELSE ST_SetSRID(ST_Transform(template.geom_3857, 4326), 0)
                END
            )
        ),
        candidate_tiles AS (
            SELECT
                CASE
                    WHEN ST_SRID(source_rast) = 3857
                        THEN ST_Clip(source_rast, geom_source, true)
                    WHEN ST_SRID(source_rast) > 0
                        THEN ST_Transform(ST_Clip(source_rast, geom_source, true), 3857)
                    ELSE ST_Transform(ST_SetSRID(ST_Clip(source_rast, geom_source, true), 4326), 3857)
                END AS rast_3857,
                template_rast,
                geom_3857
            FROM source_tiles
        ),
        aligned_tiles AS (
            SELECT ST_Resample(
                rast_3857,
                template_rast,
                %s
            ) AS rast
            FROM candidate_tiles
        ),
        mosaic AS (
            SELECT ST_Union(rast) AS rast
            FROM aligned_tiles
        ),
        resized AS (
            SELECT ST_Resize(rast, %s, %s, %s) AS rast
            FROM mosaic
            WHERE rast IS NOT NULL
        ),
        colorized AS (
            SELECT ST_ColorMap(rast, 1, %s, %s) AS rast
            FROM resized
        )
        SELECT ST_AsPNG(rast)
        FROM colorized;
        """
    ).format(
        schema_name=sql.Identifier(DATABASE_SCHEMA),
        table_name=sql.Identifier(layer.table),
        raster_expression=raster_expression,
    )

    try:
        # Keep PostGIS responsive during layer switches. Browsers can request many
        # raster tiles at once; limiting concurrent renders avoids overloading the DB.
        with TILE_RENDER_SEMAPHORE:
            with get_database_connection() as connection:
                with connection.cursor() as cursor:
                    # ST_AsPNG uses GDAL output drivers. Some PostGIS installs disable
                    # them by default, so enable them for this tile-rendering session.
                    cursor.execute("SET postgis.gdal_enabled_drivers = 'ENABLE_ALL'")
                    cursor.execute(
                        query,
                        (
                            z,
                            x,
                            y,
                            layer.tile_pixels,
                            layer.tile_pixels,
                            layer.tile_pixels,
                            layer.tile_pixels,
                            layer.resampling,
                            layer.tile_pixels,
                            layer.tile_pixels,
                            layer.resampling,
                            normalize_color_map_for_postgis(layer.color_map),
                            layer.color_map_method,
                        ),
                    )
                    row = cursor.fetchone()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check PostgreSQL/PostGIS settings.",
        ) from exc
    except psycopg2.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PostGIS raster tile query failed: {exc.pgerror or str(exc)}",
        ) from exc

    if row is None or row[0] is None:
        return TRANSPARENT_TILE

    return bytes(row[0])


@app.get("/health")
def health_check() -> dict[str, str]:
    """Lightweight service health endpoint."""

    return {"status": "ok"}


@app.get("/location")
def get_location_data(
    lon: float = Query(..., ge=-180, le=180, description="Longitude in EPSG:4326"),
    lat: float = Query(..., ge=-90, le=90, description="Latitude in EPSG:4326"),
) -> dict[str, float | int | None]:
    """Return PostGIS raster values for the selected map coordinate."""

    return extract_environmental_data(lon=lon, lat=lat)


@app.get("/predict")
def get_crop_suitability_prediction(
    lon: float = Query(..., ge=-180, le=180, description="Longitude in EPSG:4326"),
    lat: float = Query(..., ge=-90, le=90, description="Latitude in EPSG:4326"),
) -> dict[str, Any]:
    """Return environmental values and crop suitability scores for one location."""

    environmental_data = extract_environmental_data(lon=lon, lat=lat)
    return build_prediction_response(lon=lon, lat=lat, environmental_data=environmental_data)


@app.get("/suitability/heatmap")
def get_suitability_heatmap(
    crop: str = Query("rice", description="Crop name: rice, maize, or banana"),
    west: float = Query(..., ge=-180, le=180, description="Western longitude bound"),
    south: float = Query(..., ge=-90, le=90, description="Southern latitude bound"),
    east: float = Query(..., ge=-180, le=180, description="Eastern longitude bound"),
    north: float = Query(..., ge=-90, le=90, description="Northern latitude bound"),
    rows: int = Query(18, ge=4, le=30, description="Number of heatmap grid rows"),
    cols: int = Query(18, ge=4, le=30, description="Number of heatmap grid columns"),
) -> dict[str, Any]:
    """Return a red/yellow/green crop suitability grid for the current map view."""

    return build_suitability_heatmap(
        crop=crop,
        west=west,
        south=south,
        east=east,
        north=north,
        rows=rows,
        cols=cols,
    )


@app.get("/raster/{layer_name}")
def get_raster_layer(layer_name: str) -> dict[str, Any]:
    """Return Leaflet-ready metadata and tile URL for one environmental raster."""

    layer = get_raster_layer_or_404(layer_name)
    return raster_layer_metadata(layer)


@app.get("/raster/{layer_name}/tiles/{z}/{x}/{y}.png")
def get_raster_tile(layer_name: str, z: int, x: int, y: int) -> Response:
    """Return one colorized raster PNG tile for Leaflet display."""

    layer = get_raster_layer_or_404(layer_name)
    tile = read_or_generate_cached_tile(layer=layer, z=z, x=x, y=y)
    return Response(
        content=tile,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


# Optional static file routes let FastAPI serve the frontend during development.
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/style.css", include_in_schema=False)
def serve_styles() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "style.css")


@app.get("/script.js", include_in_schema=False)
def serve_script() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "script.js")

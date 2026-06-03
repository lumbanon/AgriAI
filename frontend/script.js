// Backend URL selection supports both FastAPI-served pages and local file access.
const API_BASE_URL = getApiBaseUrl();
const SABAH_CENTER = [5.35, 117.15];
const SEARCH_ZOOM = 13;
const NOT_AVAILABLE = "N/A";

const CROP_OPTIONS = [
    { key: "rice", label: "Rice" },
    { key: "maize", label: "Maize" },
    { key: "banana", label: "Banana" },
];

const HEATMAP_GRID_SIZE = 18;

// Land-cover classes returned by the land_cover raster.
const LAND_COVER_CLASSES = {
    10: "Forest",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up Area",
    60: "Bare Land",
    70: "Water",
};

// Environmental metadata drives both formatting and UI rendering.
// Keep future layers here so sidebar, popup, and selector stay consistent.
const ENVIRONMENT_GROUPS = [
    {
        key: "soil",
        title: "Soil",
        fields: [
            { key: "ph", label: "pH", format: formatPH },
            { key: "soc", label: "SOC", format: formatSOC },
            { key: "cec", label: "CEC", format: formatCEC },
            { key: "clay", label: "Clay", format: formatClay },
            { key: "nitrogen", label: "Nitrogen", format: formatNitrogen },
            { key: "sand", label: "Sand", format: formatSand },
            { key: "bulkdensity", label: "Bulk Density", format: formatDensity },
        ],
    },
    {
        key: "climate",
        title: "Climate",
        fields: [
            { key: "rainfall", label: "Rainfall", format: formatRainfall },
            { key: "temperature", label: "Temperature", format: formatTemperature },
        ],
    },
    {
        key: "terrain",
        title: "Terrain",
        fields: [
            { key: "dem", label: "Elevation", format: formatElevation },
            { key: "slope", label: "Slope", format: formatSlope },
        ],
    },
    {
        key: "land_cover",
        title: "Land Cover",
        fields: [
            { key: "land_cover", label: "Land Cover", format: formatLandCover },
        ],
    },
];

const ENVIRONMENT_FIELDS = ENVIRONMENT_GROUPS.flatMap((group) => group.fields);

const coordinateOutput = document.getElementById("coordinate-output");
const dataOutput = document.getElementById("data-output");
const recommendationOutput = document.getElementById("recommendation-output");
const coordinateSearchForm = document.getElementById("coordinate-search-form");
const latitudeInput = document.getElementById("latitude-input");
const longitudeInput = document.getElementById("longitude-input");
const coordinateSearchError = document.getElementById("coordinate-search-error");
const rasterLayerSelect = document.getElementById("raster-layer-select");
const opacitySlider = document.getElementById("opacity-slider");
const opacityOutput = document.getElementById("opacity-output");
const layerStatus = document.getElementById("layer-status");
const layerLegend = document.getElementById("layer-legend");
const layerInfo = document.getElementById("layer-info");
const cropHeatmapSelect = document.getElementById("crop-heatmap-select");
const generateHeatmapButton = document.getElementById("generate-heatmap-button");
const clearHeatmapButton = document.getElementById("clear-heatmap-button");
const heatmapStatus = document.getElementById("heatmap-status");

// Leaflet map centered on Sabah.
const map = L.map("map", {
    zoomControl: true,
    preferCanvas: true,
}).setView(SABAH_CENTER, 7);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

map.createPane("suitabilityHeatmapPane");
map.getPane("suitabilityHeatmapPane").style.zIndex = 430;

let selectedMarker = null;
let activeRasterLayer = null;
let activeRasterMetadata = null;
let activeSuitabilityLayer = null;
let locationRequestId = 0;
let heatmapRequestId = 0;
let rasterLayerRequestId = 0;
let pendingRasterTiles = 0;

initializeRasterLayerSelector();
initializeCropHeatmapSelector();
updateOpacityLabel();
renderLegend(null);
renderLayerInfo(null);

// Click and coordinate search share the same query/render path.
map.on("click", (event) => {
    const { lat, lng } = event.latlng;
    handleLocationSelection(lat, lng, { zoomToLocation: false });
});

coordinateSearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const coordinates = parseCoordinateSearch();

    if (!coordinates) {
        return;
    }

    handleLocationSelection(coordinates.lat, coordinates.lon, { zoomToLocation: true });
});

rasterLayerSelect.addEventListener("change", () => {
    handleRasterLayerSelection(rasterLayerSelect.value);
});

map.on("zoomend", () => {
    updateActiveLayerZoomStatus();
});

map.on("moveend", () => {
    if (activeSuitabilityLayer) {
        setHeatmapStatus("Map view changed. Generate again to refresh the heatmap extent.");
    }
});

opacitySlider.addEventListener("input", () => {
    updateOpacityLabel();

    if (activeRasterLayer) {
        activeRasterLayer.setOpacity(getSelectedOpacity());
    }
});

generateHeatmapButton.addEventListener("click", () => {
    generateSuitabilityHeatmap();
});

clearHeatmapButton.addEventListener("click", () => {
    clearSuitabilityHeatmap();
});

cropHeatmapSelect.addEventListener("change", () => {
    if (activeSuitabilityLayer) {
        generateSuitabilityHeatmap();
    }
});

function getApiBaseUrl() {
    if (window.location.protocol === "file:") {
        return "http://127.0.0.1:8001";
    }

    if (window.location.port === "8001" || window.location.port === "") {
        return window.location.origin;
    }

    return "http://127.0.0.1:8001";
}

function initializeRasterLayerSelector() {
    ENVIRONMENT_FIELDS.forEach((field) => {
        const option = document.createElement("option");
        option.value = field.key;
        option.textContent = field.label;
        rasterLayerSelect.appendChild(option);
    });
}

function initializeCropHeatmapSelector() {
    CROP_OPTIONS.forEach((crop) => {
        const option = document.createElement("option");
        option.value = crop.key;
        option.textContent = crop.label;
        cropHeatmapSelect.appendChild(option);
    });
}

function parseCoordinateSearch() {
    const lat = Number(latitudeInput.value);
    const lon = Number(longitudeInput.value);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        showCoordinateSearchError("Enter numeric latitude and longitude values.");
        return null;
    }

    if (lat < -90 || lat > 90) {
        showCoordinateSearchError("Latitude must be between -90 and 90.");
        return null;
    }

    if (lon < -180 || lon > 180) {
        showCoordinateSearchError("Longitude must be between -180 and 180.");
        return null;
    }

    clearCoordinateSearchError();
    return { lat, lon };
}

async function handleLocationSelection(lat, lon, options = {}) {
    const requestId = ++locationRequestId;

    if (options.zoomToLocation) {
        map.setView([lat, lon], Math.max(map.getZoom(), SEARCH_ZOOM));
    }

    setSelectedMarker(lat, lon);
    updateCoordinates(lat, lon);
    setLoadingState();

    selectedMarker
        .bindPopup(createLoadingPopup(lat, lon), { maxWidth: 430 })
        .openPopup();

    try {
        const prediction = await fetchCropSuitability(lon, lat);

        if (requestId !== locationRequestId) {
            return;
        }

        const data = prediction.environmental_data || {};
        renderEnvironmentalData(data);
        renderSuitabilityDashboard(prediction);

        selectedMarker
            .setPopupContent(createDataPopup(lat, lon, prediction))
            .openPopup();
    } catch (error) {
        if (requestId !== locationRequestId) {
            return;
        }

        renderError(error.message);
        selectedMarker
            .setPopupContent(createErrorPopup(lat, lon, error.message))
            .openPopup();
    }
}

function setSelectedMarker(lat, lon) {
    if (selectedMarker) {
        selectedMarker.setLatLng([lat, lon]);
        return;
    }

    selectedMarker = L.marker([lat, lon]).addTo(map);
}

function updateCoordinates(lat, lon) {
    coordinateOutput.textContent = `Latitude: ${lat.toFixed(6)}, Longitude: ${lon.toFixed(6)}`;
}

function showCoordinateSearchError(message) {
    coordinateSearchError.textContent = message;
}

function clearCoordinateSearchError() {
    coordinateSearchError.textContent = "";
}

function setLoadingState() {
    dataOutput.className = "data-grid empty-state";
    dataOutput.textContent = "Loading raster values from PostGIS...";
    recommendationOutput.textContent = "Calculating crop suitability scores...";
}

async function fetchCropSuitability(lon, lat) {
    const url = new URL("/predict", API_BASE_URL);
    url.searchParams.set("lon", lon.toFixed(8));
    url.searchParams.set("lat", lat.toFixed(8));

    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(payload.detail || "Unable to load environmental data.");
    }

    return payload;
}

async function handleRasterLayerSelection(layerName) {
    const requestId = ++rasterLayerRequestId;

    if (!layerName) {
        clearRasterLayer();
        return;
    }

    // Stop old raster tile requests before starting the next layer. This keeps
    // slope and land-cover switches from generating two PostGIS tile sets at once.
    removeActiveRasterLayer();
    activeRasterMetadata = null;
    pendingRasterTiles = 0;
    setLayerStatus("Loading layer...");

    try {
        const metadata = await fetchRasterMetadata(layerName);
        if (requestId !== rasterLayerRequestId) {
            return;
        }

        showRasterLayer(metadata, requestId);
    } catch (error) {
        if (requestId !== rasterLayerRequestId) {
            return;
        }

        clearRasterLayer();
        setLayerStatus(error.message);
    }
}

async function fetchRasterMetadata(layerName) {
    const url = new URL(`/raster/${encodeURIComponent(layerName)}`, API_BASE_URL);
    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(payload.detail || "Unable to load raster layer metadata.");
    }

    return payload;
}

function showRasterLayer(metadata, requestId) {
    if (map.getZoom() < metadata.minzoom) {
        map.setZoom(metadata.minzoom);
    }

    const tileUrl = `${API_BASE_URL}${metadata.tile_url}`;
    const rasterBounds = createRasterBounds(metadata.bounds);
    activeRasterLayer = L.tileLayer(tileUrl, {
        bounds: rasterBounds,
        tileSize: 256,
        minZoom: metadata.minzoom,
        maxZoom: metadata.maxzoom,
        // Keep a small tile buffer for smoother panning while avoiding eager
        // zoom redraws, which are expensive for PostGIS-backed raster tiles.
        keepBuffer: 2,
        noWrap: true,
        opacity: getSelectedOpacity(),
        updateInterval: metadata.tile_pixels && metadata.tile_pixels < 256 ? 800 : 500,
        updateWhenIdle: true,
        updateWhenZooming: false,
        reuseTiles: true,
        zIndex: 20,
        attribution: metadata.attribution || "",
    });

    activeRasterLayer.once("load", () => {
        if (requestId === rasterLayerRequestId) {
            pendingRasterTiles = 0;
            updateActiveLayerZoomStatus();
        }
    });

    activeRasterLayer.on("loading", () => {
        if (requestId !== rasterLayerRequestId) {
            return;
        }

        pendingRasterTiles = 1;
        setLayerStatus(`Loading layer... ${metadata.label}`);
    });

    activeRasterLayer.on("tileloadstart", () => {
        if (requestId !== rasterLayerRequestId) {
            return;
        }

        pendingRasterTiles += 1;
        setLayerStatus(`Loading layer... ${metadata.label}`);
    });

    activeRasterLayer.on("tileload", () => {
        handleRasterTileFinished(metadata, requestId);
    });

    activeRasterLayer.on("tileerror", () => {
        if (requestId !== rasterLayerRequestId) {
            return;
        }

        handleRasterTileFinished(metadata, requestId);
        setLayerStatus(`Unable to draw ${metadata.label} tiles. Check the raster tile endpoint.`);
    });

    activeRasterLayer.addTo(map);

    activeRasterMetadata = metadata;
    renderLegend(metadata);
    renderLayerInfo(metadata);
    updateActiveLayerZoomStatus();
}

function createRasterBounds(bounds) {
    if (!Array.isArray(bounds) || bounds.length !== 4) {
        return undefined;
    }

    const [west, south, east, north] = bounds.map(Number);
    if (![west, south, east, north].every(Number.isFinite)) {
        return undefined;
    }

    return L.latLngBounds([south, west], [north, east]);
}

function clearRasterLayer() {
    rasterLayerRequestId += 1;
    removeActiveRasterLayer();
    activeRasterMetadata = null;
    pendingRasterTiles = 0;
    rasterLayerSelect.value = "";
    renderLegend(null);
    renderLayerInfo(null);
    setLayerStatus("No raster layer selected.");
}

function removeActiveRasterLayer() {
    removeRasterLayer(activeRasterLayer);
    activeRasterLayer = null;
}

function removeRasterLayer(layer) {
    if (layer && map.hasLayer(layer)) {
        map.removeLayer(layer);
    }
}

function getSelectedOpacity() {
    return Number(opacitySlider.value) / 100;
}

function updateOpacityLabel() {
    opacityOutput.textContent = `${opacitySlider.value}%`;
}

function setLayerStatus(message) {
    layerStatus.textContent = message;
}

function handleRasterTileFinished(metadata, requestId) {
    if (requestId !== rasterLayerRequestId) {
        return;
    }

    pendingRasterTiles = Math.max(0, pendingRasterTiles - 1);
    if (pendingRasterTiles === 0) {
        updateActiveLayerZoomStatus();
    }
}

function updateActiveLayerZoomStatus() {
    if (!activeRasterMetadata) {
        return;
    }

    if (map.getZoom() < activeRasterMetadata.minzoom) {
        setLayerStatus(`${activeRasterMetadata.label} is visible from zoom ${activeRasterMetadata.minzoom}+. Zoom in to draw this layer.`);
        return;
    }

    const cacheStatus = activeRasterMetadata.disk_cache ? "Cached after first load" : "Live PostGIS render";
    setLayerStatus(`Showing ${activeRasterMetadata.label}. ${cacheStatus}.`);
}

async function generateSuitabilityHeatmap() {
    const crop = cropHeatmapSelect.value || CROP_OPTIONS[0].key;
    const cropLabel = getCropLabel(crop);
    const requestId = ++heatmapRequestId;
    const bounds = map.getBounds();

    setHeatmapStatus(`Generating ${cropLabel} suitability heatmap...`);
    generateHeatmapButton.disabled = true;

    try {
        const heatmap = await fetchSuitabilityHeatmap(crop, bounds);

        if (requestId !== heatmapRequestId) {
            return;
        }

        showSuitabilityHeatmap(heatmap);
        const cellCount = Array.isArray(heatmap.features) ? heatmap.features.length : 0;
        if (cellCount === 0) {
            setHeatmapStatus(`No ${cropLabel} suitability cells returned for this map view.`);
            return;
        }

        setHeatmapStatus(`Showing ${cropLabel} heatmap with ${cellCount} scored cells.`);
    } catch (error) {
        if (requestId !== heatmapRequestId) {
            return;
        }

        clearSuitabilityHeatmap();
        setHeatmapStatus(error.message);
    } finally {
        if (requestId === heatmapRequestId) {
            generateHeatmapButton.disabled = false;
        }
    }
}

async function fetchSuitabilityHeatmap(crop, bounds) {
    const url = new URL("/suitability/heatmap", API_BASE_URL);
    url.searchParams.set("crop", crop);
    url.searchParams.set("west", bounds.getWest().toFixed(8));
    url.searchParams.set("south", bounds.getSouth().toFixed(8));
    url.searchParams.set("east", bounds.getEast().toFixed(8));
    url.searchParams.set("north", bounds.getNorth().toFixed(8));
    url.searchParams.set("rows", String(HEATMAP_GRID_SIZE));
    url.searchParams.set("cols", String(HEATMAP_GRID_SIZE));

    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(payload.detail || "Unable to generate suitability heatmap.");
    }

    return payload;
}

function showSuitabilityHeatmap(heatmap) {
    removeSuitabilityHeatmapLayer();

    if (!heatmap.features || !heatmap.features.length) {
        setHeatmapStatus("No scored cells returned for this map view.");
        return;
    }

    activeSuitabilityLayer = L.geoJSON(heatmap, {
        pane: "suitabilityHeatmapPane",
        style: getHeatmapCellStyle,
        onEachFeature: bindHeatmapFeaturePopup,
    }).addTo(map);
}

function clearSuitabilityHeatmap() {
    heatmapRequestId += 1;
    removeSuitabilityHeatmapLayer();
    setHeatmapStatus("Select a crop and generate a suitability heatmap.");
    generateHeatmapButton.disabled = false;
}

function removeSuitabilityHeatmapLayer() {
    if (activeSuitabilityLayer && map.hasLayer(activeSuitabilityLayer)) {
        map.removeLayer(activeSuitabilityLayer);
    }

    activeSuitabilityLayer = null;
}

function getHeatmapCellStyle(feature) {
    const color = feature.properties?.color || "#d94f3d";
    return {
        color,
        fillColor: color,
        fillOpacity: 0.52,
        opacity: 0.12,
        weight: 1,
    };
}

function bindHeatmapFeaturePopup(feature, layer) {
    const properties = feature.properties || {};
    const score = normalizeScore(properties.score);
    const cropLabel = getCropLabel(properties.crop);
    const suitabilityClass = titleCase(properties.class || "unknown");

    layer.bindPopup(`
        <article class="popup-card heatmap-popup">
            <header class="popup-card-header">
                <p class="popup-kicker">Suitability Heatmap</p>
                <h3>${escapeHtml(cropLabel)}: ${score}%</h3>
            </header>
            <div class="popup-coordinate-card">
                <span>Sample point</span>
                <strong>${Number(properties.lat).toFixed(5)}, ${Number(properties.lon).toFixed(5)}</strong>
            </div>
            <p class="popup-land-cover">${escapeHtml(suitabilityClass)} suitability</p>
        </article>
    `);
}

function setHeatmapStatus(message) {
    heatmapStatus.textContent = message;
}

function renderLegend(metadata) {
    if (!metadata) {
        layerLegend.className = "legend-list empty-state";
        layerLegend.textContent = "Select a layer to display its legend.";
        return;
    }

    layerLegend.className = "legend-list";
    layerLegend.innerHTML = metadata.legend.map((item) => {
        return `
            <div class="legend-row">
                <span class="legend-swatch" style="background:${escapeHtml(item.color)}"></span>
                <span>${escapeHtml(item.label)}</span>
            </div>
        `;
    }).join("");
}

function renderLayerInfo(metadata) {
    if (!metadata) {
        layerInfo.className = "layer-info empty-state";
        layerInfo.textContent = "Select a layer to display metadata.";
        return;
    }

    layerInfo.className = "layer-info";
    layerInfo.innerHTML = `
        <div>
            <span>Layer</span>
            <strong>${escapeHtml(metadata.label)}</strong>
        </div>
        <div>
            <span>Source</span>
            <strong>${escapeHtml(metadata.source)}</strong>
        </div>
        <div>
            <span>Resolution</span>
            <strong>${escapeHtml(metadata.resolution)}</strong>
        </div>
        <div>
            <span>Tile render</span>
            <strong>${Number(metadata.tile_pixels || 256)} px</strong>
        </div>
        <div>
            <span>CRS</span>
            <strong>${escapeHtml(metadata.crs)}</strong>
        </div>
        <div>
            <span>Extent</span>
            <strong>${escapeHtml(metadata.extent)}</strong>
        </div>
    `;
}

function renderEnvironmentalData(data) {
    dataOutput.className = "environment-summary";
    dataOutput.innerHTML = createFormattedEnvironmentGroups(data)
        .map(renderSidebarGroup)
        .join("");
}

function renderSidebarGroup(group) {
    const rows = group.fields.map((field) => {
        return `
            <div class="environment-row">
                <span>${escapeHtml(field.label)}</span>
                <strong>${escapeHtml(field.value)}</strong>
            </div>
        `;
    }).join("");

    return `
        <section class="environment-group">
            <h3>${escapeHtml(group.title)}</h3>
            <div class="environment-group-body">${rows}</div>
        </section>
    `;
}

function renderSuitabilityDashboard(prediction) {
    if (!prediction || !prediction.suitability_scores) {
        recommendationOutput.className = "recommendation-box";
        recommendationOutput.textContent = "Click the map or search coordinates to calculate crop suitability.";
        return;
    }

    const scores = prediction.suitability_scores;
    const recommendedCrop = prediction.recommended_crop;
    const confidence = prediction.confidence || {};
    const scoreCards = CROP_OPTIONS.map((crop) => {
        const score = normalizeScore(scores[crop.key]);
        const isBestCrop = crop.key === recommendedCrop;
        return renderSuitabilityScoreCard(crop, score, isBestCrop);
    }).join("");

    recommendationOutput.className = "recommendation-box suitability-dashboard";
    recommendationOutput.innerHTML = `
        <div class="recommendation-summary">
            <span>Recommended crop</span>
            <strong>${escapeHtml(getCropLabel(recommendedCrop))}</strong>
        </div>
        <div class="confidence-summary">
            <span>Confidence</span>
            <strong>${escapeHtml(confidence.level || "Low")} (${normalizeScore(confidence.score)}%)</strong>
            <small>${Number(confidence.available_features || 0)} of ${Number(confidence.total_features || 0)} features available</small>
        </div>
        <div class="suitability-score-list">${scoreCards}</div>
        <p class="model-note">${escapeHtml(formatModelType(prediction.model_type))}</p>
    `;
}

function renderSuitabilityScoreCard(crop, score, isBestCrop) {
    const cardClass = isBestCrop ? "suitability-score-card best-crop" : "suitability-score-card";
    return `
        <div class="${cardClass}">
            <div class="score-card-header">
                <span>${escapeHtml(crop.label)}</span>
                <strong>${score}%</strong>
            </div>
            <div class="score-track" aria-hidden="true">
                <i style="width:${score}%"></i>
            </div>
            ${isBestCrop ? '<small>Best match</small>' : ""}
        </div>
    `;
}

function renderError(message) {
    dataOutput.className = "data-grid empty-state";
    dataOutput.textContent = message;
    recommendationOutput.textContent = "Prediction unavailable.";
}

function createLoadingPopup(lat, lon) {
    return `
        <article class="popup-card">
            <header class="popup-card-header">
                <p class="popup-kicker">Environmental Data</p>
                <h3>Loading raster values</h3>
            </header>
            <div class="popup-coordinate-card">
                <span>Coordinates</span>
                <strong>${lat.toFixed(6)}, ${lon.toFixed(6)}</strong>
            </div>
            <p class="popup-muted">Loading raster values from PostGIS...</p>
        </article>
    `;
}

function createDataPopup(lat, lon, prediction) {
    const data = prediction.environmental_data || {};
    const groups = createFormattedEnvironmentGroups(data)
        .map(renderPopupGroup)
        .join("");
    const scoreRows = CROP_OPTIONS.map((crop) => {
        const score = normalizeScore(prediction.suitability_scores?.[crop.key]);
        const bestClass = crop.key === prediction.recommended_crop ? " best-crop" : "";
        return `
            <div class="popup-score-row${bestClass}">
                <span>${escapeHtml(crop.label)}</span>
                <strong>${score}%</strong>
            </div>
        `;
    }).join("");
    const confidence = prediction.confidence || {};

    return `
        <article class="popup-card">
            <header class="popup-card-header">
                <p class="popup-kicker">Crop Suitability</p>
                <h3>${escapeHtml(getCropLabel(prediction.recommended_crop))} recommended</h3>
            </header>
            <div class="popup-coordinate-card">
                <span>Coordinates</span>
                <strong>${lat.toFixed(6)}, ${lon.toFixed(6)}</strong>
            </div>
            <section class="popup-section">
                <h4>Suitability Scores</h4>
                <div class="popup-data">${scoreRows}</div>
                <p class="popup-confidence">
                    Confidence: ${escapeHtml(confidence.level || "Low")} (${normalizeScore(confidence.score)}%)
                </p>
            </section>
            ${groups}
        </article>
    `;
}

function renderPopupGroup(group) {
    if (group.key === "land_cover") {
        return `
            <section class="popup-section">
                <h4>${escapeHtml(group.title)}</h4>
                <div class="popup-land-cover">${escapeHtml(group.fields[0].value)}</div>
            </section>
        `;
    }

    const rows = group.fields.map((field) => {
        return `
            <div class="popup-row">
                <span>${escapeHtml(field.label)}</span>
                <strong>${escapeHtml(field.value)}</strong>
            </div>
        `;
    }).join("");

    return `
        <section class="popup-section">
            <h4>${escapeHtml(group.title)}</h4>
            <div class="popup-data">${rows}</div>
        </section>
    `;
}

function createErrorPopup(lat, lon, message) {
    return `
        <article class="popup-card">
            <header class="popup-card-header">
                <p class="popup-kicker">Environmental Data</p>
                <h3>Lookup failed</h3>
            </header>
            <div class="popup-coordinate-card">
                <span>Coordinates</span>
                <strong>${lat.toFixed(6)}, ${lon.toFixed(6)}</strong>
            </div>
            <p class="popup-error">${escapeHtml(message)}</p>
        </article>
    `;
}

function createFormattedEnvironmentGroups(data) {
    return ENVIRONMENT_GROUPS.map((group) => {
        return {
            key: group.key,
            title: group.title,
            fields: group.fields.map((field) => {
                return {
                    key: field.key,
                    label: field.label,
                    value: field.format(data[field.key]),
                };
            }),
        };
    });
}

function formatPH(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    // SoilGrids pH is commonly stored as pH x 10.
    const displayValue = numericValue > 14 ? numericValue / 10 : numericValue;
    return displayValue.toFixed(1);
}

function formatSOC(value) {
    return formatNumberWithUnit(value, "g/kg", { decimals: 0 });
}

function formatCEC(value) {
    return formatNumberWithUnit(value, "mmol(c)/kg", { decimals: 0 });
}

function formatClay(value) {
    // Soil texture fractions are stored as percent x 10.
    return formatPercentage(value, { scale: 10, decimals: 1 });
}

function formatSand(value) {
    // Soil texture fractions are stored as percent x 10.
    return formatPercentage(value, { scale: 10, decimals: 1 });
}

function formatNitrogen(value) {
    // Soil nitrogen is stored as percent x 1000.
    return formatPercentage(value, { scale: 1000, decimals: 2 });
}

function formatDensity(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    // Bulk density is stored as g/cm3 x 100.
    return `${(numericValue / 100).toFixed(2)} g/cm\u00B3`;
}

function formatRainfall(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    const rainfallMm = numericValue < 100 ? numericValue * 100 : numericValue;
    return `${Math.round(rainfallMm)} mm/year`;
}

function formatTemperature(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    // Temperature is stored as degrees Celsius x 100.
    return `${(numericValue / 100).toFixed(2)} \u00B0C`;
}

function formatElevation(value) {
    return formatNumberWithUnit(value, "m", { decimals: 0 });
}

function formatSlope(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    return `${formatDecimal(numericValue, 2)}\u00B0`;
}

function formatLandCover(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    const classCode = Math.round(numericValue);
    return LAND_COVER_CLASSES[classCode] || `Unknown class (${classCode})`;
}

function formatPercentage(value, options = {}) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    const scale = options.scale ?? 1;
    const decimals = options.decimals ?? 1;
    return `${(numericValue / scale).toFixed(decimals)} %`;
}

function formatNumberWithUnit(value, unit, options = {}) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return NOT_AVAILABLE;
    }

    const decimals = options.decimals ?? 0;
    return `${formatDecimal(numericValue, decimals)} ${unit}`;
}

function formatDecimal(value, decimals) {
    return Number(value).toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function getNumericValue(value) {
    if (value === null || value === undefined || value === "") {
        return null;
    }

    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
}

function normalizeScore(value) {
    const numericValue = getNumericValue(value);
    if (numericValue === null) {
        return 0;
    }

    return Math.max(0, Math.min(100, Math.round(numericValue)));
}

function getCropLabel(cropKey) {
    const crop = CROP_OPTIONS.find((option) => option.key === cropKey);
    return crop ? crop.label : "Unknown crop";
}

function formatModelType(modelType) {
    if (modelType === "rule_based_v1") {
        return "Rule-based MVP scoring. Random Forest can replace this prediction module later.";
    }

    return modelType || "Prediction model";
}

function titleCase(value) {
    return String(value)
        .replaceAll("_", " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

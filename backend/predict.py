from __future__ import annotations

import math
from typing import Any, Mapping


SUPPORTED_CROPS = ("rice", "maize", "banana")

FEATURE_KEYS = (
    "ph",
    "soc",
    "cec",
    "clay",
    "nitrogen",
    "sand",
    "bulkdensity",
    "rainfall",
    "temperature",
    "dem",
    "slope",
    "land_cover",
)

FEATURE_WEIGHTS = {
    "ph": 1.0,
    "soc": 0.8,
    "cec": 0.7,
    "clay": 0.7,
    "nitrogen": 0.7,
    "sand": 0.6,
    "bulkdensity": 0.6,
    "rainfall": 1.2,
    "temperature": 1.1,
    "dem": 0.7,
    "slope": 0.8,
    "land_cover": 0.7,
}

# MVP agronomic ranges. These are intentionally transparent and easy to replace
# with a trained model while keeping the external API response shape stable.
CROP_RULES: dict[str, dict[str, Any]] = {
    "rice": {
        "label": "Rice",
        "ranges": {
            "ph": (4.5, 5.5, 7.0, 8.2),
            "soc": (5.0, 20.0, 80.0, 160.0),
            "cec": (5.0, 16.0, 40.0, 80.0),
            "clay": (8.0, 20.0, 45.0, 70.0),
            "nitrogen": (0.02, 0.10, 0.35, 0.60),
            "sand": (0.0, 5.0, 35.0, 70.0),
            "bulkdensity": (0.75, 0.95, 1.35, 1.75),
            "rainfall": (1100.0, 1800.0, 3200.0, 4600.0),
            "temperature": (18.0, 24.0, 32.0, 37.0),
            "dem": (0.0, 0.0, 800.0, 1500.0),
            "slope": (0.0, 0.0, 3.0, 9.0),
        },
        "land_cover": {
            30: 88,
            40: 100,
            20: 65,
            10: 35,
            50: 20,
            60: 30,
            70: 8,
        },
    },
    "maize": {
        "label": "Maize",
        "ranges": {
            "ph": (5.0, 5.8, 7.5, 8.3),
            "soc": (5.0, 15.0, 60.0, 140.0),
            "cec": (4.0, 10.0, 35.0, 70.0),
            "clay": (5.0, 10.0, 35.0, 60.0),
            "nitrogen": (0.02, 0.10, 0.30, 0.55),
            "sand": (12.0, 25.0, 55.0, 78.0),
            "bulkdensity": (0.85, 1.05, 1.45, 1.80),
            "rainfall": (350.0, 600.0, 1400.0, 2200.0),
            "temperature": (15.0, 20.0, 30.0, 36.0),
            "dem": (0.0, 0.0, 1500.0, 2500.0),
            "slope": (0.0, 0.0, 8.0, 18.0),
        },
        "land_cover": {
            30: 92,
            40: 100,
            20: 70,
            60: 55,
            10: 30,
            50: 20,
            70: 5,
        },
    },
    "banana": {
        "label": "Banana",
        "ranges": {
            "ph": (4.5, 5.5, 7.5, 8.3),
            "soc": (8.0, 25.0, 100.0, 180.0),
            "cec": (5.0, 15.0, 50.0, 90.0),
            "clay": (5.0, 15.0, 40.0, 65.0),
            "nitrogen": (0.03, 0.12, 0.40, 0.70),
            "sand": (10.0, 20.0, 50.0, 75.0),
            "bulkdensity": (0.70, 0.90, 1.30, 1.70),
            "rainfall": (900.0, 1500.0, 3200.0, 4800.0),
            "temperature": (18.0, 25.0, 32.0, 38.0),
            "dem": (0.0, 0.0, 1000.0, 1800.0),
            "slope": (0.0, 0.0, 12.0, 25.0),
        },
        "land_cover": {
            30: 94,
            40: 100,
            20: 78,
            10: 58,
            60: 45,
            50: 20,
            70: 5,
        },
    },
}


def prepare_features(environmental_data: Mapping[str, Any]) -> dict[str, float | int | None]:
    """Normalize raw raster values into agronomic units for model/scoring input."""

    return {
        "ph": _normalize_ph(environmental_data.get("ph")),
        "soc": _number_or_none(environmental_data.get("soc")),
        "cec": _normalize_cec(environmental_data.get("cec")),
        "clay": _normalize_texture_percent(environmental_data.get("clay")),
        "nitrogen": _normalize_nitrogen_percent(environmental_data.get("nitrogen")),
        "sand": _normalize_texture_percent(environmental_data.get("sand")),
        "bulkdensity": _normalize_bulkdensity(environmental_data.get("bulkdensity")),
        "rainfall": _normalize_rainfall(environmental_data.get("rainfall")),
        "temperature": _normalize_temperature(environmental_data.get("temperature")),
        "dem": _number_or_none(environmental_data.get("dem")),
        "slope": _number_or_none(environmental_data.get("slope")),
        "land_cover": _normalize_land_cover(environmental_data.get("land_cover")),
    }


def generate_suitability_score(crop: str, features: Mapping[str, Any]) -> int:
    """Return a 0-100 suitability score for one crop using rule-based scoring."""

    crop_key = crop.lower()
    if crop_key not in CROP_RULES:
        supported = ", ".join(SUPPORTED_CROPS)
        raise ValueError(f"Unsupported crop '{crop}'. Supported crops: {supported}.")

    factor_scores = _score_crop_factors(crop_key, features)
    if not factor_scores:
        return 0

    weighted_score = sum(score * weight for _, score, weight in factor_scores)
    total_weight = sum(weight for _, _, weight in factor_scores)
    return int(round(_clamp(weighted_score / total_weight, 0.0, 100.0)))


def predict_crop_suitability(environmental_data: Mapping[str, Any]) -> dict[str, Any]:
    """Predict crop suitability for the MVP rule-based model.

    A future Random Forest implementation can replace this function while keeping
    FastAPI routes and frontend response handling stable.
    """

    features = prepare_features(environmental_data)
    scores = {
        crop: generate_suitability_score(crop, features)
        for crop in SUPPORTED_CROPS
    }
    recommended_crop = max(scores, key=scores.get)
    confidence = _calculate_confidence(scores, features)

    return {
        "model_type": "rule_based_v1",
        "features": features,
        "scores": scores,
        "recommended_crop": recommended_crop,
        "confidence": confidence,
    }


def classify_suitability(score: int | float | None) -> str:
    """Class name used by the heatmap and dashboard."""

    if score is None:
        return "unknown"
    if score >= 75:
        return "high"
    if score >= 50:
        return "moderate"
    return "low"


def suitability_color(score: int | float | None) -> str:
    """Map suitability score to the MVP red/yellow/green palette."""

    suitability_class = classify_suitability(score)
    if suitability_class == "high":
        return "#2f9e44"
    if suitability_class == "moderate":
        return "#f2c94c"
    if suitability_class == "low":
        return "#d94f3d"
    return "#000000"


def _score_crop_factors(
    crop: str,
    features: Mapping[str, Any],
) -> list[tuple[str, float, float]]:
    crop_rules = CROP_RULES[crop]
    factor_scores: list[tuple[str, float, float]] = []

    for feature_key, limits in crop_rules["ranges"].items():
        value = _number_or_none(features.get(feature_key))
        if value is None:
            continue

        score = _score_range(value, limits)
        factor_scores.append((feature_key, score, FEATURE_WEIGHTS[feature_key]))

    land_cover = _normalize_land_cover(features.get("land_cover"))
    if land_cover is not None:
        score = float(crop_rules["land_cover"].get(land_cover, 45))
        factor_scores.append(("land_cover", score, FEATURE_WEIGHTS["land_cover"]))

    return factor_scores


def _score_range(value: float, limits: tuple[float, float, float, float]) -> float:
    absolute_min, optimal_min, optimal_max, absolute_max = limits

    if value < absolute_min or value > absolute_max:
        return 0.0
    if optimal_min <= value <= optimal_max:
        return 100.0
    if value < optimal_min:
        span = optimal_min - absolute_min
        return 100.0 if span <= 0 else ((value - absolute_min) / span) * 100.0

    span = absolute_max - optimal_max
    return 100.0 if span <= 0 else ((absolute_max - value) / span) * 100.0


def _calculate_confidence(
    scores: Mapping[str, int],
    features: Mapping[str, Any],
) -> dict[str, Any]:
    available_features = sum(
        1 for key in FEATURE_KEYS
        if features.get(key) is not None
    )
    completeness = available_features / len(FEATURE_KEYS)

    ranked_scores = sorted(scores.values(), reverse=True)
    top_score = ranked_scores[0] if ranked_scores else 0
    second_score = ranked_scores[1] if len(ranked_scores) > 1 else 0
    separation = max(top_score - second_score, 0) / 100

    confidence_score = int(round(_clamp((0.75 * completeness + 0.25 * separation) * 100, 0, 100)))
    if confidence_score >= 70:
        level = "High"
    elif confidence_score >= 45:
        level = "Moderate"
    else:
        level = "Low"

    return {
        "score": confidence_score,
        "level": level,
        "available_features": available_features,
        "total_features": len(FEATURE_KEYS),
    }


def _normalize_ph(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number / 10 if number > 14 else number


def _normalize_cec(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number / 10 if number > 100 else number


def _normalize_texture_percent(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number / 10 if number > 100 else number


def _normalize_nitrogen_percent(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    if number > 10:
        return number / 1000
    return number


def _normalize_bulkdensity(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number / 100 if number > 10 else number


def _normalize_rainfall(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number * 100 if 0 < number < 100 else number


def _normalize_temperature(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number / 100 if number > 80 else number


def _normalize_land_cover(value: Any) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return int(round(number))


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)

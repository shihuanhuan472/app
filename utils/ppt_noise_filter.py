import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


DEFAULT_NOISE_TEXT_PATTERNS = [
    r"\bmgi[-\s]?service\s*@\s*mgi[-\s]?tech\.com\b",
    r"\bwww\.mgi[-\s]?tech\.com\b",
    r"\bmgi[-\s]?tech\.com\b",
    r"\bbuilding\s+no\.?\s*11\b",
    r"\bbeishan\s+industrial\s+zone\b",
    r"\byantian\s+district\b",
    r"\bshenzhen\s*518083\b",
    r"深圳市盐田区北山工业区",
    r"北山工业区\s*11\s*栋",
    r"版权所有|版权声明|all\s+rights\s+reserved|confidential",
]

GENERIC_CONTACT_PATTERNS = [
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    r"\bhttps?://[^\s]+",
    r"\bwww\.[^\s]+",
]


class PPTNoiseFilter:
    """Filter boilerplate text and decorative images before AI parsing.

    The filter is intentionally conservative for evidence-like screenshots. It
    targets known presentation noise: blank placeholders, blue brand/DNA
    backgrounds, and contact/footer boilerplate.
    """

    def __init__(self) -> None:
        self.enabled = _env_bool("PPT_NOISE_FILTER_ENABLED", True)
        self.config_path = self._resolve_config_path(
            os.getenv("PPT_NOISE_FILTER_CONFIG", "config/ppt_noise_filter.json")
        )
        config = self._load_config(self.config_path)

        text_patterns = list(DEFAULT_NOISE_TEXT_PATTERNS)
        text_patterns.extend(config.get("text_patterns") or [])
        self.text_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in text_patterns if pattern
        ]
        self.generic_contact_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in GENERIC_CONTACT_PATTERNS
        ]

        self.footer_zone_ratio = float(
            config.get(
                "footer_zone_ratio",
                _env_float("PPT_NOISE_FOOTER_ZONE_RATIO", 0.22),
            )
        )
        self.blank_light_ratio = float(config.get("blank_light_ratio", 0.985))
        self.blank_dark_ratio = float(config.get("blank_dark_ratio", 0.985))
        self.blank_dynamic_range = float(config.get("blank_dynamic_range", 18.0))
        self.blank_channel_std = float(config.get("blank_channel_std", 7.0))
        self.blue_ratio = float(config.get("blue_ratio", 0.52))
        self.blue_mean_delta = float(config.get("blue_mean_delta", 32.0))
        self.decorative_min_width = int(config.get("decorative_min_width", 520))
        self.decorative_min_height = int(config.get("decorative_min_height", 240))
        self.wide_footer_aspect_ratio = float(config.get("wide_footer_aspect_ratio", 3.5))
        self.low_saturation = float(config.get("low_saturation", 24.0))

    def _resolve_config_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _load_config(self, path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            print(f"[PPTNoiseFilter] failed to read config {path}: {error}", flush=True)
            return {}

    def sanitize_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text or ""

        kept_lines = []
        for raw_line in str(text).splitlines():
            line = raw_line.strip()
            if not line:
                kept_lines.append(raw_line)
                continue
            if self.is_noise_text(line):
                print(
                    f"[PPTNoiseFilter] filtered boilerplate text: {line[:120]}",
                    flush=True,
                )
                continue
            kept_lines.append(raw_line)

        sanitized = "\n".join(kept_lines)
        sanitized = re.sub(r"\n{4,}", "\n\n\n", sanitized).strip()
        return sanitized

    def is_noise_text(
        self,
        text: str,
        *,
        top: Optional[int] = None,
        slide_height: Optional[int] = None,
    ) -> bool:
        if not self.enabled:
            return False
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return False

        if any(pattern.search(normalized) for pattern in self.text_patterns):
            return True

        # Generic emails/URLs are usually boilerplate when they are short lines
        # or live in the slide footer/header region.
        has_generic_contact = any(
            pattern.search(normalized) for pattern in self.generic_contact_patterns
        )
        if not has_generic_contact:
            return False

        if len(normalized) <= 180:
            return True

        if top is not None and slide_height:
            footer_start = int(slide_height * (1.0 - self.footer_zone_ratio))
            header_end = int(slide_height * self.footer_zone_ratio)
            return top >= footer_start or top <= header_end

        return False

    def should_filter_image_path(
        self,
        image_path: Path | str,
        *,
        area_ratio: Optional[float] = None,
        source: str = "image",
    ) -> Tuple[bool, str]:
        if not self.enabled:
            return False, ""
        try:
            with Image.open(image_path) as image:
                return self.should_filter_image(image, area_ratio=area_ratio, source=source)
        except Exception:
            return False, ""

    def should_filter_image_bytes(
        self,
        image_bytes: bytes,
        *,
        area_ratio: Optional[float] = None,
        source: str = "image-bytes",
    ) -> Tuple[bool, str]:
        if not self.enabled:
            return False, ""
        try:
            import io

            with Image.open(io.BytesIO(image_bytes)) as image:
                return self.should_filter_image(image, area_ratio=area_ratio, source=source)
        except Exception:
            return False, ""

    def should_filter_image(
        self,
        image: Image.Image,
        *,
        area_ratio: Optional[float] = None,
        source: str = "image",
    ) -> Tuple[bool, str]:
        if not self.enabled:
            return False, ""

        stats = self._image_stats(image)
        if not stats:
            return False, ""

        if self._is_blank_or_placeholder(stats):
            return True, "blank_or_low_information"

        if self._is_blue_decorative_background(stats, area_ratio):
            return True, "blue_decorative_background"

        if self._is_wide_low_info_footer(stats, area_ratio):
            return True, "wide_low_information_footer"

        return False, ""

    def _image_stats(self, image: Image.Image) -> Dict[str, float]:
        rgb = image.convert("RGB")
        width, height = rgb.size
        if width <= 1 or height <= 1:
            return {}

        sample = rgb.copy()
        sample.thumbnail((320, 320), Image.Resampling.LANCZOS)
        arr = np.asarray(sample, dtype=np.float32)
        if arr.size == 0:
            return {}

        channels_mean = arr.mean(axis=(0, 1))
        channels_std = arr.std(axis=(0, 1))
        channel_max = arr.max(axis=2)
        channel_min = arr.min(axis=2)
        saturation = channel_max - channel_min
        luminance = (
            0.299 * arr[:, :, 0]
            + 0.587 * arr[:, :, 1]
            + 0.114 * arr[:, :, 2]
        )
        dynamic_range = float(np.percentile(luminance, 98) - np.percentile(luminance, 2))
        light_ratio = float(np.mean(luminance > 246))
        dark_ratio = float(np.mean(luminance < 10))
        blue_mask = (
            (arr[:, :, 2] > arr[:, :, 0] + 35)
            & (arr[:, :, 2] > arr[:, :, 1] + 10)
            & (arr[:, :, 2] > 90)
        )

        return {
            "width": float(width),
            "height": float(height),
            "aspect_ratio": float(width / max(height, 1)),
            "mean_r": float(channels_mean[0]),
            "mean_g": float(channels_mean[1]),
            "mean_b": float(channels_mean[2]),
            "max_std": float(channels_std.max()),
            "sat_mean": float(saturation.mean()),
            "dynamic_range": dynamic_range,
            "light_ratio": light_ratio,
            "dark_ratio": dark_ratio,
            "blue_ratio": float(np.mean(blue_mask)),
        }

    def _is_blank_or_placeholder(self, stats: Dict[str, float]) -> bool:
        if stats["light_ratio"] >= self.blank_light_ratio:
            return True
        if stats["dark_ratio"] >= self.blank_dark_ratio:
            return True
        return (
            stats["dynamic_range"] <= self.blank_dynamic_range
            and stats["max_std"] <= self.blank_channel_std
        )

    def _is_blue_decorative_background(
        self,
        stats: Dict[str, float],
        area_ratio: Optional[float],
    ) -> bool:
        is_large_enough = (
            stats["width"] >= self.decorative_min_width
            and stats["height"] >= self.decorative_min_height
        )
        if not is_large_enough:
            return False

        if area_ratio is not None and area_ratio < 0.45:
            return False

        blue_mean_delta = stats["mean_b"] - max(stats["mean_r"], stats["mean_g"])
        blue_dominant = (
            stats["blue_ratio"] >= self.blue_ratio
            or blue_mean_delta >= self.blue_mean_delta
        )
        if not blue_dominant:
            return False

        # Evidence screenshots are usually less color-dominant and more UI/text
        # like. This rule intentionally targets saturated, full-background art.
        return stats["sat_mean"] >= self.low_saturation

    def _is_wide_low_info_footer(
        self,
        stats: Dict[str, float],
        area_ratio: Optional[float],
    ) -> bool:
        if stats["aspect_ratio"] < self.wide_footer_aspect_ratio:
            return False
        if area_ratio is not None and area_ratio > 0.35:
            return False
        if stats["sat_mean"] > self.low_saturation:
            return False
        return stats["light_ratio"] + stats["dark_ratio"] >= 0.72

import io
import math
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class LogoOnlyFilter:
    """Reject an image only when the whole image matches a configured logo."""

    HASH_SIZE = 8
    HIGH_FREQUENCY_FACTOR = 4
    BACKGROUND_TOLERANCE = 24
    MAX_ASPECT_LOG_DELTA = 0.30
    REFERENCE_SIZES = (175, 125, 96)

    def __init__(self) -> None:
        self.enabled = _env_bool("LOGO_ONLY_FILTER_ENABLED", True)
        configured_dir = os.getenv(
            "LOGO_ONLY_REFERENCE_DIR",
            "config/ignored_images/huada",
        ).strip()
        reference_dir = Path(configured_dir).expanduser()
        if not reference_dir.is_absolute():
            reference_dir = PROJECT_ROOT / reference_dir
        self.reference_dir = reference_dir.resolve()
        self.max_distance = max(
            0,
            min(int(os.getenv("LOGO_ONLY_PHASH_DISTANCE", "6")), self.HASH_SIZE**2),
        )
        self._dct_matrix = self._build_dct_matrix(
            self.HASH_SIZE * self.HIGH_FREQUENCY_FACTOR
        )
        self._references = self._load_references() if self.enabled else []

        if self.enabled:
            if self._references:
                print(
                    "[LogoOnlyFilter] enabled "
                    f"references={len(self._references)} "
                    f"distance={self.max_distance}",
                    flush=True,
                )
            else:
                print(
                    "[LogoOnlyFilter] enabled but no valid reference images found: "
                    f"{self.reference_dir}",
                    flush=True,
                )

    @staticmethod
    def _build_dct_matrix(size: int) -> np.ndarray:
        positions = np.arange(size, dtype=np.float64)
        frequencies = positions[:, None]
        matrix = np.cos(
            math.pi * (2.0 * positions + 1.0) * frequencies / (2.0 * size)
        )
        matrix[0, :] *= math.sqrt(1.0 / size)
        matrix[1:, :] *= math.sqrt(2.0 / size)
        return matrix

    def _load_references(self) -> list[Tuple[str, int, float]]:
        if not self.reference_dir.is_dir():
            return []

        references = []
        for path in sorted(self.reference_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue
            try:
                with Image.open(path) as image:
                    candidates = [(path.name, image.copy())]
                    for size in self.REFERENCE_SIZES:
                        resized = image.copy()
                        resized.thumbnail((size, size), Image.Resampling.LANCZOS)
                        candidates.append((f"{path.name}@{size}", resized))

                    for name, candidate in candidates:
                        signature = self._signature(candidate)
                        if signature is None:
                            continue
                        image_hash, aspect_ratio = signature
                        references.append((name, image_hash, aspect_ratio))
            except Exception:
                continue
        return references

    def _foreground_mask(self, image: Image.Image) -> Optional[Image.Image]:
        rgba = image.convert("RGBA")
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.int16)
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)

        has_transparent_background = (
            int(alpha.min()) < 240
            and float(np.mean(alpha < 240)) >= 0.01
        )
        if has_transparent_background:
            mask = alpha
        else:
            corners = np.array(
                [
                    rgb[0, 0],
                    rgb[0, -1],
                    rgb[-1, 0],
                    rgb[-1, -1],
                ],
                dtype=np.int16,
            )
            background = np.median(corners, axis=0)
            difference = np.max(np.abs(rgb - background), axis=2)
            mask = np.where(
                difference > self.BACKGROUND_TOLERANCE,
                255,
                0,
            ).astype(np.uint8)

        foreground_y, foreground_x = np.where(mask > 24)
        if foreground_x.size == 0 or foreground_y.size == 0:
            return None

        left = int(foreground_x.min())
        right = int(foreground_x.max()) + 1
        top = int(foreground_y.min())
        bottom = int(foreground_y.max()) + 1
        if right - left < 4 or bottom - top < 4:
            return None

        return Image.fromarray(mask, mode="L").crop((left, top, right, bottom))

    def _signature(self, image: Image.Image) -> Optional[Tuple[int, float]]:
        mask = self._foreground_mask(image)
        if mask is None:
            return None

        aspect_ratio = mask.width / max(mask.height, 1)
        size = self.HASH_SIZE * self.HIGH_FREQUENCY_FACTOR
        square = Image.new("L", (size, size), 0)
        fitted = mask.copy()
        fitted.thumbnail((size - 4, size - 4), Image.Resampling.LANCZOS)
        square.paste(
            fitted,
            ((size - fitted.width) // 2, (size - fitted.height) // 2),
        )

        pixels = np.asarray(square, dtype=np.float64)
        dct = self._dct_matrix @ pixels @ self._dct_matrix.T
        low_frequencies = dct[: self.HASH_SIZE, : self.HASH_SIZE]
        comparison_values = low_frequencies.flatten()[1:]
        median = float(np.median(comparison_values))
        bits = (low_frequencies > median).flatten()

        image_hash = 0
        for bit in bits:
            image_hash = (image_hash << 1) | int(bit)
        return image_hash, aspect_ratio

    def _signature_from_path(self, image_path: Path) -> Optional[Tuple[int, float]]:
        try:
            with Image.open(image_path) as image:
                return self._signature(image)
        except Exception:
            return None

    def _signature_from_bytes(self, image_bytes: bytes) -> Optional[Tuple[int, float]]:
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                return self._signature(image)
        except Exception:
            return None

    def _match_signature(
        self,
        signature: Optional[Tuple[int, float]],
    ) -> Optional[Tuple[str, int]]:
        if not self.enabled or not self._references or signature is None:
            return None

        candidate_hash, candidate_aspect = signature
        best_match = None
        for name, reference_hash, reference_aspect in self._references:
            if candidate_aspect <= 0 or reference_aspect <= 0:
                continue
            aspect_delta = abs(math.log(candidate_aspect / reference_aspect))
            if aspect_delta > self.MAX_ASPECT_LOG_DELTA:
                continue

            distance = (candidate_hash ^ reference_hash).bit_count()
            if best_match is None or distance < best_match[1]:
                best_match = (name, distance)

        if best_match is None or best_match[1] > self.max_distance:
            return None
        return best_match

    def match_path(self, image_path: Path | str) -> Optional[Tuple[str, int]]:
        return self._match_signature(self._signature_from_path(Path(image_path)))

    def match_bytes(self, image_bytes: bytes) -> Optional[Tuple[str, int]]:
        return self._match_signature(self._signature_from_bytes(image_bytes))

    def should_filter_path(self, image_path: Path | str) -> bool:
        match = self.match_path(image_path)
        if match is None:
            return False
        reference_name, distance = match
        print(
            "[LogoOnlyFilter] filtered whole-image logo "
            f"candidate={image_path} reference={reference_name} distance={distance}",
            flush=True,
        )
        return True

    def should_filter_bytes(self, image_bytes: bytes, source: str = "image") -> bool:
        match = self.match_bytes(image_bytes)
        if match is None:
            return False
        reference_name, distance = match
        print(
            "[LogoOnlyFilter] filtered whole-image logo "
            f"candidate={source} reference={reference_name} distance={distance}",
            flush=True,
        )
        return True

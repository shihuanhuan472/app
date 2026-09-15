"""Rasterize WMF/EMF files before passing them to Pillow or vision models.

Pillow can identify metafiles, but loading them requires a Windows-specific
``drawwmf`` handler and does not cover every WMF variant.  The optional
backends below keep the application usable across Windows and Linux without
making ImageMagick or LibreOffice a mandatory Python dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


METAFILE_SUFFIXES = {".wmf", ".emf", ".wmz", ".emz"}


def is_metafile(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in METAFILE_SUFFIXES


def _pillow_can_load(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.load()
        return True
    except Exception:
        return False


def _rasterize_with_pillow(source: Path, target: Path, dpi: int) -> bool:
    """Use Pillow when its platform handler supports this particular file."""
    try:
        with Image.open(source) as image:
            try:
                image.load(dpi=dpi)
            except TypeError:
                image.load()
            image.convert("RGB").save(target, format="PNG")
        return target.is_file() and target.stat().st_size > 0
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _rasterize_with_wand(source: Path, target: Path, dpi: int) -> bool:
    """Use an already installed ImageMagick/libwmf through Wand, if present."""
    try:
        from wand.image import Image as WandImage
    except (ImportError, OSError):
        return False

    try:
        with WandImage(filename=str(source), resolution=(dpi, dpi)) as image:
            image.format = "png"
            image.background_color = "white"
            image.alpha_channel = "remove"
            image.save(filename=str(target))
        return target.is_file() and target.stat().st_size > 0
    except Exception as error:
        print(f"[Metafile] Wand 转换失败 {source.name}: {error}", flush=True)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _resolve_soffice() -> str:
    configured = os.getenv("PPT_SOFFICE_EXE", "").strip()
    candidates = []
    if configured:
        configured_path = Path(os.path.expanduser(configured))
        candidates.extend(
            [
                configured_path.with_name("soffice.com"),
                configured_path,
                configured_path.with_name("soffice.exe"),
            ]
        )
    for command in ("soffice.com", "soffice", "libreoffice"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _rasterize_with_libreoffice(source: Path, target: Path, runtime_dir: Path) -> bool:
    soffice = _resolve_soffice()
    if not soffice:
        return False

    output_dir = runtime_dir / "output"
    profile_dir = runtime_dir / "profile"
    process_temp_dir = runtime_dir / "temp"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    process_temp_dir.mkdir(parents=True, exist_ok=True)

    # A separate profile prevents a running desktop LibreOffice instance from
    # hijacking the headless process. Keep it on the configured document disk.
    profile_uri = profile_dir.resolve().as_uri()
    command = [
        soffice,
        f"-env:UserInstallation={profile_uri}",
        "--headless",
        "--convert-to",
        "png",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        process_env = os.environ.copy()
        # LibreOffice otherwise uses the account's system TEMP directory,
        # which can be a much smaller system drive than DOCUMENT_BASE_DIR.
        for temp_variable in ("TEMP", "TMP", "TMPDIR"):
            process_env[temp_variable] = str(process_temp_dir)
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            env=process_env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"[Metafile] LibreOffice 转换失败 {source.name}: {error}", flush=True)
        return False

    generated = output_dir / f"{source.stem}.png"
    if completed.returncode != 0 or not generated.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        if detail:
            detail = detail[-500:]
        print(
            f"[Metafile] LibreOffice 转换失败 {source.name}: "
            f"returncode={completed.returncode} {detail}",
            flush=True,
        )
        return False

    try:
        shutil.copy2(generated, target)
        return target.is_file() and target.stat().st_size > 0
    except OSError as error:
        print(f"[Metafile] 复制转换结果失败 {source.name}: {error}", flush=True)
        return False


def rasterize_metafile(
    source_path: str | os.PathLike[str],
    *,
    target_path: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    dpi: int = 144,
) -> str:
    """Return a Pillow-readable PNG path for a WMF/EMF source.

    A Pillow-readable PNG path is returned. A ``RuntimeError`` includes the
    available-backend guidance when no backend can rasterize the metafile.
    """
    source = Path(source_path)
    if not is_metafile(source):
        return str(source)
    if not source.is_file():
        raise FileNotFoundError(source)

    target = Path(target_path) if target_path else source.with_suffix(".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0 and _pillow_can_load(target):
        return str(target)

    if runtime_root is None:
        runtime_root = source.parent / ".metafile_runtime"
    runtime_root_path = Path(runtime_root)
    runtime_root_path.mkdir(parents=True, exist_ok=True)

    if _rasterize_with_pillow(source, target, dpi):
        return str(target)

    if _rasterize_with_wand(source, target, dpi):
        return str(target)

    with tempfile.TemporaryDirectory(
        prefix="metafile_",
        dir=str(runtime_root_path),
    ) as temp_dir:
        if _rasterize_with_libreoffice(source, target, Path(temp_dir)):
            return str(target)

    raise RuntimeError(
        f"无法转换 {source.name}：当前 Pillow 未提供 WMF/EMF 加载器。"
        "请安装带 libwmf 的 ImageMagick 并安装 Wand，或安装 LibreOffice，"
        "再设置 PPT_SOFFICE_EXE 指向 soffice.exe。"
    )


def prepare_image_for_pillow(
    image_path: str | os.PathLike[str],
    *,
    runtime_root: str | os.PathLike[str] | None = None,
) -> str:
    """Make a possibly-vector image safe for ``Image.open`` callers."""
    if not is_metafile(image_path):
        return str(image_path)
    source = Path(image_path)
    target = source.with_name(f"{source.stem}_rasterized.png")
    return rasterize_metafile(source, target_path=target, runtime_root=runtime_root)

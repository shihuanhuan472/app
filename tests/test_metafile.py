from PIL import Image

from utils import metafile


def test_non_metafile_path_is_unchanged(tmp_path):
    image_path = tmp_path / "image.png"
    Image.new("RGB", (4, 3), "white").save(image_path)

    assert metafile.prepare_image_for_pillow(image_path) == str(image_path)


def test_metafile_uses_a_png_target_when_pillow_backend_is_available(tmp_path, monkeypatch):
    source = tmp_path / "drawing.wmf"
    source.write_bytes(b"not-a-real-wmf")
    target = tmp_path / "drawing_rasterized.png"

    def fake_pillow_rasterizer(source_path, target_path, dpi):
        assert source_path == source
        assert dpi == 144
        Image.new("RGB", (8, 6), "white").save(target_path)
        return True

    monkeypatch.setattr(metafile, "_rasterize_with_pillow", fake_pillow_rasterizer)
    monkeypatch.setattr(metafile, "_rasterize_with_wand", lambda *args: False)
    monkeypatch.setattr(metafile, "_rasterize_with_libreoffice", lambda *args: False)

    result = metafile.prepare_image_for_pillow(source, runtime_root=tmp_path / "runtime")

    assert result == str(target)
    with Image.open(result) as image:
        assert image.size == (8, 6)
        assert image.format == "PNG"


def test_metafile_reports_missing_conversion_backend(tmp_path, monkeypatch):
    source = tmp_path / "drawing.emf"
    source.write_bytes(b"not-a-real-emf")

    monkeypatch.setattr(metafile, "_rasterize_with_pillow", lambda *args: False)
    monkeypatch.setattr(metafile, "_rasterize_with_wand", lambda *args: False)
    monkeypatch.setattr(metafile, "_rasterize_with_libreoffice", lambda *args: False)

    try:
        metafile.prepare_image_for_pillow(source, runtime_root=tmp_path / "runtime")
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("expected a clear conversion backend error")

    assert "WMF/EMF" in message
    assert "LibreOffice" in message

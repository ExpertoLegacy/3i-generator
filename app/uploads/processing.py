"""Turn uploaded files into images suitable for a Word report. Nothing leaves the computer."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

import pillow_heif
import pypdfium2
from PIL import Image, ImageOps, UnidentifiedImageError

pillow_heif.register_heif_opener()

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 2200
PDF_SCALE = 150 / 72  # render PDF pages at 150 dpi
Image.MAX_IMAGE_PIXELS = 80_000_000


class UploadError(Exception):
    pass


@dataclass
class ProcessedFile:
    filename: str
    mime: str
    data: bytes


def _encode(image: Image.Image) -> tuple[bytes, str]:
    """Photos become JPEG. Images with few colors, such as diagrams, stay PNG for sharp lines."""
    output = io.BytesIO()
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    if has_alpha:
        background = Image.new("RGB", image.size, (255, 255, 255))
        converted = image.convert("RGBA")
        background.paste(converted, mask=converted.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    sample = image.copy()
    sample.thumbnail((300, 300))
    few_colors = sample.getcolors(maxcolors=4096) is not None
    if few_colors:
        image.save(output, format="PNG", dpi=(200, 200), optimize=True)
        return output.getvalue(), "image/png"
    image.save(output, format="JPEG", quality=85, dpi=(200, 200), optimize=True)
    return output.getvalue(), "image/jpeg"


def _process_image(raw: bytes, name: str) -> ProcessedFile:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise UploadError(f"{name} could not be read as an image.") from exc
    image.thumbnail((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
    data, mime = _encode(image)
    return ProcessedFile(name, mime, data)


def _process_pdf(raw: bytes, name: str) -> list[ProcessedFile]:
    try:
        document = pypdfium2.PdfDocument(raw)
    except Exception as exc:  # pypdfium2 raises its own error types for damaged files
        raise UploadError(f"{name} could not be read as a PDF. If it has a password, remove it first.") from exc
    pages = []
    try:
        count = len(document)
        if count > 40:
            raise UploadError(f"{name} has {count} pages. Upload 40 pages or fewer at a time.")
        stem = PurePath(name).stem
        for index in range(count):
            image = document[index].render(scale=PDF_SCALE).to_pil()
            data, mime = _encode(image)
            label = f"{stem} page {index + 1}" if count > 1 else stem
            pages.append(ProcessedFile(label, mime, data))
    finally:
        document.close()
    return pages


def process_upload(raw: bytes, name: str) -> list[ProcessedFile]:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError(f"{name} is larger than 30 MB. Use a smaller file.")
    if not raw:
        raise UploadError(f"{name} is empty.")
    if raw[:5] == b"%PDF-":
        return _process_pdf(raw, name)
    return [_process_image(raw, name)]

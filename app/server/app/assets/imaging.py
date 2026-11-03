import io
import warnings
from dataclasses import dataclass
from PIL import Image, ImageOps, UnidentifiedImageError
from ..domain import digest
from ..errors import Problem
from .schemas import Crop

MAX_UPLOAD = 10 * 1024 * 1024
MAX_EDGE = 8192
MAX_PIXELS = 32 * 1024 * 1024
MAX_STORED = 64 * 1024 * 1024
MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
PURPOSES = {"cover", "avatar", "scene", "illustration"}

@dataclass(frozen=True)
class EncodedVariant:
    name: str
    data: bytes
    width: int
    height: int
    mime: str
    sha256: str

def encode(image, name, fmt="WEBP"):
    output = io.BytesIO()
    image.save(output, format=fmt, **({"quality": 90, "method": 4} if fmt == "WEBP" else {"optimize": True}))
    data = output.getvalue()
    if len(data) > MAX_STORED:
        raise Problem("IMAGE_TOO_LARGE", "图片清理后的体积过大，请缩小后上传", 413)
    return EncodedVariant(name, data, image.width, image.height, MIMES[fmt], digest(data))

def decode(data, declared_mime, *, max_bytes=MAX_UPLOAD):
    if not data or len(data) > max_bytes:
        raise Problem("IMAGE_TOO_LARGE", "图片须小于或等于 10 MiB", 413)
    if declared_mime not in MIMES.values():
        raise Problem("IMAGE_TYPE_INVALID", "请选择 JPEG、PNG 或 WebP 图片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format not in MIMES or MIMES[probe.format] != declared_mime:
                    raise Problem("IMAGE_TYPE_INVALID", "图片内容与声明类型不一致")
                width, height = probe.size
                if min(width, height) < 1 or max(width, height) > MAX_EDGE or width * height > MAX_PIXELS:
                    raise Problem("IMAGE_DIMENSIONS_INVALID", "图片最长边须不超过 8192 像素，且不超过 32 百万像素")
                if getattr(probe, "n_frames", 1) != 1:
                    raise Problem("IMAGE_ANIMATED", "请选择静态图片")
                probe.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                turned = ImageOps.exif_transpose(source)
                mode = "RGBA" if "A" in turned.getbands() or "transparency" in turned.info else "RGB"
                # A fresh image excludes EXIF GPS, profiles, comments and text.
                clean = Image.new(mode, turned.size)
                clean.paste(turned.convert(mode))
                return clean
    except Problem:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Problem("IMAGE_INVALID", "图片损坏或无法安全解码")

def prepare(data, declared_mime, purpose, crop):
    if purpose not in PURPOSES:
        raise Problem("IMAGE_PURPOSE_INVALID", "图片用途不支持")
    image = decode(data, declared_mime)
    rect = Crop.model_validate(crop)
    if rect.x + rect.width > image.width or rect.y + rect.height > image.height:
        raise Problem("IMAGE_CROP_INVALID", "裁切范围超出了图片")
    if purpose == "avatar" and rect.width != rect.height:
        raise Problem("IMAGE_CROP_INVALID", "人物头像请使用方形裁切")
    if purpose == "cover" and abs(rect.width / rect.height - 2 / 3) > .01:
        raise Problem("IMAGE_CROP_INVALID", "封面请使用 2:3 裁切")
    image = image.crop((rect.x, rect.y, rect.x + rect.width, rect.y + rect.height))
    values = [encode(image, "original", "PNG")]
    if purpose == "avatar":
        values += [encode(image.resize((edge, edge), Image.Resampling.LANCZOS), "avatar"+str(edge)) for edge in (128, 256, 512)]
    elif purpose == "cover":
        values += [encode(image.resize((width, round(width * image.height / image.width)), Image.Resampling.LANCZOS), "cover"+str(width)) for width in (320, 640)]
    else:
        reading = image.copy()
        reading.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        values.append(encode(reading, "reading"))
        thumb = image.copy()
        thumb.thumbnail((320, 320), Image.Resampling.LANCZOS)
        values.append(encode(thumb, "thumbnail"))
    if any(len(value.data) > MAX_UPLOAD for value in values) or sum(len(value.data) for value in values) > MAX_STORED:
        raise Problem("IMAGE_TOO_LARGE", "裁切后的单张图片须不超过 10 MiB，请缩小图片或裁切范围后上传", 413)
    return values

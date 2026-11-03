import io
import mimetypes
from pathlib import Path
from sqlalchemy import select
from ..config import settings
from ..models import uid, now
from ..storage import write_private, private_path
from ..errors import Problem
from .core import hash_code, safe_owned
from .models import StoredObject

MAX_UPLOAD = 8 * 1024 * 1024
ALLOWED = {".txt", ".md", ".docx", ".epub", ".png", ".jpg", ".jpeg", ".webp"}


def s3_client():
    if not all((settings.product_s3_endpoint, settings.product_s3_access_key, settings.product_s3_secret_key)):
        raise Problem("OBJECT_STORAGE_NOT_CONFIGURED", "对象存储尚未配置", 503)
    try:
        from minio import Minio
    except ImportError:
        raise Problem("OBJECT_STORAGE_DEPENDENCY_MISSING", "请安装 object-storage 可选依赖", 503)
    return Minio(settings.product_s3_endpoint, access_key=settings.product_s3_access_key,
                 secret_key=settings.product_s3_secret_key, secure=settings.product_s3_secure)


def upload(db, owner, name, content, *, purpose="attachment", rights="用户提供 · 权利未核验"):
    name = Path(name.replace("\\", "/")).name[:200]
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED or not content or len(content) > MAX_UPLOAD:
        raise Problem("UPLOAD_INVALID", "支持 TXT、MD、DOCX、EPUB 和常见图片，单文件最多 8 MB")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if extension in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image, UnidentifiedImageError
        try:
            image = Image.open(io.BytesIO(content))
            if image.width * image.height > 20000000:
                raise ValueError()
            image.verify()
            content_type = Image.MIME.get(image.format, content_type)
        except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
            raise Problem("IMAGE_INVALID", "图片无法解析或尺寸过大")
    driver = settings.product_storage_driver
    if driver not in ("local", "s3"):
        raise Problem("OBJECT_STORAGE_NOT_CONFIGURED", "对象存储驱动无效", 503)
    identity = uid()
    key = owner + "/product-" + identity + extension
    if driver == "local":
        write_private(owner, key.split("/")[1], content)
    else:
        # Deployment creates private buckets; this adapter never changes public policy.
        s3_client().put_object(settings.product_s3_bucket, key, io.BytesIO(content), len(content), content_type=content_type)
    import hashlib
    row = StoredObject(id=identity, owner_id=owner, name=name, object_key=key, driver=driver, content_type=content_type,
                       size=len(content), checksum=hashlib.sha256(content).hexdigest(), purpose=purpose, rights=rights[:200])
    db.add(row)
    db.flush()
    return row


def read(row):
    if row.deleted_at:
        raise Problem("RESOURCE_NOT_FOUND", "文件已删除", 404)
    if row.driver == "local":
        try:
            return private_path(row.owner_id, row.object_key.split("/")[1]).read_bytes()
        except FileNotFoundError:
            raise Problem("RESOURCE_NOT_FOUND", "文件不存在", 404)
    response = s3_client().get_object(settings.product_s3_bucket, row.object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove(row):
    if row.driver == "local":
        private_path(row.owner_id, row.object_key.split("/")[1]).unlink(missing_ok=True)
    else:
        s3_client().remove_object(settings.product_s3_bucket, row.object_key)
    row.deleted_at = now()


def remove_account_objects(db, owner):
    for row in db.scalars(select(StoredObject).where(StoredObject.owner_id == owner)):
        if row.driver == "s3":
            remove(row)


def object_result(row):
    return {"id": row.id, "name": row.name, "contentType": row.content_type, "size": row.size,
            "rights": row.rights, "createdAt": row.created_at.isoformat(), "purpose": row.purpose}

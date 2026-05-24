"""Cloudflare R2 private storage backend."""
from flask import current_app

from app.storage.base import EvidenceStorage, StorageError

try:
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - exercised when optional R2 deps are not installed locally.
    ClientError = Exception


class R2Storage(EvidenceStorage):
    provider_name = 'r2'

    def __init__(self, client=None, bucket_name=None):
        self.bucket_name = bucket_name or current_app.config.get('R2_BUCKET_NAME')
        if not self.bucket_name:
            raise StorageError('R2_BUCKET_NAME is required for R2 storage.')
        self.client = client or self._build_client()

    def _build_client(self):
        try:
            import boto3
        except ImportError as exc:
            raise StorageError('boto3 is required for Cloudflare R2 storage.') from exc

        endpoint_url = current_app.config.get('R2_ENDPOINT_URL')
        access_key = current_app.config.get('R2_ACCESS_KEY_ID')
        secret_key = current_app.config.get('R2_SECRET_ACCESS_KEY')
        if not all([endpoint_url, access_key, secret_key]):
            raise StorageError('R2 endpoint and access keys must be configured.')

        return boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name='auto',
        )

    def save_file(self, file_stream, key, content_type=None, metadata=None):
        extra_args = {
            'Metadata': {k: str(v) for k, v in (metadata or {}).items() if v is not None},
        }
        if content_type:
            extra_args['ContentType'] = content_type
        try:
            self.client.upload_fileobj(file_stream, self.bucket_name, key, ExtraArgs=extra_args)
        except ClientError as exc:
            raise StorageError('Could not upload evidence to R2.') from exc
        return key

    def open_file(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return response['Body']
        except ClientError as exc:
            raise FileNotFoundError(key) from exc

    def delete_file(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as exc:
            raise StorageError('Could not delete R2 evidence object.') from exc

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as exc:
            status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            if status == 404:
                return False
            raise StorageError('Could not check R2 evidence object.') from exc

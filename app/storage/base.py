"""Storage interface for private evidence files."""
from abc import ABC, abstractmethod


class StorageError(RuntimeError):
    """Raised when a storage backend cannot complete an operation."""


class EvidenceStorage(ABC):
    """Small interface shared by local development storage and Cloudflare R2."""

    provider_name = 'base'

    @abstractmethod
    def save_file(self, file_stream, key, content_type=None, metadata=None):
        """Persist bytes from a binary stream under a private object key."""

    @abstractmethod
    def open_file(self, key):
        """Return a readable binary stream for a private object key."""

    @abstractmethod
    def delete_file(self, key):
        """Delete a private object key. Missing files are treated as success."""

    @abstractmethod
    def exists(self, key):
        """Return True when a private object key exists."""

"""Network-free tests for the Google Cloud Storage adapter."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import specguard.web.storage as storage_module
from specguard.web.storage import CloudStorage, StoredObject


def test_cloud_storage_uses_one_lazy_client_for_upload_download_and_delete(
    monkeypatch,
) -> None:
    client = MagicMock()
    bucket = client.bucket.return_value
    upload_blob = bucket.blob.return_value
    upload_blob.download_as_bytes.return_value = b"stored bytes"
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(storage_module.storage, "Client", client_factory)
    cloud_storage = CloudStorage(bucket_name="fictional-audit-bucket", project_id="demo-project")

    assert client_factory.call_count == 0

    stored = cloud_storage.upload_bytes("run-1/submittal.pdf", b"pdf bytes", "application/pdf")

    assert stored == StoredObject(
        object_name="run-1/submittal.pdf",
        sha256=hashlib.sha256(b"pdf bytes").hexdigest(),
        content_type="application/pdf",
    )
    client_factory.assert_called_once_with(project="demo-project")
    client.bucket.assert_called_once_with("fictional-audit-bucket")
    bucket.blob.assert_called_once_with("run-1/submittal.pdf")
    upload_blob.upload_from_string.assert_called_once_with(
        b"pdf bytes", content_type="application/pdf"
    )

    assert cloud_storage.download_bytes("run-1/result.pdf") == b"stored bytes"
    cloud_storage.delete_object("run-1/orphan.pdf")

    assert client_factory.call_count == 1
    assert client.bucket.call_count == 3
    assert bucket.blob.call_args_list[1].args == ("run-1/result.pdf",)
    assert bucket.blob.call_args_list[2].args == ("run-1/orphan.pdf",)
    assert upload_blob.download_as_bytes.call_count == 1
    assert upload_blob.delete.call_count == 1

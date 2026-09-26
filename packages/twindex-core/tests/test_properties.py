from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st

from twindex_core import Vault


@settings(max_examples=25)
@given(text=st.text(max_size=300))
def test_source_snapshot_is_idempotent_for_arbitrary_unicode(text: str) -> None:
    with TemporaryDirectory() as directory:
        with Vault.open(Path(directory) / "vault") as vault:
            first = vault.ingest_text(text, uri="file:///note")
            second = vault.ingest_text(text, uri="file:///note")
            assert first.id == second.id
            assert vault.source_text(first.id) == text
            assert len(vault.list_evidence(first.id)) == 1

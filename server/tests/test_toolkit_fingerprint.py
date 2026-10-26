"""Timing-policy changes invalidate host toolkit qualification receipts."""

from pathlib import Path

from youju.onboarding import store


def test_toolkit_fingerprint_includes_shared_position_date_semantics(monkeypatch):
    before = store.toolkit_key()
    policy = Path(store.__file__).parents[1] / "positions" / "deadlines.py"
    read_bytes = Path.read_bytes
    observed = []

    def changed_bytes(path):
        contents = read_bytes(path)
        if path == policy:
            observed.append(path)
            return contents + b"\n# synthetic policy change\n"
        return contents

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    assert store.toolkit_key() != before
    assert observed == [policy]

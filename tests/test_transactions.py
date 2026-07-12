from __future__ import annotations

from pathlib import Path

import pytest

from novel.core.contracts import TransactionJournal, TransactionStatus
from novel.core.io import load_json_model
from novel.core.transactions import (
    FileMutation,
    TransactionError,
    commit_transaction,
    prepare_transaction,
    recover_incomplete_transactions,
)


def test_transaction_commits_all_files(tmp_path: Path) -> None:
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("old-left", encoding="utf-8")
    journal_path, _ = prepare_transaction(
        tmp_path,
        purpose="test commit",
        mutations=[FileMutation(left, b"new-left"), FileMutation(right, b"new-right")],
    )

    journal = commit_transaction(tmp_path, journal_path)

    assert journal.status == TransactionStatus.COMMITTED
    assert left.read_text(encoding="utf-8") == "new-left"
    assert right.read_text(encoding="utf-8") == "new-right"
    assert not (journal_path.parent / "staged").exists()
    assert not (journal_path.parent / "backups").exists()


def test_transaction_rolls_back_every_target_after_injected_failure(tmp_path: Path) -> None:
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("old-left", encoding="utf-8")
    right.write_text("old-right", encoding="utf-8")
    journal_path, _ = prepare_transaction(
        tmp_path,
        purpose="test rollback",
        mutations=[FileMutation(left, b"new-left"), FileMutation(right, b"new-right")],
    )

    with pytest.raises(TransactionError, match="rolled back"):
        commit_transaction(
            tmp_path,
            journal_path,
            fault_injector=lambda index, entry: (_ for _ in ()).throw(OSError("boom")) if index == 1 else None,
        )

    assert left.read_text(encoding="utf-8") == "old-left"
    assert right.read_text(encoding="utf-8") == "old-right"
    journal = load_json_model(journal_path, TransactionJournal)
    assert journal.status == TransactionStatus.ROLLED_BACK
    assert not (journal_path.parent / "staged").exists()
    assert not (journal_path.parent / "backups").exists()


def test_recovery_rolls_back_prepared_transaction(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before", encoding="utf-8")
    prepare_transaction(
        tmp_path,
        purpose="test recovery",
        mutations=[FileMutation(target, b"after")],
    )

    recovered = recover_incomplete_transactions(tmp_path)

    assert len(recovered) == 1
    assert recovered[0].status == TransactionStatus.ROLLED_BACK
    assert target.read_text(encoding="utf-8") == "before"

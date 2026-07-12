from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable
import uuid

from novel.core.artifact_store import resolve_project_path, sha256_bytes, sha256_file
from novel.core.contracts import TransactionEntry, TransactionJournal, TransactionStatus
from novel.core.io import atomic_write_bytes, atomic_write_model_json, load_json_model
from novel.core.timeutil import utc_now


class TransactionError(RuntimeError):
    """Raised when a recoverable workspace transaction cannot commit."""


@dataclass(frozen=True)
class FileMutation:
    target: Path
    content: bytes


FaultInjector = Callable[[int, TransactionEntry], None]


def new_transaction_id() -> str:
    return f"tx_{uuid.uuid4().hex}"


def prepare_transaction(
    root: Path,
    *,
    purpose: str,
    mutations: list[FileMutation],
    transaction_id: str | None = None,
) -> tuple[Path, TransactionJournal]:
    root = root.resolve()
    if not mutations:
        raise TransactionError("transaction requires at least one mutation")
    transaction_id = transaction_id or new_transaction_id()
    transaction_dir = root / "transactions" / transaction_id
    entries: list[TransactionEntry] = []
    seen: set[Path] = set()
    for index, mutation in enumerate(mutations):
        target = mutation.target.resolve()
        try:
            relative_target = target.relative_to(root).as_posix()
        except ValueError as exc:
            raise TransactionError(f"transaction target escapes project root: {target}") from exc
        if target in seen:
            raise TransactionError(f"duplicate transaction target: {relative_target}")
        seen.add(target)
        staged = transaction_dir / "staged" / f"{index:04d}.bin"
        backup = transaction_dir / "backups" / f"{index:04d}.bin"
        atomic_write_bytes(staged, mutation.content)
        existed = target.is_file()
        before_hash: str | None = None
        backup_relative: str | None = None
        if existed:
            original = target.read_bytes()
            before_hash = sha256_bytes(original)
            atomic_write_bytes(backup, original)
            backup_relative = backup.relative_to(root).as_posix()
        entries.append(
            TransactionEntry(
                target_path=relative_target,
                staged_path=staged.relative_to(root).as_posix(),
                backup_path=backup_relative,
                existed=existed,
                before_sha256=before_hash,
                after_sha256=sha256_bytes(mutation.content),
            )
        )
    now = utc_now()
    journal = TransactionJournal(
        transaction_id=transaction_id,
        purpose=purpose,
        status=TransactionStatus.PREPARED,
        entries=entries,
        created_at=now,
        updated_at=now,
    )
    journal_path = transaction_dir / "journal.json"
    atomic_write_model_json(journal_path, journal)
    return journal_path, journal


def commit_transaction(
    root: Path,
    journal_path: Path,
    *,
    fault_injector: FaultInjector | None = None,
) -> TransactionJournal:
    root = root.resolve()
    journal = load_json_model(journal_path, TransactionJournal)
    if journal.status != TransactionStatus.PREPARED:
        raise TransactionError(f"transaction is not prepared: {journal.status.value}")
    journal = _write_status(journal_path, journal, TransactionStatus.APPLYING)
    try:
        for index, entry in enumerate(journal.entries):
            if fault_injector:
                fault_injector(index, entry)
            staged = resolve_project_path(root, entry.staged_path)
            target = resolve_project_path(root, entry.target_path)
            atomic_write_bytes(target, staged.read_bytes())
            if sha256_file(target) != entry.after_sha256:
                raise TransactionError(f"transaction write verification failed: {entry.target_path}")
    except BaseException as exc:
        try:
            rollback_transaction(root, journal_path, error=f"{exc.__class__.__name__}: {exc}")
        except Exception as rollback_exc:
            raise TransactionError(
                f"transaction failed and rollback also failed: {rollback_exc}"
            ) from exc
        raise TransactionError(f"transaction failed and was rolled back: {exc}") from exc
    committed = _write_status(journal_path, journal, TransactionStatus.COMMITTED)
    try:
        _cleanup_transaction_payloads(journal_path)
    except TransactionError as exc:
        committed = _write_status(
            journal_path,
            committed,
            TransactionStatus.COMMITTED,
            error=str(exc),
        )
    return committed


def rollback_transaction(
    root: Path,
    journal_path: Path,
    *,
    error: str | None = None,
) -> TransactionJournal:
    root = root.resolve()
    journal = load_json_model(journal_path, TransactionJournal)
    if journal.status == TransactionStatus.COMMITTED:
        raise TransactionError("committed transaction cannot be rolled back automatically")
    journal = _write_status(journal_path, journal, TransactionStatus.ROLLING_BACK, error=error)
    for entry in reversed(journal.entries):
        target = resolve_project_path(root, entry.target_path)
        if entry.existed:
            if not entry.backup_path:
                raise TransactionError(f"missing transaction backup: {entry.target_path}")
            backup = resolve_project_path(root, entry.backup_path)
            content = backup.read_bytes()
            if entry.before_sha256 and sha256_bytes(content) != entry.before_sha256:
                raise TransactionError(f"transaction backup hash mismatch: {entry.target_path}")
            atomic_write_bytes(target, content)
        else:
            target.unlink(missing_ok=True)
    rolled_back = _write_status(journal_path, journal, TransactionStatus.ROLLED_BACK, error=error)
    try:
        _cleanup_transaction_payloads(journal_path)
    except TransactionError as exc:
        rolled_back = _write_status(
            journal_path,
            rolled_back,
            TransactionStatus.ROLLED_BACK,
            error=str(exc),
        )
    return rolled_back


def recover_incomplete_transactions(root: Path) -> list[TransactionJournal]:
    root = root.resolve()
    recovered: list[TransactionJournal] = []
    transaction_root = root / "transactions"
    if not transaction_root.exists():
        return recovered
    for journal_path in sorted(transaction_root.glob("tx_*/journal.json")):
        journal = load_json_model(journal_path, TransactionJournal)
        if journal.status in {
            TransactionStatus.PREPARED,
            TransactionStatus.APPLYING,
            TransactionStatus.ROLLING_BACK,
        }:
            recovered.append(
                rollback_transaction(root, journal_path, error="recovered incomplete transaction")
            )
        elif journal.status in {TransactionStatus.COMMITTED, TransactionStatus.ROLLED_BACK}:
            try:
                _cleanup_transaction_payloads(journal_path)
            except TransactionError:
                continue
    return recovered


def _cleanup_transaction_payloads(journal_path: Path) -> None:
    for name in ("staged", "backups"):
        payload_dir = journal_path.parent / name
        if not payload_dir.exists():
            continue
        try:
            shutil.rmtree(payload_dir)
        except OSError as exc:
            raise TransactionError(f"transaction payload cleanup failed for {payload_dir}: {exc}") from exc


def _write_status(
    journal_path: Path,
    journal: TransactionJournal,
    status: TransactionStatus,
    *,
    error: str | None = None,
) -> TransactionJournal:
    updated = journal.model_copy(
        update={"status": status, "updated_at": utc_now(), "error": error or journal.error}
    )
    atomic_write_model_json(journal_path, updated)
    return updated

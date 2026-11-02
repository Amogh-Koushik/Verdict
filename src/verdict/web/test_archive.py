import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from verdict.core.models import Problem, ProblemTest

MAX_TESTS = 50
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
TEST_FILE_NAME = re.compile(r"([1-9][0-9]*)\.(in|out)")
_CHUNK_BYTES = 1024 * 1024


class TestArchiveError(ValueError):
    """An uploaded test archive was rejected; the message is shown to the admin."""

    # Stops pytest from treating this exception class as a test class.
    __test__ = False


@dataclass(frozen=True)
class StoredTestHashes:
    idx: int
    input_sha256: str
    output_sha256: str


def unpack_test_archive(upload: BinaryIO, destination: Path) -> list[StoredTestHashes]:
    """Validate the zip, then write {i}.in and {i}.out into the empty folder `destination`."""
    try:
        with ZipFile(upload) as archive:
            count = _validate(archive)
            return [_extract_test(archive, idx, destination) for idx in range(1, count + 1)]
    except BadZipFile as error:
        raise TestArchiveError(f"The file is not a readable zip archive ({error}).") from None


def store_test_archive(db: Session, problem_id: int, upload: BinaryIO, problems_dir: Path) -> int:
    """Store the archive as the problem's next test version and return that version number."""
    # Locking the row makes concurrent uploads for one problem take turns.
    problem = db.scalars(select(Problem).where(Problem.id == problem_id).with_for_update()).one()
    version = problem.tests_version + 1
    problem_dir = problems_dir / str(problem.id)
    problem_dir.mkdir(parents=True, exist_ok=True)
    # Files go to a staging folder that is renamed into place, so a judge never sees half a version.
    staging = Path(tempfile.mkdtemp(prefix=f".v{version}-", dir=problem_dir))
    try:
        hashes = unpack_test_archive(upload, staging)
        final = problem_dir / f"v{version}"
        if final.exists():
            # Left by an upload whose commit failed; no problem_tests row refers to it.
            shutil.rmtree(final)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        db.rollback()
        raise

    db.add_all(
        ProblemTest(
            problem_id=problem.id,
            version=version,
            idx=test.idx,
            input_sha256=test.input_sha256,
            output_sha256=test.output_sha256,
        )
        for test in hashes
    )
    problem.tests_version = version
    db.commit()
    return version


def _validate(archive: ZipFile) -> int:
    # Every name and declared size is checked before a single byte is extracted.
    entries = archive.infolist()
    for entry in entries:
        _reject_unsafe_path(entry.filename)
    names = [entry.filename for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise TestArchiveError(f"The archive lists {', '.join(duplicates)} more than once.")
    count = _count_test_pairs(names)
    _check_sizes(entries)
    for entry in entries:
        if entry.flag_bits & 0x1:
            raise TestArchiveError(f"{entry.filename} is encrypted.")
    return count


def _extract_test(archive: ZipFile, idx: int, destination: Path) -> StoredTestHashes:
    input_sha256 = _copy_entry(archive, f"{idx}.in", destination / f"{idx}.in")
    output_sha256 = _copy_entry(archive, f"{idx}.out", destination / f"{idx}.out")
    return StoredTestHashes(idx, input_sha256, output_sha256)


def _reject_unsafe_path(name: str) -> None:
    parts = name.replace("\\", "/").split("/")
    if name.startswith(("/", "\\")) or ":" in name or ".." in parts:
        raise TestArchiveError(
            f"{name!r} points outside the archive; only plain file names are allowed."
        )


def _count_test_pairs(names: list[str]) -> int:
    inputs: set[int] = set()
    outputs: set[int] = set()
    for name in names:
        match = TEST_FILE_NAME.fullmatch(name)
        if match is None:
            raise TestArchiveError(
                f"{name!r} is not an expected file. The archive must hold only 1.in, 1.out, "
                "2.in, 2.out and so on, with no folders."
            )
        (inputs if match.group(2) == "in" else outputs).add(int(match.group(1)))
    if not inputs and not outputs:
        raise TestArchiveError("The archive holds no tests.")
    unpaired = sorted(inputs ^ outputs)
    if unpaired:
        listed = ", ".join(str(idx) for idx in unpaired)
        raise TestArchiveError(f"Tests {listed} have an .in file without its .out, or the reverse.")
    if len(inputs) > MAX_TESTS:
        raise TestArchiveError(f"The archive holds {len(inputs)} tests; the limit is {MAX_TESTS}.")
    expected = set(range(1, len(inputs) + 1))
    if inputs != expected:
        missing = ", ".join(str(idx) for idx in sorted(expected - inputs))
        raise TestArchiveError(f"Tests must be numbered from 1 with no gaps; missing {missing}.")
    return len(inputs)


def _check_sizes(entries: list[ZipInfo]) -> None:
    for entry in entries:
        if entry.file_size > MAX_FILE_BYTES:
            raise TestArchiveError(f"{entry.filename} is larger than {_megabytes(MAX_FILE_BYTES)}.")
    total = sum(entry.file_size for entry in entries)
    if total > MAX_TOTAL_BYTES:
        raise TestArchiveError(
            f"The tests add up to {_megabytes(total)}; the limit is {_megabytes(MAX_TOTAL_BYTES)}."
        )


def _copy_entry(archive: ZipFile, name: str, target: Path) -> str:
    # zipfile stops at the declared size and checks the CRC, so a lying size header fails here.
    digest = hashlib.sha256()
    with archive.open(name) as source, target.open("wb") as sink:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
            sink.write(chunk)
    return digest.hexdigest()


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"

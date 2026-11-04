import hashlib
import io
import struct
import zipfile
from pathlib import Path

import pytest

from verdict.web.test_archive import (
    MAX_FILE_BYTES,
    MAX_TESTS,
    TestArchiveError,
    unpack_test_archive,
)


def make_zip(entries: list[tuple[str, bytes]], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Build a zip holding exactly these names, without the cleaning ZipFile.write applies."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=method)
    return buffer.getvalue()


def numbered_pairs(count: int, size: int = 4) -> list[tuple[str, bytes]]:
    entries = []
    for idx in range(1, count + 1):
        entries.append((f"{idx}.in", b"i" * size))
        entries.append((f"{idx}.out", b"o" * size))
    return entries


def unpack(data: bytes, destination: Path) -> list[object]:
    return list(unpack_test_archive(io.BytesIO(data), destination))


def test_valid_archive_is_written_out_with_hashes(tmp_path: Path) -> None:
    data = make_zip([("2.out", b"5\n"), ("1.in", b"1 2\n"), ("2.in", b"2 3\n"), ("1.out", b"3\n")])
    hashes = unpack_test_archive(io.BytesIO(data), tmp_path)
    assert [test.idx for test in hashes] == [1, 2]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["1.in", "1.out", "2.in", "2.out"]
    assert (tmp_path / "2.in").read_bytes() == b"2 3\n"
    assert hashes[0].input_sha256 == hashlib.sha256(b"1 2\n").hexdigest()
    assert hashes[1].output_sha256 == hashlib.sha256(b"5\n").hexdigest()


def test_archive_with_the_maximum_number_of_tests_is_accepted(tmp_path: Path) -> None:
    assert len(unpack(make_zip(numbered_pairs(MAX_TESTS)), tmp_path)) == MAX_TESTS


def test_file_of_exactly_the_size_limit_is_accepted(tmp_path: Path) -> None:
    data = make_zip([("1.in", b"\0" * MAX_FILE_BYTES), ("1.out", b"0")])
    assert len(unpack(data, tmp_path)) == 1


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        pytest.param([("/1.in", b"1"), ("1.out", b"1")], "points outside", id="absolute path"),
        pytest.param([("../1.in", b"1"), ("1.out", b"1")], "points outside", id="zip slip"),
        pytest.param(
            [("tests/../../etc/1.in", b"1"), ("1.out", b"1")], "points outside", id="deep zip slip"
        ),
        pytest.param(
            [("..\\1.in", b"1"), ("1.out", b"1")], "points outside", id="zip slip with backslash"
        ),
        pytest.param([("C:/1.in", b"1"), ("1.out", b"1")], "points outside", id="drive letter"),
        pytest.param(
            [("tests/1.in", b"1"), ("tests/1.out", b"1")], "not an expected file", id="folder"
        ),
        pytest.param(
            [("tests/", b""), *numbered_pairs(1)], "not an expected file", id="folder entry"
        ),
        pytest.param(
            [*numbered_pairs(1), ("README.txt", b"hi")], "not an expected file", id="extra"
        ),
        pytest.param([("01.in", b"1"), ("01.out", b"1")], "not an expected file", id="zero pad"),
        pytest.param([("0.in", b"1"), ("0.out", b"1")], "not an expected file", id="test zero"),
        pytest.param([("1.in", b"1"), ("1.ans", b"1")], "not an expected file", id="extension"),
        pytest.param([("1.IN", b"1"), ("1.OUT", b"1")], "not an expected file", id="upper case"),
        pytest.param([], "no tests", id="empty archive"),
        pytest.param(
            [*numbered_pairs(1), ("2.in", b"1")], "Tests 2 have", id="input without output"
        ),
        pytest.param(
            [*numbered_pairs(1), ("2.out", b"1")], "Tests 2 have", id="output without input"
        ),
        pytest.param(
            [*numbered_pairs(1), ("3.in", b"1"), ("3.out", b"1")],
            "missing 2",
            id="gap in numbering",
        ),
        pytest.param([("2.in", b"1"), ("2.out", b"1")], "missing 1", id="not starting at 1"),
        pytest.param(numbered_pairs(MAX_TESTS + 1), "the limit is 50", id="too many tests"),
        pytest.param(
            [("1.in", b"\0" * (MAX_FILE_BYTES + 1)), ("1.out", b"0")],
            "larger than 10.0 MB",
            id="file over 10 MB",
        ),
        pytest.param(
            numbered_pairs(6, size=9 * 1024 * 1024), "the limit is 50.0 MB", id="total over 50 MB"
        ),
    ],
)
def test_an_archive_breaking_a_rule_is_rejected_and_nothing_is_extracted(
    tmp_path: Path, entries: list[tuple[str, bytes]], message: str
) -> None:
    with pytest.raises(TestArchiveError, match=message):
        unpack(make_zip(entries), tmp_path)
    assert list(tmp_path.iterdir()) == [], "nothing may be extracted from a rejected archive"


def test_duplicate_names_are_rejected(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = make_zip([*numbered_pairs(1), ("1.in", b"other")])
    with pytest.raises(TestArchiveError, match="more than once"):
        unpack(data, tmp_path)


def test_encrypted_entry_is_rejected(tmp_path: Path) -> None:
    data = bytearray(make_zip(numbered_pairs(1)))
    # ZipFile clears flag_bits when writing, so set 1.in's "encrypted" bit in the bytes instead:
    # in its local header and in its central directory entry.
    data[6] |= 0x1
    data[data.index(b"PK\x01\x02") + 8] |= 0x1
    with pytest.raises(TestArchiveError, match=r"1\.in is encrypted"):
        unpack(bytes(data), tmp_path)


def test_file_that_is_not_a_zip_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(TestArchiveError, match="not a readable zip archive"):
        unpack(b"1.in and 1.out, honest", tmp_path)


def test_entry_whose_size_header_understates_its_data_is_rejected(tmp_path: Path) -> None:
    data = bytearray(make_zip([("1.in", b"A" * 100), ("1.out", b"B")], zipfile.ZIP_STORED))
    # Rewrite 1.in's uncompressed size from 100 to 10 in its local header and central directory.
    struct.pack_into("<I", data, 22, 10)
    struct.pack_into("<I", data, data.index(b"PK\x01\x02") + 24, 10)
    with pytest.raises(TestArchiveError, match="Bad CRC-32"):
        unpack(bytes(data), tmp_path)

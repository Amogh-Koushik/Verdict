import pytest

from verdict.judge.checker import outputs_match


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (b"3\n", b"3\n"),
        (b"3\n", b"3"),
        (b"3", b"3\n\n\n"),
        (b"1 2 3\n", b"1  2\t3\n"),
        (b"1 2 3\n", b"1\n2\n3\n"),
        (b"1 2\n3 4\n", b"1 2\r\n3 4\r\n"),
        (b"  leading\n", b"leading"),
        (b"", b""),
        (b"", b"\n \t\n"),
        (b"\xe2\x9c\x93 ok\n", b"\xe2\x9c\x93 ok"),
    ],
)
def test_outputs_that_differ_only_in_whitespace_match(expected: bytes, actual: bytes) -> None:
    assert outputs_match(expected, actual)


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (b"3\n", b"4\n"),
        (b"3\n", b""),
        (b"", b"3"),
        (b"1 2 3\n", b"1 2\n"),
        (b"1 2\n", b"1 2 3\n"),
        (b"12\n", b"1 2\n"),
        (b"1 2\n", b"12\n"),
        (b"yes\n", b"YES\n"),
        (b"1.0\n", b"1\n"),
        (b"2 1\n", b"1 2\n"),
    ],
)
def test_outputs_with_different_tokens_do_not_match(expected: bytes, actual: bytes) -> None:
    assert not outputs_match(expected, actual)

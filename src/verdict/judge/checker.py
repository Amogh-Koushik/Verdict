def outputs_match(expected: bytes, actual: bytes) -> bool:
    """True if both outputs hold the same tokens, ignoring how whitespace separates them."""
    return expected.split() == actual.split()

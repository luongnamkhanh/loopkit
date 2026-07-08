def parse_int_list(s: str) -> list[int]:
    result = []
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue  # ponytail: skip blanks so "" and " , " -> []
        try:
            result.append(int(token))
        except ValueError:
            raise ValueError(f"invalid integer token: {token!r}") from None
    return result


if __name__ == "__main__":
    assert parse_int_list("") == []
    assert parse_int_list("   ") == []
    assert parse_int_list(" 1, 2 ,3 ") == [1, 2, 3]
    assert parse_int_list("-5, +7") == [-5, 7]
    try:
        parse_int_list("1, x, 3")
        assert False
    except ValueError as e:
        assert "'x'" in str(e)
    print("ok")

import pytest
from solution import parse_int_list
def test_basic(): assert parse_int_list("1, 2,3") == [1, 2, 3]
def test_empty(): assert parse_int_list("  ") == []
def test_bad():
    with pytest.raises(ValueError):
        parse_int_list("1,x,3")

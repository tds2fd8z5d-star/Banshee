from bot.utils import clean_name, clean_phone, parse_positive_int


def test_phone():
    assert clean_phone("8 (900) 123-45-67") == "+79001234567"
    assert clean_phone("+7 900 123 45 67") == "+79001234567"
    assert clean_phone("9001234567") == "+79001234567"
    assert clean_phone("79001234567") == "+79001234567"
    assert clean_phone("123") is None
    assert clean_phone("abc") is None


def test_name():
    assert clean_name("  Анна   Мария ") == "Анна Мария"
    assert clean_name("А") is None
    assert clean_name("/start") is None
    assert clean_name("12345") is None


def test_int():
    assert parse_positive_int("1 500 ₽") == 1500
    assert parse_positive_int("0") is None
    assert parse_positive_int("abc") is None

from grokreg.mail.tempmail import TempMailClient


def test_extract_code_simple():
    assert TempMailClient.extract_code("Your code is 123456") == "123456"


def test_extract_code_cn():
    assert TempMailClient.extract_code("验证码：654321 请勿泄露") == "654321"


def test_extract_code_xai_subject():
    assert TempMailClient.extract_code("", "ABC-DEF xAI") == "ABC-DEF"
    assert TempMailClient.extract_code("hello", "XY9-K2M welcome") == "XY9-K2M"


def test_extract_code_xai_body():
    assert TempMailClient.extract_code("Your verification code is AB1-CD2") == "AB1-CD2"


def test_extract_code_none():
    assert TempMailClient.extract_code("hello world") is None

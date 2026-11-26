from fksc.otp import extract_otp
from fksc.parser import parse_message, should_buy

F = {"require_100_percent": True, "max_price": 2000, "exclude_keywords": ["expired"]}


def test_basic_100_percent():
    d = parse_message("🔥 100% SuperCoins Deal\nBoAt Earbuds\nMRP ₹2,999  Deal ₹899\nhttps://www.flipkart.com/boat-x/p/itm123?pid=ACCGHJ7XZ9KQ2ABC&affid=x")
    assert d and d.is_100 and d.price == 899
    assert d.key == "ACCGHJ7XZ9KQ2ABC"
    assert should_buy(d, F) == (True, "ok")


def test_shortlink_and_sc_wording():
    d = parse_message("Loot!! 100 % SC on this. Rs. 499\nhttps://fkrt.it/abcDEF!!NNNN.")
    assert d.is_100 and d.url == "https://fkrt.it/abcDEF!!NNNN"


def test_coinback_wording():
    assert parse_message("Full coinback 😍 https://dl.flipkart.com/s/xyz").is_100
    assert parse_message("SuperCoins: 100% https://dl.flipkart.com/s/xyz").is_100


def test_not_100():
    d = parse_message("50% SuperCoins https://www.flipkart.com/p/itm1")
    assert not d.is_100 and should_buy(d, F)[0] is False


def test_price_cap_and_exclude():
    assert should_buy(parse_message("100% SC ₹5000 https://www.flipkart.com/p/itm1"), F)[0] is False
    assert should_buy(parse_message("100% SC expired https://www.flipkart.com/p/itm1"), F)[0] is False


def test_hidden_link_entity():
    d = parse_message("100% SuperCoins — Buy here", ["https://www.flipkart.com/p/itm9?pid=ABCDEFGHIJ12"])
    assert d and d.key == "ABCDEFGHIJ12"


def test_no_link():
    assert parse_message("100% SuperCoins coming in 5 mins!") is None


def test_otp():
    assert extract_otp("123456 is your OTP for txn of INR 899.00 at Flipkart") == "123456"
    assert extract_otp("Your OTP for transaction at FLIPKART is 4821. Do not share") == "4821"

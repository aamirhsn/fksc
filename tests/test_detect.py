from fksc.detect import detect, product_key
from fksc.otp import extract_otp

U = "https://www.flipkart.com/boat-x/p/itm123abc?pid=ACCGHJ7XZ9KQ2ABC&lid=x"


def test_card_earn_coins_full():
    o = detect(U, "boAt Airdopes 141 Earbuds\n4.1\n₹899\n₹4,490\n79% off\nEarn 899 SuperCoins")
    assert o.price == 899 and o.coins == 899 and o.ratio == 1.0
    assert o.key == "ACCGHJ7XZ9KQ2ABC"


def test_percent_wording():
    assert detect(U, "Some Product Name Here\n₹499\n100% SuperCoins back").ratio == 1.0
    assert detect(U, "Some Product Name Here\n₹499\n50% coin back").ratio == 0.5


def test_half():
    o = detect(U, "Some Product Name Here\n₹1,000\n₹2,000\nGet 500 SuperCoins back")
    assert o.price == 1000 and o.ratio == 0.5


def test_spending_coins_is_not_coinback():
    assert detect(U, "Some Product Name Here\n₹999\nGet ₹50 off using 50 SuperCoins") is None
    assert detect(U, "Some Product Name Here\n₹999\nRedeem 100 SuperCoins") is None


def test_no_coins():
    assert detect(U, "Product\n₹499\nBank offer 10% off") is None


def test_key_without_pid():
    assert product_key("https://www.flipkart.com/x/p/itmabc123?foo=1") == "itmabc123"


def test_otp():
    assert extract_otp("123456 is your OTP for txn of INR 899.00 at Flipkart") == "123456"
    assert extract_otp("Your OTP for transaction at FLIPKART is 4821. Do not share") == "4821"

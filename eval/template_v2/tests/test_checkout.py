import unittest

from shop.checkout import checkout


class CheckoutTest(unittest.TestCase):
    def test_charge(self):
        self.assertEqual(checkout({"total_cents": 500}, "tok_4242")["status"], "charged")


if __name__ == "__main__":
    unittest.main()

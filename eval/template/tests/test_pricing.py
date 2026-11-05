import unittest

from shop.pricing import apply_coupon


class PricingTest(unittest.TestCase):
    def test_half(self):
        self.assertEqual(apply_coupon(1000, "HALF"), 500)


if __name__ == "__main__":
    unittest.main()

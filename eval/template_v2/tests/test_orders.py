import unittest

from shop.orders import create_order


class OrdersTest(unittest.TestCase):
    def test_total(self):
        order = create_order([{"price_cents": 250, "qty": 2}], {"id": "cus_1", "email": "a@example.com"})
        self.assertEqual(order["total_cents"], 500)
        self.assertTrue(order["id"].startswith("ord_"))


if __name__ == "__main__":
    unittest.main()

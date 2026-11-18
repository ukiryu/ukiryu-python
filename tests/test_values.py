"""The value codec, without the library."""

import datetime as dt
import unittest

from ukiryu.values import Map, Regexp, Symbol, Time, WireValueError, decode, encode, float_to_s


class FloatToS(unittest.TestCase):
    def test_matches_ruby_float_to_s(self):
        # Ruby 4.0.1: p [0.0, -0.0, 1.0, 100.0, 1e15, 1e16, 0.0001, 0.00001, 1.5e300,
        #                123.456, -2.5e-10, 0.1, 1e20, 1.0/3, Float::INFINITY].map(&:to_s)
        cases = [
            (0.0, "0.0"), (-0.0, "-0.0"), (1.0, "1.0"), (100.0, "100.0"),
            (1e15, "1000000000000000.0"), (1e16, "1.0e+16"), (0.0001, "0.0001"),
            (0.00001, "1.0e-05"), (1.5e300, "1.5e+300"), (123.456, "123.456"),
            (-2.5e-10, "-2.5e-10"), (0.1, "0.1"), (1e20, "1.0e+20"),
            (1.0 / 3, "0.3333333333333333"), (float("inf"), "Infinity"),
            (float("-inf"), "-Infinity"), (float("nan"), "NaN"),
        ]
        for value, text in cases:
            self.assertEqual(float_to_s(value), text, repr(value))


class Codec(unittest.TestCase):
    def test_scalars_keep_their_types(self):
        self.assertEqual(encode(True), True)
        self.assertIs(type(encode(1)), int)
        self.assertEqual(encode(10 ** 40), 10 ** 40)
        self.assertEqual(encode(1.0), {"$float": "1.0"})
        self.assertEqual(encode(b"\xff\x00"), {"$bytes": "ff00"})
        self.assertEqual(encode(Symbol("a")), {"$sym": "a"})
        self.assertEqual(encode(Regexp("a+")), {"$regexp": "a+"})
        self.assertEqual(encode(Regexp("a+", 1)), {"$regexp": {"source": "a+", "options": 1}})

    def test_hash_keys_choose_object_or_map(self):
        self.assertEqual(encode({"a": 1}), {"a": 1})
        self.assertEqual(encode({Symbol("a"): 1, "b": 2}),
                         {"$map": [[{"$sym": "a"}, 1], ["b", 2]]})
        self.assertEqual(decode({"$map": [[1, 2], [[1], {"$sym": "x"}]]}),
                         Map(((1, 2), ([1], Symbol("x")))))

    def test_round_trip(self):
        values = [
            None, False, 0, -(10 ** 30), "text", 2.5, b"\x80", Symbol("s"), Regexp("x", 4),
            Time(-1, 999_999_999), Time(1705486830, 1, 19800, False), [1, [2.0]],
            {"a": [Symbol("b")]}, Map(((Symbol("k"), 1), (2, None))),
        ]
        for value in values:
            self.assertEqual(decode(encode(value)), value)

    def test_utc_text_time(self):
        self.assertEqual(decode({"$time": "2024-01-17T10:20:30.123456789Z"}),
                         Time(1705486830, 123456789))
        self.assertEqual(decode({"$time": "1969-12-31T23:59:59.999999999Z"}),
                         Time(-1, 999999999))
        self.assertEqual(decode({"$time": "0000-03-01T00:00:00.000000000Z"}),
                         Time(-62162035200, 0))

    def test_from_datetime_is_exact(self):
        value = dt.datetime(2024, 1, 17, 10, 20, 30, 123456, tzinfo=dt.timezone.utc)
        self.assertEqual(Time.from_datetime(value), Time(1705486830, 123456000, 0, True))
        with self.assertRaises(ValueError):
            Time.from_datetime(dt.datetime(2024, 1, 1))

    def test_lossy_or_unknown_values_are_rejected(self):
        with self.assertRaises(WireValueError):
            decode(1.5)
        with self.assertRaises(WireValueError):
            encode(object())
        with self.assertRaises(WireValueError):
            decode({"$time": "2024-01-17"})


if __name__ == "__main__":
    unittest.main()

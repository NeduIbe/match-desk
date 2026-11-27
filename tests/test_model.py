"""Self-checks for Naija Match Desk. They use made-up numbers only and need no internet.
Run:  python3 -m unittest discover -s tests -v     (from the main folder)"""
import math, os, random, sys, unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import update as U


def fake_games(venue_sd, seed, teams=16, rounds=3):
    """Pretend league. venue_sd = how different each team is at home and away (0 = not different)."""
    r = random.Random(seed)
    T = ['T%d' % i for i in range(teams)]
    att = {t: r.gauss(0, .25) for t in T}
    de = {t: r.gauss(0, .25) for t in T}
    ha = {t: r.gauss(0, venue_sd) for t in T}
    hd = {t: r.gauss(0, venue_sd) for t in T}
    ms, d = [], datetime(2025, 8, 1)

    def pois(l):
        L, k, p = math.exp(-l), 0, 1.0
        while True:
            p *= r.random()
            if p <= L:
                return k
            k += 1
    for _ in range(rounds):
        for h in T:
            for a in T:
                if h != a:
                    d += timedelta(hours=9)
                    ms.append(dict(h=h, a=a, hg=pois(1.5 * math.exp(att[h] + ha[h] + de[a])), ag=pois(1.15 * math.exp(att[a] + de[h] + hd[h])),
                                   hhg=None, hag=None, hc=None, ac=None, hy=None, ay=None, hr=None, ar=None, hst=None, ast=None, d=d, ref=None))
    return ms


class Maths(unittest.TestCase):
    def test_poisson_adds_to_one(self):
        self.assertAlmostEqual(sum(U.pois_pmf(1.7, 30)), 1.0, places=6)

    def test_shrink_stays_between(self):
        v = U.shrink(3.0, 4, 1.0, 5.0)
        self.assertTrue(1.0 < v < 3.0)
        self.assertEqual(U.shrink(2.0, 0, 1.0, 5.0), 1.0)

    def test_est_k_is_limited(self):
        r = random.Random(1)
        groups = {'t%d' % i: [r.gauss(1.4, 1.0) for _ in range(12)] for i in range(12)}
        k = U.est_k(groups)
        self.assertTrue(k is None or 1.0 <= k <= 50.0)
        self.assertIsNone(U.est_k({'a': [1, 2, 3, 4]}))  # too few teams: say nothing


class Predictions(unittest.TestCase):
    def setUp(self):
        self.ms = fake_games(0.0, 5)
        gh = sum(m['hg'] for m in self.ms) / len(self.ms)
        ga = sum(m['ag'] for m in self.ms) / len(self.ms)
        self.lg = {'n': len(self.ms), 'gh': gh, 'ga': ga, 'g_team': (gh + ga) / 2, 'h1share': 0.44, 'k': {}}
        self.prof = lambda t: U.profile(sorted([m for m in self.ms if t in (m['h'], m['a'])], key=lambda m: m['d'], reverse=True)[:16], t)

    def test_chances_add_to_one_and_are_sane(self):
        p = U.predict(self.prof('T1'), self.prof('T2'), self.lg)
        self.assertAlmostEqual(sum(p['p1x2']), 1.0, places=3)
        self.assertTrue(all(0 < x < 1 for x in p['p1x2']))
        self.assertTrue(p['goals']['o15'] > p['goals']['o25'] > p['goals']['o35'])

    def test_no_prediction_without_data(self):
        self.assertIsNone(U.predict(None, self.prof('T2'), self.lg))
        self.assertIsNone(U.predict(self.prof('T1'), self.prof('T2'), {'n': 5}))

    def test_elo_model_uses_real_goal_average(self):
        m = U.elo_model(1800, 1600, 2.7, 60)
        self.assertTrue(m['p1x2'][0] > m['p1x2'][2])          # the stronger team is favoured
        self.assertAlmostEqual(m['exp_total'], 2.7, delta=0.15)  # total goals stays the league average


class HonestTests(unittest.TestCase):
    def test_home_away_split_not_adopted_when_no_real_difference(self):
        r = U.split_backtest(fake_games(0.0, 11), {})
        self.assertTrue(r is None or r['used'] is False)

    def test_home_away_split_adopted_when_real_difference(self):
        r = U.split_backtest(fake_games(0.5, 11, 20, 3), {})
        self.assertTrue(r is not None and r['used'] is True)

    def test_window_test_returns_a_known_length(self):
        r = U.window_backtest(fake_games(0.0, 3))
        self.assertTrue(r is None or r['best'] in U.WIN_GRID)

    def test_too_little_data_means_no_test(self):
        self.assertIsNone(U.split_backtest(fake_games(0.0, 2)[:60], {}))
        self.assertIsNone(U.window_backtest(fake_games(0.0, 2)[:60]))


class Page(unittest.TestCase):
    def test_page_has_what_it_needs(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        h = open(os.path.join(root, 'index.html'), encoding='utf8').read()
        for needle in ('id="trackPane"', 'id="bbSec"', 'naija-track-v1', 'Save backup file', 'Does the checklist help?'):
            self.assertIn(needle, h)


if __name__ == '__main__':
    unittest.main()

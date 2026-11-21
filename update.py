#!/usr/bin/env python3
"""Naija Match Desk: daily data updater. Uses only the Python standard library.

Sources
  football-data.co.uk   Free CSV files, no key needed. Results, match stats (corners, cards,
                        shots), half-time scores, referees and bookmaker odds for the
                        Premier League, La Liga, Serie A, Bundesliga and Ligue 1.
  ESPN (keyless)        Upcoming fixtures for the five leagues, the Champions League and the NPFL,
                        plus the match ids the page uses for live lineups and events.
  api-football.com      OPTIONAL. Free key in the secret API_FOOTBALL_KEY. Adds the NPFL.

Run:   python3 update.py
Output: data/fixtures.json (the page reads this file)
"""
import csv
import difflib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo('Europe/London')
except Exception:  # pragma: no cover
    LONDON = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(ROOT, 'data'))
LOCAL = os.environ.get('LOCAL_SOURCE_DIR')          # used only for offline testing
NOW = datetime.fromisoformat(os.environ['FAKE_NOW']) if os.environ.get('FAKE_NOW') else datetime.now(timezone.utc)

LEAGUES = {
    'E0': {'name': 'Premier League', 'short': 'EPL', 'org': 'PL'},
    'SP1': {'name': 'La Liga', 'short': 'La Liga', 'org': 'PD'},
    'I1': {'name': 'Serie A', 'short': 'Serie A', 'org': 'SA'},
    'D1': {'name': 'Bundesliga', 'short': 'Bundesliga', 'org': 'BL1'},
    'F1': {'name': 'Ligue 1', 'short': 'Ligue 1', 'org': 'FL1'},
}
_y = NOW.year if NOW.month >= 7 else NOW.year - 1
CUR = '%02d%02d' % (_y % 100, (_y + 1) % 100)
PREV = '%02d%02d' % ((_y - 1) % 100, _y % 100)
BASE = 'https://football-data.co.uk/mmz4281/{s}/{f}.csv'
FIX_URL = 'https://football-data.co.uk/fixtures.csv'
WINDOW = 16          # matches per team used for rates
STATUS = {}
NOTES = []

DISPLAY = {
    'Man United': 'Manchester United', 'Man City': 'Manchester City', "Nott'm Forest": 'Nottingham Forest',
    'Wolves': 'Wolverhampton', 'Ath Madrid': 'Atletico Madrid', 'Ath Bilbao': 'Athletic Bilbao',
    'Sociedad': 'Real Sociedad', 'Vallecano': 'Rayo Vallecano', 'Espanol': 'Espanyol', 'Betis': 'Real Betis',
    'Celta': 'Celta Vigo', "M'gladbach": 'Gladbach', 'Ein Frankfurt': 'Eintracht Frankfurt',
    'Paris SG': 'Paris Saint-Germain', 'Milan': 'AC Milan', 'Inter': 'Inter Milan', 'FC Koln': 'FC Koln',
    'La Coruna': 'Deportivo La Coruna', 'Leverkusen': 'Bayer Leverkusen', 'Dortmund': 'Borussia Dortmund',
}

ALIASES = {
    'manchester united': 'Man United', 'manchester city': 'Man City', 'nottingham forest': "Nott'm Forest",
    'wolverhampton wanderers': 'Wolves', 'tottenham hotspur': 'Tottenham', 'newcastle united': 'Newcastle',
    'brighton hove albion': 'Brighton', 'west ham united': 'West Ham', 'leeds united': 'Leeds',
    'bournemouth': 'Bournemouth', 'leicester city': 'Leicester', 'ipswich town': 'Ipswich', 'hull city': 'Hull',
    'coventry city': 'Coventry', 'athletic club': 'Ath Bilbao', 'athletic bilbao': 'Ath Bilbao',
    'atletico madrid': 'Ath Madrid', 'club atletico de madrid': 'Ath Madrid', 'real betis': 'Betis',
    'real betis balompie': 'Betis', 'celta vigo': 'Celta', 'celta de vigo': 'Celta', 'espanyol': 'Espanol',
    'real sociedad': 'Sociedad', 'rayo vallecano': 'Vallecano', 'deportivo alaves': 'Alaves',
    'deportivo la coruna': 'La Coruna', 'rc deportivo': 'La Coruna', 'inter milan': 'Inter',
    'internazionale': 'Inter', 'internazionale milano': 'Inter', 'milan': 'Milan', 'roma': 'Roma',
    'napoli': 'Napoli', 'hellas verona': 'Verona', 'bayern munich': 'Bayern Munich',
    'bayern munchen': 'Bayern Munich', 'borussia dortmund': 'Dortmund', 'borussia monchengladbach': "M'gladbach",
    'monchengladbach': "M'gladbach", 'eintracht frankfurt': 'Ein Frankfurt', 'bayer leverkusen': 'Leverkusen',
    'bayer 04 leverkusen': 'Leverkusen', 'union berlin': 'Union Berlin', 'werder bremen': 'Werder Bremen',
    'hamburger': 'Hamburg', 'hamburg': 'Hamburg', 'st pauli': 'St Pauli', 'paris saint germain': 'Paris SG',
    'olympique lyonnais': 'Lyon', 'olympique lyon': 'Lyon', 'olympique de marseille': 'Marseille',
    'olympique marseille': 'Marseille', 'as monaco': 'Monaco', 'monaco': 'Monaco', 'lille': 'Lille',
    'stade rennais': 'Rennes', 'stade brestois 29': 'Brest', 'stade brest': 'Brest',
    'strasbourg alsace': 'Strasbourg', 'estac troyes': 'Troyes', 'paris': 'Paris FC',
}
STOP = {'fc', 'afc', 'cf', 'sc', 'sv', 'ac', 'as', 'ssc', 'us', 'rc', 'rcd', 'ogc', 'losc', 'fsv', 'vfb', 'tsg',
        'sco', 'ca', 'cd', 'ud', 'fk', 'sd', 'bv', 'the', 'de', 'club', 'calcio', '1907', 'and', 'ssd'}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- fetching
def fetch(url, headers=None, tries=3, timeout=45):
    if LOCAL:
        m = re.search(r'/(\d{4})/(\w+)\.csv$', url)
        e = re.search(r'/soccer/([\w.]+)/(scoreboard|summary)', url)
        name = '%s_%s.csv' % (m.group(1), m.group(2)) if m else url.rsplit('/', 1)[-1].split('?')[0]
        if e:
            name = 'espn_%s_%s.json' % (e.group(1), e.group(2))
        path = os.path.join(LOCAL, name)
        if not os.path.exists(path):
            raise FileNotFoundError(name)
        with open(path, 'rb') as f:
            return f.read()
    last = None
    for i in range(tries):
        try:
            req = Request(url, headers=dict({'User-Agent': 'Mozilla/5.0 (match-desk updater)'}, **(headers or {})))
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network, 404, rate limit
            last = e
            time.sleep(3 * (i + 1))
    raise last


def read_rows(raw):
    text = raw.decode('utf-8-sig', errors='replace')
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): (v or '').strip() for k, v in r.items() if k is not None and isinstance(v, (str, type(None)))}
        if r.get('HomeTeam') and r.get('AwayTeam') and r.get('Date'):
            out.append(r)
    return out


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def whole(x):
    v = num(x)
    return None if v is None else int(v)


def to_utc(date_s, time_s):
    d = None
    for f in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            d = datetime.strptime(date_s, f)
            break
        except ValueError:
            pass
    if d is None:
        return None, False
    known = bool(time_s and re.match(r'^\d{1,2}:\d{2}', time_s))
    hh, mm = (int(time_s.split(':')[0]), int(time_s.split(':')[1][:2])) if known else (12, 0)
    local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=LONDON or timezone.utc)
    return local.astimezone(timezone.utc), known


def to_match(r):
    hg, ag = whole(r.get('FTHG')), whole(r.get('FTAG'))
    if hg is None or ag is None:
        return None
    d, _ = to_utc(r['Date'], r.get('Time'))
    if d is None:
        return None
    return {'d': d, 'h': r['HomeTeam'], 'a': r['AwayTeam'], 'hg': hg, 'ag': ag,
            'hhg': whole(r.get('HTHG')), 'hag': whole(r.get('HTAG')),
            'hc': whole(r.get('HC')), 'ac': whole(r.get('AC')),
            'hy': whole(r.get('HY')), 'ay': whole(r.get('AY')), 'hr': whole(r.get('HR')), 'ar': whole(r.get('AR')),
            'hst': whole(r.get('HST')), 'ast': whole(r.get('AST')), 'ref': r.get('Referee') or None}


# ---------------------------------------------------------------- statistics
def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def cards_of(m, side):
    y, r = (m['hy'], m['hr']) if side == 'h' else (m['ay'], m['ar'])
    return None if y is None else y + (r or 0)


def total_cards(m):
    a, b = cards_of(m, 'h'), cards_of(m, 'a')
    return None if a is None or b is None else a + b


def total_corners(m):
    return None if m['hc'] is None or m['ac'] is None else m['hc'] + m['ac']


def league_avgs(ms):
    n = len(ms)
    if n == 0:
        return {'n': 0}
    gh, ga = mean([m['hg'] for m in ms]), mean([m['ag'] for m in ms])
    ht = [(m['hhg'] + m['hag'], m['hg'] + m['ag']) for m in ms if m['hhg'] is not None and m['hag'] is not None]
    share = (sum(a for a, _ in ht) / max(1, sum(b for _, b in ht))) if ht else 0.44
    return {'n': n, 'gh': gh, 'ga': ga, 'g_team': (gh + ga) / 2, 'h1share': share,
            'tc': mean([total_corners(m) for m in ms]), 'tk': mean([total_cards(m) for m in ms]),
            'o25': mean([1.0 if m['hg'] + m['ag'] >= 3 else 0.0 for m in ms]),
            'btts': mean([1.0 if m['hg'] > 0 and m['ag'] > 0 else 0.0 for m in ms])}


def profile(ms, name):
    """ms: that team's matches, newest first, already cut to the window."""
    rows = []
    for m in ms:
        home = m['h'] == name
        gf, ga = (m['hg'], m['ag']) if home else (m['ag'], m['hg'])
        side, opp = ('h', 'a') if home else ('a', 'h')
        rows.append({
            'gf': gf, 'ga': ga, 'tot': gf + ga,
            'h1': (m['hhg'] + m['hag']) if m['hhg'] is not None and m['hag'] is not None else None,
            'cf': m['hc'] if home else m['ac'], 'ca': m['ac'] if home else m['hc'],
            'tc': total_corners(m), 'kf': cards_of(m, side), 'ka': cards_of(m, opp), 'tk': total_cards(m),
            'sf': m['hst'] if home else m['ast'], 'sa': m['ast'] if home else m['hst']})
    n = len(rows)
    if n == 0:
        return None

    def m_(k):
        return mean([r[k] for r in rows])

    def cnt(k):
        return sum(1 for r in rows if r[k] is not None)

    p = {'n': n, 'gf': m_('gf'), 'ga': m_('ga'),
         'o25': 100.0 * sum(1 for r in rows if r['tot'] >= 3) / n,
         'btts': 100.0 * sum(1 for r in rows if r['gf'] > 0 and r['ga'] > 0) / n,
         'cs': 100.0 * sum(1 for r in rows if r['ga'] == 0) / n,
         'fts': 100.0 * sum(1 for r in rows if r['gf'] == 0) / n,
         'h1': m_('h1'), 'n_h1': cnt('h1'),
         'cf': m_('cf'), 'ca': m_('ca'), 'tc': m_('tc'), 'n_c': cnt('tc'),
         'kf': m_('kf'), 'ka': m_('ka'), 'tk': m_('tk'), 'n_k': cnt('tk'),
         'sf': m_('sf'), 'sa': m_('sa'), 'n_s': cnt('sf')}
    p['form'] = [{'res': 'W' if (m['hg'] > m['ag'] if m['h'] == name else m['ag'] > m['hg']) else
                  ('D' if m['hg'] == m['ag'] else 'L'),
                  'score': '%d-%d' % (m['hg'], m['ag']), 'venue': 'H' if m['h'] == name else 'A',
                  'opp': m['a'] if m['h'] == name else m['h'], 'date': m['d'].strftime('%Y-%m-%d')} for m in ms[:5]]
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()}


def pois_pmf(lam, kmax=14):
    p = math.exp(-lam)
    out = [p]
    for k in range(1, kmax + 1):
        p *= lam / k
        out.append(p)
    return out


def nb_pmf(mu, ratio, kmax=45):
    """Negative binomial with variance = mu * ratio. Corners and cards run more spread out than Poisson."""
    if ratio <= 1.01:
        return pois_pmf(mu, kmax)
    r = mu / (ratio - 1.0)
    p = r / (r + mu)
    return [math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))
            for k in range(kmax + 1)]


def p_over(pmf, line):
    return max(0.0, 1.0 - sum(pmf[:int(math.floor(line)) + 1]))


def shrink(v, n, prior, k=5.0):
    return (v * n + prior * k) / (n + k)


def predict(H, A, lg, ref=None):
    """Simple, transparent model: Poisson goals from attack and defence rates pulled toward the league mean."""
    if not H or not A or lg.get('n', 0) < 20:
        return None
    gt = lg['g_team']
    hf = max(1.0, min(1.7, lg['gh'] / lg['ga']))
    att_h, def_h = shrink(H['gf'], H['n'], gt) / gt, shrink(H['ga'], H['n'], gt) / gt
    att_a, def_a = shrink(A['gf'], A['n'], gt) / gt, shrink(A['ga'], A['n'], gt) / gt
    lh = gt * math.sqrt(hf) * att_h * def_a
    la = gt / math.sqrt(hf) * att_a * def_h
    ph_, pa_ = pois_pmf(lh), pois_pmf(la)
    ph = pd = pa = 0.0
    tot = [0.0] * 30
    for i, x in enumerate(ph_):
        for j, y in enumerate(pa_):
            p = x * y
            tot[i + j] += p
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    s = ph + pd + pa
    ph, pd, pa = ph / s, pd / s, pa / s
    lam_t = lh + la
    h1 = lam_t * lg['h1share']
    out = {'lam': [round(lh, 3), round(la, 3)], 'p1x2': [round(ph, 4), round(pd, 4), round(pa, 4)],
           'exp_total': round(lam_t, 2),
           'goals': {'o15': round(p_over(tot, 1.5), 4), 'o25': round(p_over(tot, 2.5), 4),
                     'o35': round(p_over(tot, 3.5), 4), 'btts': round((1 - ph_[0]) * (1 - pa_[0]), 4),
                     'h1o05': round(1 - math.exp(-h1), 4), 'h1o15': round(1 - math.exp(-h1) * (1 + h1), 4)},
           'corners': None, 'cards': None, 'n': [H['n'], A['n']],
           'conf': 'ok' if min(H['n'], A['n']) >= 8 else 'thin'}
    if H.get('n_c', 0) >= 4 and A.get('n_c', 0) >= 4 and lg.get('tc'):
        rh = shrink(H['tc'], H['n_c'], lg['tc']) / lg['tc']
        ra = shrink(A['tc'], A['n_c'], lg['tc']) / lg['tc']
        exp = lg['tc'] * math.sqrt(rh * ra)
        pmf = nb_pmf(exp, 1.15)
        den = (H['cf'] or 0) + (A['ca'] or 0) + (A['cf'] or 0) + (H['ca'] or 0)
        share = ((H['cf'] or 0) + (A['ca'] or 0)) / den if den else 0.5
        out['corners'] = {'exp': round(exp, 2), 'home': round(exp * share, 2), 'away': round(exp * (1 - share), 2),
                          'over': {str(l): round(p_over(pmf, l), 4) for l in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5)}}
    if H.get('n_k', 0) >= 4 and A.get('n_k', 0) >= 4 and lg.get('tk'):
        rh = shrink(H['tk'], H['n_k'], lg['tk']) / lg['tk']
        ra = shrink(A['tk'], A['n_k'], lg['tk']) / lg['tk']
        exp = lg['tk'] * math.sqrt(rh * ra)
        refx = None
        if ref and ref.get('n', 0) >= 3:
            rr = shrink(ref['avg'], ref['n'], lg['tk'], 4.0) / lg['tk']
            exp *= rr
            refx = round(rr, 3)
        pmf = nb_pmf(exp, 1.15)
        out['cards'] = {'exp': round(exp, 2), 'ref_factor': refx,
                        'over': {str(l): round(p_over(pmf, l), 4) for l in (2.5, 3.5, 4.5, 5.5, 6.5)}}
    return out


# ---------------------------------------------------------------- bookmaker odds (market consensus)
def pick(r, *cols):
    for c in cols:
        v = num(r.get(c))
        if v and v > 1.0:
            return v
    return None


def consensus(r):
    h, d, a = pick(r, 'AvgH', 'B365H', 'PSH'), pick(r, 'AvgD', 'B365D', 'PSD'), pick(r, 'AvgA', 'B365A', 'PSA')
    mkt = ou = ah = None
    if h and d and a:
        inv = [1 / h, 1 / d, 1 / a]
        s = sum(inv)
        mkt = {'odds': [round(h, 2), round(d, 2), round(a, 2)],
               'best': [pick(r, 'MaxH'), pick(r, 'MaxD'), pick(r, 'MaxA')],
               'fair': [round(x / s, 4) for x in inv], 'margin': round(s - 1, 4)}
    o, u = pick(r, 'Avg>2.5', 'B365>2.5', 'P>2.5', 'BbAv>2.5'), pick(r, 'Avg<2.5', 'B365<2.5', 'P<2.5', 'BbAv<2.5')
    if o and u:
        s = 1 / o + 1 / u
        ou = {'line': 2.5, 'over': round(o, 2), 'under': round(u, 2), 'fair_over': round((1 / o) / s, 4),
              'best_over': pick(r, 'Max>2.5'), 'best_under': pick(r, 'Max<2.5')}
    line = num(r.get('AHh'))
    if line is not None:
        ah = {'line': line, 'home': pick(r, 'AvgAHH', 'B365AHH', 'PAHH'), 'away': pick(r, 'AvgAHA', 'B365AHA', 'PAHA')}
    return mkt, ou, ah


# ---------------------------------------------------------------- name matching (for the optional sources)
def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"[^a-z0-9 ]", ' ', s.replace("'", ''))
    toks = [t for t in s.split() if t not in STOP and not t.isdigit()]
    return ' '.join(toks)


ALIAS_N = {norm(k): v for k, v in ALIASES.items()}


def match_name(name, candidates):
    """Map a name from another source onto a football-data.co.uk team name."""
    n = norm(name)
    by_norm = {norm(c): c for c in candidates}
    if n in by_norm:
        return by_norm[n]
    a = ALIAS_N.get(n)
    if a and a in candidates:
        return a
    best = difflib.get_close_matches(n, list(by_norm), n=1, cutoff=0.78)
    if best:
        return by_norm[best[0]]
    for k, c in by_norm.items():
        if k and (k in n or n in k) and min(len(k), len(n)) >= 5:
            return c
    return None


# ---------------------------------------------------------------- European leagues
def build_league(code):
    cur_rows = read_rows(fetch(BASE.format(s=CUR, f=code)))
    try:
        prev_rows = read_rows(fetch(BASE.format(s=PREV, f=code)))
    except Exception as e:
        prev_rows = []
        NOTES.append('%s: previous season file not read (%s)' % (code, e))
    cur = sorted([m for m in map(to_match, cur_rows) if m], key=lambda m: m['d'])
    prev = sorted([m for m in map(to_match, prev_rows) if m], key=lambda m: m['d'])
    allm = prev + cur
    lg = league_avgs(cur if len(cur) >= 40 else allm)
    names = sorted({m['h'] for m in cur} | {m['a'] for m in cur})
    by_team = {}
    for t in names:
        mine = [m for m in allm if m['h'] == t or m['a'] == t]
        mine.sort(key=lambda m: m['d'], reverse=True)
        by_team[t] = mine[:WINDOW]
    profiles = {t: profile(ms, t) for t, ms in by_team.items()}
    table = {}
    for t in names:
        w = d = l = gf = ga = 0
        for m in cur:
            if m['h'] != t and m['a'] != t:
                continue
            f, a = (m['hg'], m['ag']) if m['h'] == t else (m['ag'], m['hg'])
            gf += f
            ga += a
            if f > a:
                w += 1
            elif f == a:
                d += 1
            else:
                l += 1
        table[t] = {'p': w + d + l, 'w': w, 'd': d, 'l': l, 'gf': gf, 'ga': ga, 'pts': 3 * w + d}
    order = sorted(names, key=lambda t: (-table[t]['pts'], -(table[t]['gf'] - table[t]['ga']), -table[t]['gf'], t))
    for i, t in enumerate(order, 1):
        table[t]['rank'] = i
        table[t]['of'] = len(order)
    refs = {}
    for m in allm:
        k = total_cards(m)
        if m['ref'] and k is not None:
            refs.setdefault(m['ref'], []).append(k)
    refstats = {r: {'n': len(v), 'avg': sum(v) / len(v)} for r, v in refs.items()}
    return {'code': code, 'lg': lg, 'profiles': profiles, 'table': table, 'refs': refstats, 'names': names,
            'cur_n': len(cur)}


def team_entry(code, name, B):
    pr = B['profiles'].get(name) or {}
    t = dict(B['table'].get(name, {}))
    t.update({'name': DISPLAY.get(name, name), 'raw': name, 'league': code})
    t['stats'] = pr or None
    return t


def odds_fixtures(built):
    raw = fetch(FIX_URL)
    out = []
    for r in read_rows(raw):
        code = r.get('Div')
        if code not in built:
            continue
        ko, known = to_utc(r['Date'], r.get('Time'))
        if ko is None or ko < NOW - timedelta(hours=4) or ko > NOW + timedelta(days=14):
            continue
        B = built[code]
        home, away = r['HomeTeam'], r['AwayTeam']
        ref = r.get('Referee') or None
        rs = B['refs'].get(ref) if ref else None
        mkt, ou, ah = consensus(r)
        model = predict(B['profiles'].get(home), B['profiles'].get(away), B['lg'], rs)
        out.append({'id': '%s-%s-%s-%s' % (code, ko.strftime('%Y%m%d'), norm(home).replace(' ', ''), norm(away).replace(' ', '')),
                    'league': code, 'ko': ko.strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': known,
                    'home': '%s:%s' % (code, home), 'away': '%s:%s' % (code, away), 'source': 'odds file',
                    'ref': ({'name': ref, 'n': rs['n'], 'avg_cards': round(rs['avg'], 2)} if rs else ({'name': ref} if ref else None)),
                    'mkt': mkt, 'ou': ou, 'ah': ah, 'model': model})
    return out


ESPN = {'E0': 'eng.1', 'SP1': 'esp.1', 'I1': 'ita.1', 'D1': 'ger.1', 'F1': 'fra.1'}
ESPN_CL = 'uefa.champions'
ESPN_NPFL = 'nga.1'


def espn_events(slug, days=9):
    """Keyless ESPN scoreboard: upcoming and live matches for one competition."""
    d0 = NOW.strftime('%Y%m%d')
    d1 = (NOW + timedelta(days=days)).strftime('%Y%m%d')
    url = 'https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard?%s' % (slug, urlencode({'dates': '%s-%s' % (d0, d1), 'limit': 300}))
    j = json.loads(fetch(url))
    out = []
    for ev in j.get('events', []):
        comp = (ev.get('competitions') or [{}])[0]
        cs = comp.get('competitors') or []
        home = next((c for c in cs if c.get('homeAway') == 'home'), None)
        away = next((c for c in cs if c.get('homeAway') == 'away'), None)
        if not home or not away:
            continue
        state = ((ev.get('status') or {}).get('type') or {}).get('state')
        if state == 'post':
            continue
        ko = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
        out.append({'id': str(ev['id']), 'ko': ko.astimezone(timezone.utc), 'state': state,
                    'h': home.get('team') or {}, 'a': away.get('team') or {}})
    return out


def espn_name(team, names):
    for k in ('displayName', 'shortDisplayName', 'name', 'location'):
        v = team.get(k)
        if v:
            m = match_name(v, names)
            if m:
                return m
    return None


def espn_fixtures(built, have):
    """Links each odds-file fixture to its ESPN match (for live data) and adds fixtures the odds file lacks."""
    extra, cl, attached = [], [], 0
    all_names = {c: B['names'] for c, B in built.items()}
    for code, slug in ESPN.items():
        if code not in built:
            continue
        try:
            evs = espn_events(slug)
        except Exception as e:
            NOTES.append('ESPN %s: %s' % (slug, e))
            continue
        names = all_names[code]
        B = built[code]
        for ev in evs:
            h, a = espn_name(ev['h'], names), espn_name(ev['a'], names)
            if not h or not a:
                NOTES.append('ESPN: could not match %s v %s in %s' % (ev['h'].get('displayName'), ev['a'].get('displayName'), code))
                continue
            hit = None
            for f in have + extra:
                if f['league'] == code and f['home'] == '%s:%s' % (code, h) and f['away'] == '%s:%s' % (code, a):
                    fk = datetime.strptime(f['ko'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
                    if abs((fk - ev['ko']).total_seconds()) < 36 * 3600:
                        hit = f
                        break
            if hit:
                hit['espn'] = {'slug': slug, 'id': ev['id']}
                attached += 1
                continue
            model = predict(B['profiles'].get(h), B['profiles'].get(a), B['lg'], None)
            extra.append({'id': '%s-%s-%s-%s' % (code, ev['ko'].strftime('%Y%m%d'), norm(h).replace(' ', ''), norm(a).replace(' ', '')),
                          'league': code, 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': True,
                          'home': '%s:%s' % (code, h), 'away': '%s:%s' % (code, a), 'source': 'ESPN',
                          'espn': {'slug': slug, 'id': ev['id']}, 'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': model})
            attached += 1
    try:
        for ev in espn_events(ESPN_CL):
            def key_for(team):
                for c, names in all_names.items():
                    m = espn_name(team, names)
                    if m:
                        return '%s:%s' % (c, m)
                return 'X:' + (team.get('displayName') or team.get('name') or '?')
            cl.append({'ko': ev['ko'], 'h': key_for(ev['h']), 'a': key_for(ev['a']), 'id': ev['id'],
                       'hn': ev['h'].get('displayName') or '?', 'an': ev['a'].get('displayName') or '?'})
    except Exception as e:
        NOTES.append('ESPN Champions League: %s' % e)
    return extra, cl, attached


def espn_npfl(fixtures, teams, leagues):
    """NPFL fixtures straight from ESPN (used when there is no API-Football key). Fixtures only, no stats."""
    evs = espn_events(ESPN_NPFL, days=14)
    n = 0
    for ev in evs:
        h, a = ev['h'].get('displayName') or '?', ev['a'].get('displayName') or '?'
        for nm in (h, a):
            teams.setdefault('NPFL:' + nm, {'name': nm, 'raw': nm, 'league': 'NPFL', 'stats': None})
        cls, note = travel_class(h, a)
        hc = city_of(h)
        fixtures.append({'id': 'NPFL-%s' % ev['id'], 'league': 'NPFL', 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'),
                         'koKnown': not (ev['ko'].hour == 0 and ev['ko'].minute == 0), 'home': 'NPFL:' + h, 'away': 'NPFL:' + a,
                         'source': 'ESPN', 'espn': {'slug': ESPN_NPFL, 'id': ev['id']}, 'ref': None, 'mkt': None,
                         'ou': None, 'ah': None, 'model': None, 'travel': cls, 'travelNote': note,
                         'altitude': bool(hc and hc[0] == 'Jos')})
        n += 1
    leagues.setdefault('NPFL', {'name': 'NPFL (Nigeria)', 'short': 'NPFL', 'comp': 'npfl', 'avg': {}})
    return n


# ---------------------------------------------------------------- NPFL (optional, API-Football)
CITY = {  # home city of each club: (lat, lon). Used only to estimate road-trip length.
    'enyimba': ('Aba', 5.12, 7.37), 'rivers united': ('Port Harcourt', 4.82, 7.05), 'enugu rangers': ('Enugu', 6.44, 7.50),
    'rangers': ('Enugu', 6.44, 7.50), 'plateau united': ('Jos', 9.92, 8.89), 'kano pillars': ('Kano', 12.0, 8.52),
    'barau': ('Kano', 12.0, 8.52), 'katsina united': ('Katsina', 12.99, 7.60), 'ikorodu city': ('Ikorodu', 6.62, 3.51),
    'sporting lagos': ('Lagos', 6.52, 3.38), 'inter lagos': ('Lagos', 6.52, 3.38), 'remo stars': ('Ikenne', 6.87, 3.70),
    'shooting stars': ('Ibadan', 7.38, 3.95), 'kwara united': ('Ilorin', 8.50, 4.55), 'niger tornadoes': ('Minna', 9.61, 6.55),
    'ranchers bees': ('Kaduna', 10.52, 7.44), 'nasarawa united': ('Lafia', 8.49, 8.52), 'doma united': ('Gombe', 10.29, 11.17),
    'bendel insurance': ('Benin City', 6.34, 5.63), 'warri wolves': ('Warri', 5.52, 5.75), 'abia warriors': ('Umuahia', 5.53, 7.49),
    'akwa united': ('Uyo', 5.03, 7.93), 'heartland': ('Owerri', 5.48, 7.03), 'bayelsa united': ('Yenagoa', 4.93, 6.26),
    'lobi stars': ('Makurdi', 7.73, 8.52), 'wikki tourists': ('Bauchi', 10.31, 9.84), 'el kanemi warriors': ('Maiduguri', 11.85, 13.16),
    'sunshine stars': ('Akure', 7.25, 5.19), 'gombe united': ('Gombe', 10.29, 11.17), 'kun khalifat': None,
}


def city_of(name):
    n = norm(name).replace(' fc', '')
    for k, v in CITY.items():
        if k == n or (len(k) > 6 and k in n):
            return v
    return None


def travel_class(home, away):
    a, b = city_of(home), city_of(away)
    if not a or not b:
        return None, 'Away travel unknown'
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[2], b[1], b[2]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(h)) * 1.35     # straight line to road distance
    hrs = km / 55.0                                      # average road speed
    cls = 'short' if hrs < 3 else ('mid' if hrs < 9.5 else 'long')
    return cls, '%s to %s, about %d hours by road (estimate from city distances)' % (b[0], a[0], round(hrs))


def af_get(key, path, params):
    j = json.loads(fetch('https://v3.football.api-sports.io' + path + '?' + urlencode(params), headers={'x-apisports-key': key}))
    if j.get('errors'):
        raise RuntimeError('API-Football: ' + json.dumps(j['errors'])[:240])
    return j.get('response', [])


def npfl_update(key):
    state_path = os.path.join(DATA_DIR, 'npfl_state.json')
    state = {}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    if not state.get('league_id'):
        found = []
        for x in af_get(key, '/leagues', {'country': 'Nigeria'}):
            L = x['league']
            nm = L['name'].lower()
            if 'women' in nm or L.get('type') != 'League':
                continue
            score = 3 if nm == 'npfl' else 2 if ('npfl' in nm or 'premier' in nm) else 0
            found.append((score, L['id'], L['name']))
        found.sort(reverse=True)
        if not found or found[0][0] == 0:
            raise RuntimeError('No NPFL league in API-Football. Leagues seen: %s' % [f[2] for f in found][:8])
        state['league_id'] = found[0][1]
        state['league_name'] = found[0][2]
    info = af_get(key, '/leagues', {'id': state['league_id']})
    seasons = info[0].get('seasons', []) if info else []
    cur = [s for s in seasons if s.get('current')] or sorted(seasons, key=lambda s: s['year'])[-1:]
    if not cur:
        raise RuntimeError('NPFL has no seasons in API-Football')
    season = cur[0]['year']
    cov = (cur[0].get('coverage') or {}).get('fixtures') or {}
    stats_ok = bool(cov.get('statistics_fixtures'))
    fx = af_get(key, '/fixtures', {'league': state['league_id'], 'season': season})
    hist = state.setdefault('history', {})
    if state.get('season') != season:
        hist.clear()
    state['season'] = season
    fins = [f for f in fx if f['fixture']['status']['short'] in ('FT', 'AET', 'PEN') and f['goals']['home'] is not None]
    got = empties = 0
    if stats_ok:
        for f in sorted(fins, key=lambda f: f['fixture']['date'], reverse=True):
            fid = str(f['fixture']['id'])
            if fid in hist:
                continue
            if got >= 60 or empties >= 4:
                break
            res = af_get(key, '/fixtures/statistics', {'fixture': fid})
            rec = {}
            for ent in res:
                side = 'h' if ent['team']['name'] == f['teams']['home']['name'] else 'a'
                vals = {s['type']: s['value'] for s in ent.get('statistics', [])}
                rec[side + 'c'] = whole(vals.get('Corner Kicks'))
                rec[side + 'y'] = whole(vals.get('Yellow Cards'))
                rec[side + 'r'] = whole(vals.get('Red Cards'))
                rec[side + 'st'] = whole(vals.get('Shots on Goal'))
            hist[fid] = rec
            got += 1
            empties = empties + 1 if not rec else 0
    ms = []
    for f in fins:
        st = hist.get(str(f['fixture']['id']), {})
        ht = (f.get('score') or {}).get('halftime') or {}
        ms.append({'d': datetime.fromisoformat(f['fixture']['date']).astimezone(timezone.utc),
                   'h': f['teams']['home']['name'], 'a': f['teams']['away']['name'],
                   'hg': f['goals']['home'], 'ag': f['goals']['away'], 'hhg': ht.get('home'), 'hag': ht.get('away'),
                   'hc': st.get('hc'), 'ac': st.get('ac'), 'hy': st.get('hy'), 'ay': st.get('ay'),
                   'hr': st.get('hr'), 'ar': st.get('ar'), 'hst': st.get('hst'), 'ast': st.get('ast'), 'ref': None})
    ms.sort(key=lambda m: m['d'])
    lg = league_avgs(ms)
    names = sorted({m['h'] for m in ms} | {m['a'] for m in ms} | {f['teams'][s]['name'] for f in fx for s in ('home', 'away')})
    prof, table = {}, {}
    for t in names:
        mine = sorted([m for m in ms if m['h'] == t or m['a'] == t], key=lambda m: m['d'], reverse=True)
        prof[t] = profile(mine[:WINDOW], t)
        w = d = l = gf = ga = 0
        for m in mine:
            fo, ag = (m['hg'], m['ag']) if m['h'] == t else (m['ag'], m['hg'])
            gf += fo
            ga += ag
            w, d, l = (w + 1, d, l) if fo > ag else ((w, d + 1, l) if fo == ag else (w, d, l + 1))
        table[t] = {'p': w + d + l, 'w': w, 'd': d, 'l': l, 'gf': gf, 'ga': ga, 'pts': 3 * w + d}
    order = sorted(names, key=lambda t: (-table[t]['pts'], -(table[t]['gf'] - table[t]['ga']), -table[t]['gf'], t))
    for i, t in enumerate(order, 1):
        table[t]['rank'], table[t]['of'] = i, len(order)
    fixtures = []
    for f in fx:
        if f['fixture']['status']['short'] not in ('NS', 'TBD'):
            continue
        ko = datetime.fromisoformat(f['fixture']['date']).astimezone(timezone.utc)
        if ko < NOW - timedelta(hours=4) or ko > NOW + timedelta(days=14):
            continue
        h, a = f['teams']['home']['name'], f['teams']['away']['name']
        cls, note = travel_class(h, a)
        hc = city_of(h)
        fixtures.append({'id': 'NPFL-%s' % f['fixture']['id'], 'league': 'NPFL', 'ko': ko.strftime('%Y-%m-%dT%H:%M:00Z'),
                         'koKnown': not (ko.hour == 0 and ko.minute == 0), 'home': 'NPFL:' + h, 'away': 'NPFL:' + a,
                         'source': 'API-Football', 'ref': None, 'mkt': None, 'ou': None, 'ah': None,
                         'model': predict(prof.get(h), prof.get(a), lg, None), 'travel': cls, 'travelNote': note,
                         'altitude': bool(hc and hc[0] == 'Jos')})
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(state_path, 'w') as fh:
        json.dump(state, fh, separators=(',', ':'))
    teams = {}
    for t in names:
        e = dict(table[t])
        e.update({'name': t, 'raw': t, 'league': 'NPFL', 'stats': prof[t]})
        teams['NPFL:' + t] = e
    stats_note = 'corner and card data %s' % ('on' if stats_ok else 'not offered by the free feed for this league')
    return teams, fixtures, lg, '%s season %s: %d played, %d upcoming, %d stat lookups today, %s' % (state['league_name'], season, len(fins), len(fixtures), got, stats_note)


# ---------------------------------------------------------------- main
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, 'fixtures.json')
    prev = {}
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    built = {}
    for code in LEAGUES:
        try:
            built[code] = build_league(code)
            log('%s: %d matches this season, %d teams' % (code, built[code]['cur_n'], len(built[code]['names'])))
        except Exception as e:
            STATUS['football-data.co.uk ' + code] = 'FAILED: %s' % e
            log('FAILED', code, e)
    teams, fixtures, leagues = {}, [], {}
    for code, B in built.items():
        leagues[code] = {'name': LEAGUES[code]['name'], 'short': LEAGUES[code]['short'], 'comp': 'europe',
                         'avg': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in B['lg'].items()}}
        for n in B['names']:
            teams['%s:%s' % (code, n)] = team_entry(code, n, B)
    if built:
        try:
            fixtures += odds_fixtures(built)
            STATUS['football-data.co.uk'] = 'ok: %d leagues, %d fixtures with odds' % (len(built), len(fixtures))
        except Exception as e:
            STATUS['football-data.co.uk fixtures'] = 'FAILED: %s' % e
        cl = []
        try:
            extra, cl, attached = espn_fixtures(built, fixtures)
            fixtures += extra
            STATUS['ESPN (live feed)'] = 'ok: %d fixtures linked for lineups and live events, %d extra fixtures, %d Champions League' % (attached, len(extra), len(cl))
            if cl:
                leagues['CL'] = {'name': 'Champions League', 'short': 'UCL', 'comp': 'europe', 'avg': {}}
            for c in cl:
                for key_, nm in ((c['h'], c['hn']), (c['a'], c['an'])):
                    if key_.startswith('X:'):
                        teams[key_] = {'name': nm, 'raw': nm, 'league': 'CL', 'stats': None}
                fixtures.append({'id': 'CL-%s-%s' % (c['ko'].strftime('%Y%m%d'), norm(c['hn']).replace(' ', '')),
                                 'league': 'CL', 'ko': c['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': True,
                                 'home': c['h'], 'away': c['a'], 'source': 'ESPN', 'espn': {'slug': ESPN_CL, 'id': c['id']},
                                 'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': None})
        except Exception as e:
            STATUS['ESPN (live feed)'] = 'FAILED: %s (lineups, live events and mid-week fixtures will be missing)' % e
        # continental tie within 4 days after a league fixture
        cl_times = {}
        for f in fixtures:
            if f['league'] == 'CL':
                for s in ('home', 'away'):
                    cl_times[f[s]] = datetime.strptime(f['ko'][:16], '%Y-%m-%dT%H:%M')
        for f in fixtures:
            if f['league'] in ('CL', 'NPFL'):
                continue
            ko = datetime.strptime(f['ko'][:16], '%Y-%m-%dT%H:%M')
            ah = {s: None for s in ('home', 'away')}
            for s in ('home', 'away'):
                t = cl_times.get(f[s])
                if t and timedelta(0) < t - ko <= timedelta(days=4):
                    ah[s] = t.strftime('%Y-%m-%dT%H:%M:00Z')
            if ah['home'] or ah['away']:
                f['ahead'] = ah

    k2 = os.environ.get('API_FOOTBALL_KEY', '').strip()
    if k2:
        try:
            t2, f2, lg2, msg = npfl_update(k2)
            teams.update(t2)
            fixtures += f2
            leagues['NPFL'] = {'name': 'NPFL (Nigeria)', 'short': 'NPFL', 'comp': 'npfl',
                               'avg': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in lg2.items()}}
            STATUS['api-football (NPFL)'] = 'ok: ' + msg
        except Exception as e:
            STATUS['api-football (NPFL)'] = 'FAILED: %s' % e
            kept = [f for f in prev.get('fixtures', []) if f.get('league') == 'NPFL']
            if kept and 'NPFL' in prev.get('leagues', {}):
                leagues['NPFL'] = prev['leagues']['NPFL']
                for tk_, tv in prev.get('teams', {}).items():
                    if tk_.startswith('NPFL:'):
                        teams[tk_] = tv
                fixtures += kept
                STATUS['api-football (NPFL)'] += ' (showing the previous NPFL data)'
    else:
        STATUS['api-football (NPFL)'] = 'skipped: no key (NPFL results and stats need it)'
        try:
            n_ = espn_npfl(fixtures, teams, leagues)
            STATUS['ESPN (NPFL fixtures)'] = 'ok: %d fixtures, no stats' % n_
        except Exception as e:
            STATUS['ESPN (NPFL fixtures)'] = 'FAILED: %s' % e

    if not fixtures and not teams:
        log('Nothing could be fetched. The old data file is left untouched.')
        for k_, v in STATUS.items():
            log(' ', k_, '->', v)
        sys.exit(1)
    fixtures.sort(key=lambda f: (f['ko'][:10], 0 if f['koKnown'] else 1, f['ko']))
    out = {'version': 3, 'asOf': NOW.strftime('%Y-%m-%dT%H:%M:00Z'), 'season': CUR, 'status': STATUS, 'notes': NOTES[:20],
           'leagues': leagues, 'teams': teams, 'fixtures': fixtures}
    with open(out_path, 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    log('Wrote %s: %d fixtures, %d teams' % (out_path, len(fixtures), len(teams)))
    for k_, v in STATUS.items():
        log(' ', k_, '->', v)
    for n in NOTES[:20]:
        log(' note:', n)


if __name__ == '__main__':
    main()

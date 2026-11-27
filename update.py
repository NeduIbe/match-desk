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
    # more leagues from football-data.co.uk (same free files, same stats)
    'E1': {'name': 'English Championship', 'short': 'Champ', 'org': ''},
    'E2': {'name': 'English League One', 'short': 'League 1', 'org': ''},
    'E3': {'name': 'English League Two', 'short': 'League 2', 'org': ''},
    'SC0': {'name': 'Scottish Premiership', 'short': 'Scotland', 'org': ''},
    'D2': {'name': 'German 2. Bundesliga', 'short': '2. Bundesliga', 'org': ''},
    'I2': {'name': 'Italian Serie B', 'short': 'Serie B', 'org': ''},
    'SP2': {'name': 'Spanish Segunda', 'short': 'La Liga 2', 'org': ''},
    'F2': {'name': 'French Ligue 2', 'short': 'Ligue 2', 'org': ''},
    'N1': {'name': 'Dutch Eredivisie', 'short': 'Eredivisie', 'org': ''},
    'B1': {'name': 'Belgian Pro League', 'short': 'Belgium', 'org': ''},
    'P1': {'name': 'Portuguese Primeira Liga', 'short': 'Portugal', 'org': ''},
    'T1': {'name': 'Turkish Super Lig', 'short': 'Turkey', 'org': ''},
    'G1': {'name': 'Greek Super League', 'short': 'Greece', 'org': ''},
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
        bb_ = re.search(r'/basketball/([\w-]+)/(scoreboard|summary)', url)
        e = re.search(r'/soccer/([\w.]+)/(scoreboard|summary)', url)
        name = '%s_%s.csv' % (m.group(1), m.group(2)) if m else url.rsplit('/', 1)[-1].split('?')[0]
        if e:
            name = 'espn_%s_%s.json' % (e.group(1), e.group(2))
            ev_ = re.search(r'event=(\w+)', url)
            if e.group(2) == 'summary' and ev_ and os.path.exists(os.path.join(LOCAL, 'espn_%s_summary_%s.json' % (e.group(1), ev_.group(1)))):
                name = 'espn_%s_summary_%s.json' % (e.group(1), ev_.group(1))
            dd_ = re.search(r'dates=(\d{8})-(\d{8})', url)
            if e.group(2) == 'scoreboard' and dd_ and (datetime.strptime(dd_.group(2), '%Y%m%d').date() - datetime.strptime(dd_.group(1), '%Y%m%d').date()).days > 20:
                name = 'espn_%s_scoreboard_past.json' % e.group(1)
        if 'clubelo.com' in url:
            name = 'clubelo.csv'
        if bb_:
            ev2_ = re.search(r'event=(\w+)', url)
            name = 'bb_%s_summary_%s.json' % (bb_.group(1), ev2_.group(1)) if (bb_.group(2) == 'summary' and ev2_) else 'bb_%s.json' % bb_.group(1)
        if 'open-meteo.com/v1/forecast' in url:
            name = 'openmeteo_forecast.json'
        if 'geocoding-api.open-meteo.com' in url:
            name = 'openmeteo_geo.json'
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
            'hw': sum(1 for m in ms if m['hg'] > m['ag']) / n, 'dw': sum(1 for m in ms if m['hg'] == m['ag']) / n,
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


def est_k(groups, min_teams=8, min_n=4):
    """How many games of evidence a team's own record is worth against the league average, measured from the data itself.
    k = (noise in one game) / (real spread between teams). groups: name -> list of per-game numbers. Returns None if too little data."""
    g = {t: v for t, v in groups.items() if len(v) >= min_n}
    if len(g) < min_teams:
        return None
    means = {t: sum(v) / len(v) for t, v in g.items()}
    ss = sum(sum((x - means[t]) ** 2 for x in v) for t, v in g.items())
    df = sum(len(v) - 1 for v in g.values())
    s2 = ss / df if df else 0.0
    mm = sum(means.values()) / len(means)
    var_means = sum((m - mm) ** 2 for m in means.values()) / (len(means) - 1)
    tau2 = var_means - sum(s2 / len(v) for v in g.values()) / len(g)
    if s2 <= 0:
        return None
    if tau2 <= 1e-9:
        return 50.0
    return max(1.0, min(50.0, s2 / tau2))


def predict(H, A, lg, ref=None):
    """Simple, transparent model: Poisson goals from attack and defence rates pulled toward the league mean."""
    if not H or not A or lg.get('n', 0) < 20:
        return None
    gt = lg['g_team']
    hf = max(1.0, min(1.7, lg['gh'] / lg['ga']))
    kk = lg.get('k') or {}
    kg_ = kk.get('g', 5.0)
    att_h, def_h = shrink(H['gf'], H['n'], gt, kg_) / gt, shrink(H['ga'], H['n'], gt, kg_) / gt
    att_a, def_a = shrink(A['gf'], A['n'], gt, kg_) / gt, shrink(A['ga'], A['n'], gt, kg_) / gt
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
        rh = shrink(H['tc'], H['n_c'], lg['tc'], kk.get('tc', 5.0)) / lg['tc']
        ra = shrink(A['tc'], A['n_c'], lg['tc'], kk.get('tc', 5.0)) / lg['tc']
        exp = lg['tc'] * math.sqrt(rh * ra)
        dsp = lg.get('tc_disp') or 1.0
        pmf = nb_pmf(exp, dsp)
        den = (H['cf'] or 0) + (A['ca'] or 0) + (A['cf'] or 0) + (H['ca'] or 0)
        share = ((H['cf'] or 0) + (A['ca'] or 0)) / den if den else 0.5
        out['corners'] = {'exp': round(exp, 2), 'home': round(exp * share, 2), 'away': round(exp * (1 - share), 2), 'disp': round(dsp, 3),
                          'over': {str(l): round(p_over(pmf, l), 4) for l in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5)}}
    if H.get('n_k', 0) >= 4 and A.get('n_k', 0) >= 4 and lg.get('tk'):
        rh = shrink(H['tk'], H['n_k'], lg['tk'], kk.get('tk', 5.0)) / lg['tk']
        ra = shrink(A['tk'], A['n_k'], lg['tk'], kk.get('tk', 5.0)) / lg['tk']
        exp = lg['tk'] * math.sqrt(rh * ra)
        refx = None
        if ref and ref.get('n', 0) >= 3:
            rr = shrink(ref['avg'], ref['n'], lg['tk'], kk.get('ref', 4.0)) / lg['tk']
            exp *= rr
            refx = round(rr, 3)
        dsp = lg.get('tk_disp') or 1.0
        pmf = nb_pmf(exp, dsp)
        out['cards'] = {'exp': round(exp, 2), 'ref_factor': refx, 'disp': round(dsp, 3),
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
    # measured from the data (not typed in): how much to trust a team's own record, and how spread out corners and cards are
    gvals = {}
    for t, ms_ in by_team.items():
        gvals[t] = [(m['hg'] if m['h'] == t else m['ag']) for m in ms_]
    avals = {t: [(m['ag'] if m['h'] == t else m['hg']) for m in ms_] for t, ms_ in by_team.items()}
    kc = {}
    ks_g = [x for x in (est_k(gvals), est_k(avals)) if x]
    if ks_g:
        kc['g'] = round(sum(ks_g) / len(ks_g), 2)
    kt = est_k({t: [total_corners(m) for m in ms_ if total_corners(m) is not None] for t, ms_ in by_team.items()})
    if kt:
        kc['tc'] = round(kt, 2)
    kk_ = est_k({t: [total_cards(m) for m in ms_ if total_cards(m) is not None] for t, ms_ in by_team.items()})
    if kk_:
        kc['tk'] = round(kk_, 2)
    kr = est_k({r: v for r, v in refs.items()}, min_teams=5, min_n=3)
    if kr:
        kc['ref'] = round(kr, 2)
    lg['k'] = kc
    for key, tot_fn, lgk, nk in (('tc_disp', total_corners, 'tc', 'n_c'), ('tk_disp', total_cards, 'tk', 'n_k')):
        if not lg.get(lgk):
            continue
        kshr = kc.get('tc' if lgk == 'tc' else 'tk', 5.0)
        acc, cnt_ = 0.0, 0
        for m in cur:
            x = tot_fn(m)
            H_, A_ = profiles.get(m['h']), profiles.get(m['a'])
            if x is None or not H_ or not A_ or H_.get(nk, 0) < 4 or A_.get(nk, 0) < 4:
                continue
            e = lg[lgk] * math.sqrt((shrink(H_[lgk], H_[nk], lg[lgk], kshr) / lg[lgk]) * (shrink(A_[lgk], A_[nk], lg[lgk], kshr) / lg[lgk]))
            acc += (x - e) ** 2 / e
            cnt_ += 1
        if cnt_ >= 40:
            lg[key] = max(1.0, round(acc / cnt_, 3))
    recent = [m for m in cur if m['d'] >= NOW - timedelta(days=21)]
    return {'code': code, 'lg': lg, 'profiles': profiles, 'table': table, 'refs': refstats, 'names': names,
            'cur_n': len(cur), 'recent': recent}


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
ESPN_MORE = {'E1': 'eng.2', 'E2': 'eng.3', 'E3': 'eng.4', 'SC0': 'sco.1', 'D2': 'ger.2', 'I2': 'ita.2', 'SP2': 'esp.2',
             'F2': 'fra.2', 'N1': 'ned.1', 'B1': 'bel.1', 'P1': 'por.1', 'T1': 'tur.1', 'G1': 'gre.1'}
ESPN_ALL = dict(ESPN, **ESPN_MORE)      # ESPN itself is kept to the big five for the lineup history (it is slow to read)
# competitions with a match list from ESPN only (no free stats): code -> (ESPN name, display name, short name, country code or '', kind)
ESPN_LIST = {
    'UEL': ('uefa.europa', 'Europa League', 'UEL', '', 'europe'),
    'UECL': ('uefa.europa.conf', 'Conference League', 'UECL', '', 'europe'),
    'FAC': ('eng.fa', 'FA Cup', 'FA Cup', 'GB', 'europe'),
    'EFL': ('eng.league_cup', 'EFL Cup', 'EFL Cup', 'GB', 'europe'),
    'CDR': ('esp.copa_del_rey', 'Copa del Rey', 'Copa del Rey', 'ES', 'europe'),
    'DFB': ('ger.dfb_pokal', 'DFB Pokal', 'DFB Pokal', 'DE', 'europe'),
    'CIT': ('ita.coppa_italia', 'Coppa Italia', 'Coppa Italia', 'IT', 'europe'),
    'CDF': ('fra.coupe_de_france', 'Coupe de France', 'Coupe de France', 'FR', 'europe'),
    'UNL': ('uefa.nations', 'Nations League', 'Nations League', '', 'europe'),
    'FRI': ('fifa.friendly', 'International friendlies', 'Friendlies', '', 'other'),
    'CAFCL': ('caf.champions', 'CAF Champions League', 'CAF CL', '', 'other'),
    'CAFCC': ('caf.confed', 'CAF Confederation Cup', 'CAF CC', '', 'other'),
    'MLS': ('usa.1', 'MLS (USA)', 'MLS', 'US', 'other'),
    'MEX': ('mex.1', 'Liga MX (Mexico)', 'Liga MX', 'MX', 'other'),
    'BRA': ('bra.1', 'Brazil Serie A', 'Brazil', 'BR', 'other'),
    'ARG': ('arg.1', 'Argentina Primera', 'Argentina', 'AR', 'other'),
    'JPN': ('jpn.1', 'J1 League (Japan)', 'J1', 'JP', 'other'),
    'KOR': ('kor.1', 'K League 1 (Korea)', 'K League', 'KR', 'other'),
    'SAU': ('sau.1', 'Saudi Pro League', 'Saudi', 'SA', 'other'),
    'AUS': ('aus.1', 'A-League (Australia)', 'A-League', 'AU', 'other'),
    'RSA': ('rsa.1', 'South Africa Premiership', 'S. Africa', 'ZA', 'other'),
    'EGY': ('egy.1', 'Egypt Premier League', 'Egypt', 'EG', 'other'),
    'LIB': ('conmebol.libertadores', 'Copa Libertadores', 'Libertadores', '', 'other'),
    'SUD': ('conmebol.sudamericana', 'Copa Sudamericana', 'Sudamericana', '', 'other'),
    'ACL': ('afc.champions', 'AFC Champions League', 'AFC CL', '', 'other'),
    'CCC': ('concacaf.champions', 'CONCACAF Champions Cup', 'CONCACAF', '', 'other'),
}
LISTONLY_TEAMS = {}
ESPN_CL = 'uefa.champions'
ESPN_NPFL = 'nga.1'


ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard'
ESPN_SIZE = [None]   # chunk size (days) that ESPN accepted last time: 0 = whole range, 30, 14, 7, 1
ESPN_SIZES = [0, 30, 14, 7, 1]


def espn_board(slug, d0, d1):
    """ESPN scoreboard events for a date range. ESPN refuses some range sizes with an error,
    so this tries the whole range first, then smaller chunks (30, 14, 7 days, then one day at a time)."""
    base = ESPN_BASE % slug
    a0 = datetime.strptime(d0, '%Y%m%d')
    span = (datetime.strptime(d1, '%Y%m%d') - a0).days
    order = list(ESPN_SIZES)
    if ESPN_SIZE[0] in order:
        order.remove(ESPN_SIZE[0]); order.insert(0, ESPN_SIZE[0])
    last = None
    for size in order:
        if size == 1 and span > 150:
            continue          # one request per day for a long history is too many requests
        if size and size >= span:
            continue          # same as the whole range
        urls = []
        if not size:
            urls = [base + '?' + urlencode({'dates': '%s-%s' % (d0, d1), 'limit': 300})]
        else:
            k = 0
            while k <= span:
                c0 = a0 + timedelta(days=k)
                c1 = a0 + timedelta(days=min(span, k + size - 1))
                dd = c0.strftime('%Y%m%d') if size == 1 else '%s-%s' % (c0.strftime('%Y%m%d'), c1.strftime('%Y%m%d'))
                urls.append(base + '?' + urlencode({'dates': dd}))
                k += size
        try:
            events, seen = [], set()
            for url in urls:
                j = json.loads(fetch(url, tries=2 if (size == order[0] and len(urls) == 1) else 1, timeout=30))
                for ev in j.get('events', []):
                    if ev.get('id') not in seen:
                        seen.add(ev.get('id')); events.append(ev)
            ESPN_SIZE[0] = size
            return {'events': events}
        except Exception as e:
            last = e
            if '404' in str(e):
                break           # this competition does not exist on ESPN: no point trying other date forms
    raise last or RuntimeError('no usable ESPN date form')


def espn_events(slug, days=9):
    """Keyless ESPN scoreboard: upcoming and live matches for one competition."""
    d0 = NOW.strftime('%Y%m%d')
    d1 = (NOW + timedelta(days=days)).strftime('%Y%m%d')
    j = espn_board(slug, d0, d1)
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
        ven = comp.get('venue') or {}
        out.append({'id': str(ev['id']), 'ko': ko.astimezone(timezone.utc), 'state': state,
                    'h': home.get('team') or {}, 'a': away.get('team') or {},
                    'venue': {'n': ven.get('fullName'), 'c': (ven.get('address') or {}).get('city')}})
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
    for code, slug in ESPN_ALL.items():
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
                # a club name the odds file spells differently: still list the match, with no numbers
                hn_, an_ = ev['h'].get('displayName') or ev['h'].get('name') or '?', ev['a'].get('displayName') or ev['a'].get('name') or '?'
                for nm_ in (hn_, an_):
                    LISTONLY_TEAMS['%s:%s' % (code, nm_)] = {'name': nm_, 'raw': nm_, 'league': code, 'stats': None}
                extra.append({'id': '%s-%s' % (code, ev['id']), 'league': code, 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': True,
                              'home': '%s:%s' % (code, hn_), 'away': '%s:%s' % (code, an_), 'source': 'ESPN',
                              'espn': {'slug': slug, 'id': ev['id']}, 'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': None,
                              'venue': ev.get('venue')})
                attached += 1
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
                if (ev.get('venue') or {}).get('c'):
                    hit['venue'] = ev['venue']
                attached += 1
                continue
            model = predict(B['profiles'].get(h), B['profiles'].get(a), B['lg'], None)
            extra.append({'id': '%s-%s-%s-%s' % (code, ev['ko'].strftime('%Y%m%d'), norm(h).replace(' ', ''), norm(a).replace(' ', '')),
                          'league': code, 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': True,
                          'home': '%s:%s' % (code, h), 'away': '%s:%s' % (code, a), 'source': 'ESPN',
                          'espn': {'slug': slug, 'id': ev['id']}, 'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': model,
                          'venue': ev.get('venue')})
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
                       'hn': ev['h'].get('displayName') or '?', 'an': ev['a'].get('displayName') or '?', 'venue': ev.get('venue')})
    except Exception as e:
        NOTES.append('ESPN Champions League: %s' % e)
    return extra, cl, attached


def espn_other(built, teams, leagues, fixtures):
    """Match lists for competitions the free stats files do not cover: ESPN only (kick-off, weather, news, live score)."""
    all_names = {c: B['names'] for c, B in built.items()}
    ok = bad = n = 0
    have = {((f.get('espn') or {}).get('slug'), (f.get('espn') or {}).get('id')) for f in fixtures if f.get('espn')}
    for code, (slug, name, short, cc, kind) in ESPN_LIST.items():
        try:
            evs = espn_events(slug)
        except Exception as e:
            bad += 1
            continue
        ok += 1
        added = 0
        for ev in evs:
            if (slug, ev['id']) in have:
                continue
            keys = []
            for tm in (ev['h'], ev['a']):
                nm = tm.get('displayName') or tm.get('name') or '?'
                key = None
                if kind == 'europe':        # link to the club's league entry when we know the club, so lineups history is shared
                    for c, names in all_names.items():
                        m = espn_name(tm, names)
                        if m:
                            key = '%s:%s' % (c, m)
                            break
                if not key:
                    key = '%s:%s' % (code, nm)
                    teams.setdefault(key, {'name': nm, 'raw': nm, 'league': code, 'stats': None})
                keys.append(key)
            fixtures.append({'id': '%s-%s' % (code, ev['id']), 'league': code, 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'koKnown': True,
                             'home': keys[0], 'away': keys[1], 'source': 'ESPN', 'espn': {'slug': slug, 'id': ev['id']},
                             'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': None, 'venue': ev.get('venue')})
            have.add((slug, ev['id']))
            added += 1
        n += added
        if added:
            leagues[code] = {'name': name, 'short': short, 'comp': 'europe' if kind == 'europe' else 'other', 'avg': {}}
    return n, ok, bad


# ---------------------------------------------------------------- club strength ratings (ClubElo, free) for Europe and cup games
ELO_URLS = ['http://api.clubelo.com/%s', 'https://api.clubelo.com/%s']
ELO_COUNTRY = {'E0': 'ENG', 'E1': 'ENG', 'E2': 'ENG', 'E3': 'ENG', 'SC0': 'SCO', 'SP1': 'ESP', 'SP2': 'ESP', 'I1': 'ITA', 'I2': 'ITA',
               'D1': 'GER', 'D2': 'GER', 'F1': 'FRA', 'F2': 'FRA', 'N1': 'NED', 'B1': 'BEL', 'P1': 'POR', 'T1': 'TUR', 'G1': 'GRE',
               'FAC': 'ENG', 'EFL': 'ENG', 'CDR': 'ESP', 'DFB': 'GER', 'CIT': 'ITA', 'CDF': 'FRA'}
# our best guess at how ClubElo spells some clubs (the matcher also tries the plain name and a close match)
ELO_ALIASES = {
    'manchester city': 'Man City', 'manchester united': 'Man United', 'tottenham hotspur': 'Tottenham', 'newcastle united': 'Newcastle',
    'wolverhampton wanderers': 'Wolves', 'nottingham forest': 'Forest', "nott'm forest": 'Forest', 'brighton hove albion': 'Brighton',
    'west ham united': 'West Ham', 'leeds united': 'Leeds', 'bayern munich': 'Bayern', 'bayer leverkusen': 'Leverkusen',
    'borussia dortmund': 'Dortmund', 'borussia monchengladbach': 'Gladbach', "m'gladbach": 'Gladbach', 'eintracht frankfurt': 'Frankfurt',
    'rb leipzig': 'RB Leipzig', 'paris saint germain': 'Paris SG', 'paris sg': 'Paris SG', 'olympique marseille': 'Marseille',
    'olympique lyonnais': 'Lyon', 'internazionale': 'Inter', 'inter milan': 'Inter', 'ac milan': 'Milan', 'atletico madrid': 'Atletico',
    'ath madrid': 'Atletico', 'athletic club': 'Bilbao', 'ath bilbao': 'Bilbao', 'real sociedad': 'Sociedad', 'celta vigo': 'Celta',
    'real betis': 'Betis', 'sporting cp': 'Sporting', 'sporting lisbon': 'Sporting', 'sp lisbon': 'Sporting', 'fc porto': 'Porto',
    'sl benfica': 'Benfica', 'club brugge': 'Brugge', 'red bull salzburg': 'Salzburg', 'shakhtar donetsk': 'Shakhtar',
    'dynamo kyiv': 'Dynamo Kyiv', 'red star belgrade': 'Crvena Zvezda', 'crvena zvezda': 'Crvena Zvezda', 'psv eindhoven': 'PSV',
    'young boys': 'Young Boys', 'slavia prague': 'Slavia Praha', 'sparta prague': 'Sparta Praha', 'viktoria plzen': 'Plzen',
    'ferencvaros': 'Ferencvaros', 'dinamo zagreb': 'Dinamo Zagreb', 'maccabi tel aviv': 'Maccabi Tel Aviv', 'qarabag': 'Qarabag',
    'olympiacos': 'Olympiacos', 'panathinaikos': 'Panathinaikos', 'galatasaray': 'Galatasaray', 'fenerbahce': 'Fenerbahce',
    'besiktas': 'Besiktas', 'celtic': 'Celtic', 'rangers': 'Rangers', 'ajax': 'Ajax', 'feyenoord': 'Feyenoord', 'union saint gilloise': 'St Gillis',
    'royale union saint gilloise': 'St Gillis',
    'stade rennais': 'Rennes', 'malmo ff': 'Malmoe', 'az alkmaar': 'AZ', 'heart of midlothian': 'Hearts', 'bodo glimt': 'Bodoe Glimt', 'bodo/glimt': 'Bodoe Glimt', 'fk bodo glimt': 'Bodoe Glimt',
}
ELO_ALIASES_N = {norm(k): v for k, v in ELO_ALIASES.items()}


def clubelo_load():
    """Reads today's ClubElo ratings (every European club on one scale). Falls back to the last saved copy."""
    cpath = os.path.join(DATA_DIR, 'elo.json')
    day = NOW.strftime('%Y-%m-%d')
    err = None
    for u in ELO_URLS:
        try:
            txt = fetch(u % day).decode('utf-8', 'replace')
            rows = list(csv.DictReader(io.StringIO(txt)))
            clubs = {}
            for r in rows:
                nm = (r.get('Club') or '').strip()
                try:
                    clubs[nm] = [round(float(r.get('Elo')), 1), (r.get('Country') or '').strip(), int(float(r.get('Level') or 0))]
                except (TypeError, ValueError):
                    continue
            if len(clubs) < 100:
                raise RuntimeError('only %d clubs in the file' % len(clubs))
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(cpath, 'w') as fh:
                json.dump({'asOf': day, 'clubs': clubs}, fh, separators=(',', ':'))
            return clubs, day, False
        except Exception as e:
            err = e
    if os.path.exists(cpath):
        with open(cpath) as fh:
            j = json.load(fh)
        return j.get('clubs', {}), j.get('asOf', '?'), True
    raise RuntimeError('could not read ClubElo (%s)' % err)


def elo_find(name, country, index, by_compact):
    """Finds a club in the ClubElo list from a name spelled some other way. Returns the ClubElo name or None."""
    n = norm(name)
    if not n:
        return None
    if n in index:
        return index[n][0]
    al = ELO_ALIASES_N.get(n)
    if al and norm(al) in index:
        return index[norm(al)][0]
    c = n.replace(' ', '')
    if c in by_compact:
        return by_compact[c]
    toks = set(n.split())
    cands = []
    for k, (orig, ctry) in index.items():
        kt = set(k.split())
        if k and len(k) >= 4 and (kt <= toks or toks <= kt) and min(len(kt), len(toks)) >= 1:
            cands.append((k, orig, ctry))
    if country:
        same = [x for x in cands if x[2] == country]
        if same:
            cands = same
    if len(cands) == 1:
        return cands[0][1]
    pool = [k for k, (orig, ctry) in index.items() if not country or ctry == country] or list(index)
    best = difflib.get_close_matches(n, pool, n=1, cutoff=0.84)
    if best:
        return index[best[0]][0]
    return None


def elo_model(eh, ea, base_total, home_pts, h1share=0.44):
    """Turns two club ratings into win, draw and goal chances. Simple and open: Elo gap -> expected result -> goals."""
    dr = eh + home_pts - ea
    we = 1.0 / (1.0 + 10 ** (-dr / 400.0))
    total = base_total          # real league average goals, no extra adjustment

    def split(x):
        lh, la = total * x, total * (1 - x)
        ph_, pa_ = pois_pmf(lh), pois_pmf(la)
        w = d = 0.0
        for i, p1 in enumerate(ph_):
            for j, p2 in enumerate(pa_):
                if i > j:
                    w += p1 * p2
                elif i == j:
                    d += p1 * p2
        return w + 0.5 * d, lh, la, ph_, pa_
    lo, hi = 0.03, 0.97
    for _ in range(40):
        mid = (lo + hi) / 2
        if split(mid)[0] < we:
            lo = mid
        else:
            hi = mid
    _, lh, la, ph_, pa_ = split((lo + hi) / 2)
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
    h1 = (lh + la) * h1share
    return {'lam': [round(lh, 3), round(la, 3)], 'p1x2': [round(ph, 4), round(pd, 4), round(pa, 4)], 'exp_total': round(lh + la, 2),
            'goals': {'o15': round(p_over(tot, 1.5), 4), 'o25': round(p_over(tot, 2.5), 4), 'o35': round(p_over(tot, 3.5), 4),
                      'btts': round((1 - ph_[0]) * (1 - pa_[0]), 4), 'h1o05': round(1 - math.exp(-h1), 4),
                      'h1o15': round(1 - math.exp(-h1) * (1 + h1), 4)},
            'corners': None, 'cards': None, 'n': [0, 0], 'conf': 'thin', 'basis': 'elo', 'elo': [round(eh), round(ea)]}


def add_elo_models(fixtures, teams, leagues, built):
    """Gives Europe and cup games chances from club strength ratings when the free stats files cannot (they only cover league games)."""
    clubs, asof, old = clubelo_load()
    index, by_compact = {}, {}
    for nm, (elo, ctry, lvl) in clubs.items():
        index[norm(nm)] = (nm, ctry)
        by_compact[norm(nm).replace(' ', '')] = nm
    cache, missing = {}, []

    def rating(key):
        if key in cache:
            return cache[key]
        t = teams.get(key) or {}
        code = key.split(':', 1)[0]
        ctry = ELO_COUNTRY.get(code, '')
        r = None
        for nm in (t.get('raw'), t.get('name'), key.split(':', 1)[-1]):
            if not nm:
                continue
            hit = elo_find(nm, ctry, index, by_compact)
            if hit:
                r = clubs[hit][0]
                break
        if r is None:
            missing.append(t.get('name') or key)
        else:
            t['elo'] = round(r)
        cache[key] = r
        return r
    # home advantage measured from real results: share of points the home team takes over all the leagues we read
    hw = [b['lg']['hw'] + 0.5 * b['lg']['dw'] for b in built.values() if b.get('lg', {}).get('hw') is not None]
    hdd = [b['lg'] for b in built.values() if b.get('lg', {}).get('hw') is not None]
    if not hw:
        raise RuntimeError('no league results to measure home advantage from')
    we_ = sum(hw) / len(hw)
    home_pts = 400.0 * math.log10(we_ / (1 - we_))
    h1 = sum(b['h1share'] for b in hdd) / len(hdd)
    done = tried = 0
    for f in fixtures:
        lg = leagues.get(f['league']) or {}
        if f.get('model') or lg.get('comp') != 'europe':
            continue
        tried += 1
        eh, ea = rating(f['home']), rating(f['away'])
        if eh is None or ea is None:
            continue
        gts = [built[k.split(':', 1)[0]]['lg']['g_team'] * 2 for k in (f['home'], f['away'])
               if k.split(':', 1)[0] in built and built[k.split(':', 1)[0]].get('lg', {}).get('g_team')]
        if not gts:
            gts = [b_['lg']['g_team'] * 2 for b_ in built.values() if b_.get('lg', {}).get('g_team')]
        if not gts:
            continue        # no real goals average to use: leave the game without chances
        base = sum(gts) / len(gts)
        f['model'] = elo_model(eh, ea, base, home_pts, h1)
        f['modelNote'] = 'elo'
        done += 1
    return done, tried, sorted(set(missing)), asof, old


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
                         'altitude': bool(hc and hc[0] == 'Jos'), 'venue': ev.get('venue')})
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
# ---------------------------------------------------------------- results (for the track record page)
def espn_results(slug, back=3, upto=0):
    """Finished matches of the last few days from the keyless ESPN scoreboard (goals only)."""
    d0 = (NOW - timedelta(days=back)).strftime('%Y%m%d')
    d1 = (NOW - timedelta(days=upto)).strftime('%Y%m%d')
    j = espn_board(slug, d0, d1)
    out = []
    for ev in j.get('events', []):
        comp = (ev.get('competitions') or [{}])[0]
        cs = comp.get('competitors') or []
        home = next((c for c in cs if c.get('homeAway') == 'home'), None)
        away = next((c for c in cs if c.get('homeAway') == 'away'), None)
        if not home or not away:
            continue
        if ((ev.get('status') or {}).get('type') or {}).get('state') != 'post':
            continue
        try:
            hg, ag = int(home.get('score')), int(away.get('score'))
        except (TypeError, ValueError):
            continue
        ko = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
        out.append({'id': str(ev['id']), 'ko': ko.astimezone(timezone.utc), 'h': home.get('team') or {}, 'a': away.get('team') or {}, 'hg': hg, 'ag': ag})
    return out


def build_results(built, prev_results):
    """Finished matches from the last 45 days: football-data.co.uk (full stats) plus ESPN (goals, faster)."""
    res = {}

    def rk(h, a, ko):
        return '%s|%s|%s' % (h, a, ko.strftime('%Y%m%d'))

    for code, B in built.items():
        for m in B.get('recent', []):
            k = rk('%s:%s' % (code, m['h']), '%s:%s' % (code, m['a']), m['d'])
            tc = total_cards(m)
            res[k] = {'id': '%s-%s-%s-%s' % (code, m['d'].strftime('%Y%m%d'), norm(m['h']).replace(' ', ''), norm(m['a']).replace(' ', '')),
                      'league': code, 'ko': m['d'].strftime('%Y-%m-%dT%H:%M:00Z'), 'home': '%s:%s' % (code, m['h']), 'away': '%s:%s' % (code, m['a']),
                      'hg': m['hg'], 'ag': m['ag'], 'hhg': m['hhg'], 'hag': m['hag'], 'hc': m['hc'], 'ac': m['ac'], 'cards': tc}
    all_names = {c: B['names'] for c, B in built.items()}
    for code, slug in ESPN_ALL.items():
        if code not in built:
            continue
        try:
            for ev in espn_results(slug):
                h, a = espn_name(ev['h'], all_names[code]), espn_name(ev['a'], all_names[code])
                if not h or not a:
                    continue
                k = rk('%s:%s' % (code, h), '%s:%s' % (code, a), ev['ko'])
                if k not in res:
                    res[k] = {'id': '%s-%s-%s-%s' % (code, ev['ko'].strftime('%Y%m%d'), norm(h).replace(' ', ''), norm(a).replace(' ', '')),
                              'league': code, 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'home': '%s:%s' % (code, h), 'away': '%s:%s' % (code, a),
                              'hg': ev['hg'], 'ag': ev['ag'], 'hhg': None, 'hag': None, 'hc': None, 'ac': None, 'cards': None}
        except Exception as e:
            NOTES.append('ESPN results %s: %s' % (slug, e))

    def key_for(team):
        for c, names in all_names.items():
            m = espn_name(team, names)
            if m:
                return '%s:%s' % (c, m)
        return 'X:' + (team.get('displayName') or team.get('name') or '?')
    try:
        for ev in espn_results(ESPN_CL):
            h, a = key_for(ev['h']), key_for(ev['a'])
            k = rk(h, a, ev['ko'])
            res[k] = {'id': 'CL-%s-%s' % (ev['ko'].strftime('%Y%m%d'), norm(ev['h'].get('displayName') or '?').replace(' ', '')),
                      'league': 'CL', 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'home': h, 'away': a, 'hg': ev['hg'], 'ag': ev['ag'],
                      'hhg': None, 'hag': None, 'hc': None, 'ac': None, 'cards': None}
    except Exception as e:
        NOTES.append('ESPN results CL: %s' % e)
    try:
        for ev in espn_results(ESPN_NPFL, back=5):
            h, a = 'NPFL:' + (ev['h'].get('displayName') or '?'), 'NPFL:' + (ev['a'].get('displayName') or '?')
            res[rk(h, a, ev['ko'])] = {'id': 'NPFL-%s' % ev['id'], 'league': 'NPFL', 'ko': ev['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'home': h, 'away': a,
                                       'hg': ev['hg'], 'ag': ev['ag'], 'hhg': None, 'hag': None, 'hc': None, 'ac': None, 'cards': None}
    except Exception as e:
        NOTES.append('ESPN results NPFL: %s' % e)
    # keep older results from the previous file so slow-to-settle picks can still be settled
    cutoff = (NOW - timedelta(days=45)).strftime('%Y-%m-%dT%H:%M:00Z')
    for r in prev_results:
        k = '%s|%s|%s' % (r.get('home'), r.get('away'), (r.get('ko') or '')[:10].replace('-', ''))
        if k not in res and (r.get('ko') or '') >= cutoff:
            res[k] = r
    return sorted(res.values(), key=lambda r: r['ko'])


# ---------------------------------------------------------------- weather (Open-Meteo, free, no key)
CLUBCITY = {
    'E0': {'arsenal': 'London', 'aston villa': 'Birmingham', 'brentford': 'London', 'chelsea': 'London', 'crystal palace': 'London',
           'everton': 'Liverpool', 'fulham': 'London', "nott'm forest": 'Nottingham', 'tottenham': 'London', 'west ham': 'London',
           'man city': 'Manchester', 'man united': 'Manchester', 'wolves': 'Wolverhampton', 'newcastle': 'Newcastle upon Tyne',
           'brighton': 'Brighton', 'bournemouth': 'Bournemouth', 'burnley': 'Burnley', 'leeds': 'Leeds', 'liverpool': 'Liverpool',
           'sunderland': 'Sunderland'},
    'SP1': {'alaves': 'Vitoria-Gasteiz', 'ath bilbao': 'Bilbao', 'ath madrid': 'Madrid', 'barcelona': 'Barcelona', 'betis': 'Seville',
            'celta': 'Vigo', 'elche': 'Elche', 'espanol': 'Barcelona', 'getafe': 'Getafe', 'girona': 'Girona', 'levante': 'Valencia',
            'mallorca': 'Palma', 'osasuna': 'Pamplona', 'oviedo': 'Oviedo', 'real madrid': 'Madrid', 'sevilla': 'Seville',
            'sociedad': 'San Sebastian', 'valencia': 'Valencia', 'vallecano': 'Madrid', 'villarreal': 'Villarreal'},
    'I1': {'atalanta': 'Bergamo', 'bologna': 'Bologna', 'cagliari': 'Cagliari', 'como': 'Como', 'cremonese': 'Cremona',
           'fiorentina': 'Florence', 'genoa': 'Genoa', 'inter': 'Milan', 'juventus': 'Turin', 'lazio': 'Rome', 'lecce': 'Lecce',
           'milan': 'Milan', 'napoli': 'Naples', 'parma': 'Parma', 'pisa': 'Pisa', 'roma': 'Rome', 'sassuolo': 'Sassuolo',
           'torino': 'Turin', 'udinese': 'Udine', 'verona': 'Verona'},
    'D1': {'augsburg': 'Augsburg', 'bayern munich': 'Munich', 'dortmund': 'Dortmund', 'ein frankfurt': 'Frankfurt', 'fc koln': 'Cologne',
           'freiburg': 'Freiburg', 'hamburg': 'Hamburg', 'heidenheim': 'Heidenheim', 'hoffenheim': 'Sinsheim', 'leverkusen': 'Leverkusen',
           "m'gladbach": 'Monchengladbach', 'mainz': 'Mainz', 'rb leipzig': 'Leipzig', 'st pauli': 'Hamburg', 'stuttgart': 'Stuttgart',
           'union berlin': 'Berlin', 'werder bremen': 'Bremen', 'wolfsburg': 'Wolfsburg'},
    'F1': {'angers': 'Angers', 'auxerre': 'Auxerre', 'brest': 'Brest', 'le havre': 'Le Havre', 'lens': 'Lens', 'lille': 'Lille',
           'lorient': 'Lorient', 'lyon': 'Lyon', 'marseille': 'Marseille', 'metz': 'Metz', 'monaco': 'Monaco', 'nantes': 'Nantes',
           'nice': 'Nice', 'paris fc': 'Paris', 'paris sg': 'Paris', 'rennes': 'Rennes', 'strasbourg': 'Strasbourg', 'toulouse': 'Toulouse'},
}
CCODE = {'E0': 'GB', 'SP1': 'ES', 'I1': 'IT', 'D1': 'DE', 'F1': 'FR', 'E1': 'GB', 'E2': 'GB', 'E3': 'GB', 'SC0': 'GB', 'D2': 'DE', 'I2': 'IT',
         'SP2': 'ES', 'F2': 'FR', 'N1': 'NL', 'B1': 'BE', 'P1': 'PT', 'T1': 'TR', 'G1': 'GR'}
for _c, _v in ESPN_LIST.items():
    if _v[3]:
        CCODE[_c] = _v[3]


def geocode(place, cc, cache):
    key = '%s|%s' % (place.lower(), cc or '')
    if key in cache:
        return cache[key]
    res = None
    try:
        j = json.loads(fetch('https://geocoding-api.open-meteo.com/v1/search?' + urlencode({'name': place, 'count': 8, 'language': 'en', 'format': 'json'}), tries=2, timeout=20))
        rs = j.get('results') or []
        if cc:
            rs = [r for r in rs if r.get('country_code') == cc]
        if rs:
            res = [round(rs[0]['latitude'], 3), round(rs[0]['longitude'], 3), rs[0].get('name') or place]
    except Exception as e:
        NOTES.append('geocode %s: %s' % (place, e))
        return None          # network problem: do not cache, try again next time
    cache[key] = res         # cache misses too, so we do not ask again every day
    if not LOCAL:
        time.sleep(0.25)
    return res


def venue_point(f, cache):
    lg = f['league']
    if lg == 'NPFL':
        hc = city_of(f['home'].split(':', 1)[1])
        return [hc[1], hc[2], hc[0]] if hc else None
    cc = CCODE.get(lg)
    place = (f.get('venue') or {}).get('c')
    if not place and lg in CLUBCITY:
        place = CLUBCITY[lg].get(f['home'].split(':', 1)[1].lower())
    if not place:
        place = f['home'].split(':', 1)[1]
    return geocode(place, cc, cache)


def add_weather(fixtures):
    gpath = os.path.join(DATA_DIR, 'geo.json')
    cache = {}
    if os.path.exists(gpath):
        try:
            with open(gpath) as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}
    horizon = NOW + timedelta(days=10)
    todo = {}
    ok = miss = 0
    for f in fixtures:
        if not f.get('koKnown'):
            continue
        ko = datetime.strptime(f['ko'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        if ko < NOW - timedelta(hours=3) or ko > horizon:
            continue
        pt = venue_point(f, cache)
        if not pt:
            miss += 1
            continue
        f['geo'] = pt
        todo.setdefault((pt[0], pt[1]), []).append((f, ko))
    for (la, lo), items in todo.items():
        try:
            j = json.loads(fetch('https://api.open-meteo.com/v1/forecast?' + urlencode({
                'latitude': la, 'longitude': lo, 'timezone': 'UTC', 'forecast_days': 11,
                'hourly': 'temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,wind_gusts_10m'}), tries=2, timeout=30))
            H = j['hourly']
            idx = {t: i for i, t in enumerate(H['time'])}
        except Exception as e:
            NOTES.append('weather %s,%s: %s' % (la, lo, e))
            miss += len(items)
            continue
        for f, ko in items:
            i = idx.get(ko.strftime('%Y-%m-%dT%H:00'))
            if i is None:
                miss += 1
                continue
            win = range(i, min(i + 3, len(H['time'])))
            g = lambda k: [H[k][n] for n in win if H[k][n] is not None]
            t, hu, pr, pp, gu = g('temperature_2m'), g('relative_humidity_2m'), g('precipitation'), g('precipitation_probability'), g('wind_gusts_10m')
            if not t:
                miss += 1
                continue
            f['wx'] = {'t': round(max(t), 1), 'h': round(max(hu)) if hu else None, 'rain': round(sum(pr), 1) if pr else 0,
                       'pp': round(max(pp)) if pp else None, 'gust': round(max(gu)) if gu else None,
                       'at': NOW.strftime('%Y-%m-%dT%H:%M:00Z')}
            ok += 1
    try:
        with open(gpath, 'w') as fh:
            json.dump(cache, fh, separators=(',', ':'), sort_keys=True)
    except Exception:
        pass
    return ok, miss


# ---------------------------------------------------------------- team news (ESPN injury lists, free)
def add_team_news(fixtures):
    ok = fail = 0
    for f in fixtures:
        es = f.get('espn')
        if not es or not f.get('koKnown') or ok + fail >= 150:
            continue
        ko = datetime.strptime(f['ko'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        if ko < NOW - timedelta(hours=1) or ko > NOW + timedelta(hours=60):
            continue
        try:
            j = json.loads(fetch('https://site.api.espn.com/apis/site/v2/sports/soccer/%s/summary?event=%s' % (es['slug'], es['id']), tries=2, timeout=25))
        except Exception as e:
            fail += 1
            NOTES.append('news %s: %s' % (f['id'], e))
            continue
        comp = ((j.get('header') or {}).get('competitions') or [{}])[0]
        names = {((c.get('team') or {}).get('displayName')) or '': c.get('homeAway') for c in comp.get('competitors') or []}
        inj = []
        for t in j.get('injuries') or []:
            sd = names.get(((t.get('team') or {}).get('displayName')) or '')
            if sd not in ('home', 'away'):
                continue
            for i in (t.get('injuries') or [])[:14]:
                a = i.get('athlete') or {}
                stt = i.get('status')
                stt = stt if isinstance(stt, str) else ((stt or {}).get('name') or (i.get('type') or {}).get('description') or '')
                inj.append({'s': sd, 'n': a.get('displayName') or a.get('shortName') or '?',
                            'p': ((a.get('position') or {}).get('abbreviation')) or '', 'st': stt})
        f['inj'] = inj
        f['injAt'] = NOW.strftime('%Y-%m-%dT%H:%M:00Z')
        ok += 1
        if not LOCAL:
            time.sleep(0.2)
    return ok, fail


# ---------------------------------------------------------------- usual teams: who starts, who scores (ESPN lineups of past matches)
SQ_WINDOW = 20       # last matches per team used to find regular starters and compare games with and without a player
SQ_MAX_NEW = 300     # most new match lineups fetched in one run (keeps the update fast; the rest comes next run)


def parse_lineup(j):
    comp = ((j.get('header') or {}).get('competitions') or [{}])[0]
    names = {((c.get('team') or {}).get('displayName')) or '': c.get('homeAway') for c in comp.get('competitors') or []}
    out = {'home': None, 'away': None}
    for ro in j.get('rosters') or []:
        sd = ro.get('homeAway')
        if sd not in out:
            continue
        xi, on = [], []
        for p in ro.get('roster') or []:
            a = p.get('athlete') or {}
            nm = a.get('displayName') or a.get('shortName')
            if not nm:
                continue
            pos = ((p.get('position') or {}).get('abbreviation')) or ''
            if p.get('starter'):
                xi.append([nm, pos])
            elif p.get('subbedIn'):
                on.append([nm, pos])
        if xi:
            out[sd] = {'f': ro.get('formation') or '', 'xi': xi, 'on': on}
    goals = []
    for e in j.get('keyEvents') or []:
        ty = (((e.get('type') or {}).get('text')) or ((e.get('type') or {}).get('type')) or '').lower()
        if not (e.get('scoringPlay') or 'goal' in ty) or 'own' in ty or 'disallow' in ty or 'no goal' in ty:
            continue
        sd = names.get(((e.get('team') or {}).get('displayName')) or '')
        ps = [((x.get('athlete') or {}).get('displayName')) for x in (e.get('participants') or [])]
        ps = [x for x in ps if x]
        if sd in ('home', 'away') and ps:
            goals.append([sd, ps[0], ps[1] if len(ps) > 1 else ''])
    return out, goals


def build_squads(built):
    cpath = os.path.join(DATA_DIR, 'lineups_cache.json')
    cache = {}
    if os.path.exists(cpath):
        try:
            with open(cpath) as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}
    cutoff = (NOW - timedelta(days=330)).strftime('%Y-%m-%d')
    cache = {k: v for k, v in cache.items() if v.get('d', '') >= cutoff}
    new = fails = 0
    jobs = []
    for code, slug in list(ESPN.items()) + [('NPFL', ESPN_NPFL)]:
        if code != 'NPFL' and code not in built:
            continue
        try:
            evs, seen_ids = [], set()
            for b0 in (300, 225, 150, 75):                 # ESPN returns limited rows per call, so ask in slices
                for ev in espn_results(slug, back=b0, upto=b0 - 76 if b0 > 75 else 0):
                    if ev['id'] not in seen_ids:
                        seen_ids.add(ev['id'])
                        evs.append(ev)
        except Exception as e:
            NOTES.append('squads %s: %s' % (slug, e))
            continue
        names = built[code]['names'] if code in built else None
        for ev in evs:
            if names is not None:
                h, a = espn_name(ev['h'], names), espn_name(ev['a'], names)
                if not h or not a:
                    continue
            else:
                h, a = ev['h'].get('displayName'), ev['a'].get('displayName')
                if not h or not a:
                    continue
            jobs.append((code, slug, ev, '%s:%s' % (code, h), '%s:%s' % (code, a)))
    jobs.sort(key=lambda x: x[2]['ko'], reverse=True)       # newest matches first
    for code, slug, ev, hk, ak in jobs:
        eid = '%s_%s' % (slug, ev['id'])
        if eid in cache:
            continue
        if new >= SQ_MAX_NEW:
            break
        try:
            j = json.loads(fetch('https://site.api.espn.com/apis/site/v2/sports/soccer/%s/summary?event=%s' % (slug, ev['id']), tries=2, timeout=25))
            lu, goals = parse_lineup(j)
        except Exception as e:
            fails += 1
            NOTES.append('lineup %s: %s' % (eid, e))
            continue
        if lu['home'] or lu['away']:
            cache[eid] = {'d': ev['ko'].strftime('%Y-%m-%d'), 'h': hk, 'a': ak, 'hg': ev['hg'], 'ag': ev['ag'], 'H': lu['home'], 'A': lu['away'], 'g': goals}
            new += 1
        else:
            cache[eid] = {'d': ev['ko'].strftime('%Y-%m-%d'), 'h': hk, 'a': ak, 'hg': ev['hg'], 'ag': ev['ag'], 'H': None, 'A': None, 'g': []}
        if not LOCAL:
            time.sleep(0.2)
    with open(cpath, 'w') as fh:
        json.dump(cache, fh, separators=(',', ':'))
    teams, meta = build_profiles(cache)
    return teams, meta, new, fails


# ---------------------------------------------------------------- what a missing regular starter really costs
SQ_K_CHOICES = (1, 2, 3, 5, 8, 15)
SQ_POOL_MIN = 25      # fewest league-wide cases before we trust a league-wide pattern
SQ_BT_MIN = 40        # fewest back-test cases before the back-test can switch a part off


def role_of(pos):
    p = (pos or '').upper()
    if p.startswith('G'):
        return 'gk'
    if re.match(r'(DM|CDM|HM)', p):
        return 'dm'
    if re.match(r'(CD|CB|LB|RB|SW|LWB|RWB|WB|D)', p):
        return 'df'
    if re.match(r'(CM|MC|MF|M$)', p):
        return 'cm'
    if re.match(r'(AM|CAM|LW|RW|W|LM|RM)', p):
        return 'cr'
    if re.match(r'(F|CF|ST|SS|FW)', p):
        return 'st'
    return ''


def build_profiles(cache):
    """Turns stored lineups into: who is a regular starter, how the team did with and without each one
    (adjusted for opponent strength and home or away), the league-wide pattern, and a back-test of the method."""
    W = SQ_WINDOW
    allm = []
    for v in cache.values():
        allm.append(v)
    lg_n, lg_h, lg_a = {}, {}, {}
    tgf, tga, tn = {}, {}, {}
    for v in allm:
        lg = v['h'].split(':', 1)[0]
        lg_n[lg] = lg_n.get(lg, 0) + 1
        lg_h[lg] = lg_h.get(lg, 0) + v['hg']
        lg_a[lg] = lg_a.get(lg, 0) + v['ag']
        for key, gf, ga in ((v['h'], v['hg'], v['ag']), (v['a'], v['ag'], v['hg'])):
            tgf[key] = tgf.get(key, 0) + gf
            tga[key] = tga.get(key, 0) + ga
            tn[key] = tn.get(key, 0) + 1
    LG = {}
    for lg, n in lg_n.items():
        if n >= 10 and lg_h[lg] + lg_a[lg] > 0:
            L = (lg_h[lg] + lg_a[lg]) / (2.0 * n)
            LG[lg] = (L, (lg_h[lg] / n) / L if lg_h[lg] else 1.0, (lg_a[lg] / n) / L if lg_a[lg] else 1.0)

    def clampr(x):
        return min(1.8, max(0.55, x))

    def adj(key, sd, gf, ga, opp):
        lg = key.split(':', 1)[0]
        if lg not in LG:
            return float(gf), float(ga)
        L, hm, am = LG[lg]
        ogs = (tga.get(opp, 0) + 5 * L) / (tn.get(opp, 0) + 5)
        ofs = (tgf.get(opp, 0) + 5 * L) / (tn.get(opp, 0) + 5)
        if sd == 'home':
            return gf / (hm * clampr(ogs / L)), ga / (am * clampr(ofs / L))
        return gf / (am * clampr(ogs / L)), ga / (hm * clampr(ofs / L))

    per = {}
    for eid, v in cache.items():
        for sd, key, gf, ga, lu, opp in (('home', v['h'], v['hg'], v['ag'], v.get('H'), v['a']), ('away', v['a'], v['ag'], v['hg'], v.get('A'), v['h'])):
            if not lu:
                continue
            agf, aga = adj(key, sd, gf, ga, opp)
            per.setdefault(key, []).append({'d': v['d'], 'eid': eid, 'gf': gf, 'ga': ga, 'agf': agf, 'aga': aga,
                                            'xi': {n: p for n, p in lu['xi']}, 'on': [n for n, p in lu['on']],
                                            'goals': [(g[1], g[2]) for g in (v.get('g') or []) if g[0] == sd]})
    for ms in per.values():
        ms.sort(key=lambda m: (m['d'], m['eid']))

    def window(ms, i):
        return ms[max(0, i - W):i]

    def regulars(w):
        c, pc = {}, {}
        for m in w:
            for nm, pos in m['xi'].items():
                c[nm] = c.get(nm, 0) + 1
                d = pc.setdefault(nm, {})
                d[pos] = d.get(pos, 0) + 1
        n = len(w)
        pos_of = {nm: max(d.items(), key=lambda x: x[1])[0] for nm, d in pc.items()}
        return {nm: k for nm, k in c.items() if k / n >= 0.6}, pos_of

    # every game where at least one regular starter was missing
    events = []
    for key, ms in per.items():
        for i in range(5, len(ms)):
            w = window(ms, i)
            regs, pos_of = regulars(w)
            absent = [nm for nm in regs if nm not in ms[i]['xi']]
            bg = sum(m['agf'] for m in w) / len(w)
            ba = sum(m['aga'] for m in w) / len(w)
            if absent and bg > 0 and ba > 0:
                events.append({'key': key, 'i': i, 'd': ms[i]['d'], 'absent': absent, 'pos_of': pos_of, 'agf': ms[i]['agf'], 'aga': ms[i]['aga'], 'bg': bg, 'ba': ba})

    def pool_of(group):
        n = len(group)
        if n < SQ_POOL_MIN:
            return {'att': 1.0, 'def': 1.0, 'n': n, 'ok': False}
        return {'att': round(sum(e['agf'] for e in group) / sum(e['bg'] for e in group), 3),
                'def': round(sum(e['aga'] for e in group) / sum(e['ba'] for e in group), 3), 'n': n, 'ok': True}

    pool1 = {}
    for role in ('gk', 'df', 'dm', 'cm', 'cr', 'st'):
        pool1[role] = pool_of([e for e in events if len(e['absent']) == 1 and role_of(e['pos_of'][e['absent'][0]]) == role])
    poolK = {'2': pool_of([e for e in events if len(e['absent']) == 2]), '3': pool_of([e for e in events if len(e['absent']) >= 3])}
    # (pool1 and poolK above use every game: they are for today's predictions. The tests below never use them.)

    def own(ms, i, name, K, pa, pd):
        w = window(ms, i)
        wi = [m for m in w if name in m['xi']]
        oi = [m for m in w if name not in m['xi']]
        if not wi:
            return None
        if not oi:
            return pa, pd, 1
        wgf = sum(m['agf'] for m in wi) / len(wi)
        wga = sum(m['aga'] for m in wi) / len(wi)
        n = len(oi)
        att = (sum(m['agf'] for m in oi) / wgf + K * pa) / (n + K) if wgf > 0 else pa
        dfn = (sum(m['aga'] for m in oi) / wga + K * pd) / (n + K) if wga > 0 else pd
        return min(1.8, max(0.4, att)), min(1.8, max(0.4, dfn)), 0

    def run_pool(sums):
        if sums['n'] < SQ_POOL_MIN or sums['bg'] <= 0 or sums['ba'] <= 0:
            return 1.0, 1.0
        return sums['agf'] / sums['bg'], sums['aga'] / sums['ba']

    # Honest back-test, game by game in date order. Every game is predicted using ONLY games played on earlier dates
    # (league-wide pattern and the team's own record). The first 60% of games choose the settings (K, and whether to use
    # the attack and defence parts); the last 40% is a test no setting was chosen on.
    ordered = sorted(events, key=lambda e: (e['d'], e['key']))
    sums = {}                                   # running league-wide totals: role / 'k2' / 'k3'
    per_event = []                              # (event, group, pool att, pool def) known before that game
    k = 0
    while k < len(ordered):
        day = str(ordered[k]['d'])[:10]
        batch = []
        while k < len(ordered) and str(ordered[k]['d'])[:10] == day:
            batch.append(ordered[k]); k += 1
        for e in batch:                          # predict first (only earlier days are in sums) ...
            n_abs = len(e['absent'])
            grp = None
            if n_abs == 1:
                r = role_of(e['pos_of'][e['absent'][0]])
                grp = r
            elif n_abs == 2:
                grp = 'k2'
            elif n_abs >= 3:
                grp = 'k3'
            if grp:
                pa_, pd_ = run_pool(sums.get(grp, {'n': 0, 'agf': 0, 'bg': 0, 'aga': 0, 'ba': 0}))
                per_event.append((e, grp, pa_, pd_))
        for e in batch:                          # ... then add these games to what is known
            n_abs = len(e['absent'])
            grp = role_of(e['pos_of'][e['absent'][0]]) if n_abs == 1 else ('k2' if n_abs == 2 else 'k3')
            if grp:
                t = sums.setdefault(grp, {'n': 0, 'agf': 0.0, 'bg': 0.0, 'aga': 0.0, 'ba': 0.0})
                t['n'] += 1; t['agf'] += e['agf']; t['bg'] += e['bg']; t['aga'] += e['aga']; t['ba'] += e['ba']
    single_ev = [x for x in per_event if x[1] not in ('k2', 'k3')]
    cut = int(len(single_ev) * 0.6)
    parts = {'val': single_ev[:cut], 'test': single_ev[cut:]}

    def score(items, K):
        ea = eb = da = db = 0.0
        n = 0
        for e, grp, pa_, pd_ in items:
            nm = e['absent'][0]
            f = (pa_, pd_, 1) if K is None else own(per[e['key']], e['i'], nm, K, pa_, pd_)
            if not f:
                continue
            n += 1
            ea += (e['agf'] - e['bg'] * f[0]) ** 2
            eb += (e['agf'] - e['bg']) ** 2
            da += (e['aga'] - e['ba'] * f[1]) ** 2
            db += (e['aga'] - e['ba']) ** 2
        return {'n': n, 'impAtt': 1 - ea / eb if (n and eb) else 0.0, 'impDef': 1 - da / db if (n and db) else 0.0}
    bt = {K_: score(parts['val'], K_) for K_ in SQ_K_CHOICES + (None,)}
    best = None
    for K_ in SQ_K_CHOICES:
        if bt[K_]['n'] and (best is None or bt[K_]['impAtt'] + bt[K_]['impDef'] > bt[best]['impAtt'] + bt[best]['impDef']):
            best = K_
    K = best if best is not None else 3
    val = bt.get(best) or {'n': 0, 'impAtt': 0.0, 'impDef': 0.0}
    enough = val['n'] >= SQ_BT_MIN
    use_att = (val['impAtt'] > 0) if enough else True
    use_def = (val['impDef'] > 0) if enough else True
    cur = score(parts['test'], best) if best is not None else {'n': 0, 'impAtt': 0.0, 'impDef': 0.0}
    cur_pool = score(parts['test'], None)
    mm = [x for x in per_event if x[1] in ('k2', 'k3')]
    mm = mm[int(len(mm) * 0.6):]
    ma = mb = md = mc = 0.0
    for e, grp, pa_, pd_ in mm:
        ma += (e['agf'] - e['bg'] * pa_) ** 2
        mb += (e['agf'] - e['bg']) ** 2
        md += (e['aga'] - e['ba'] * pd_) ** 2
        mc += (e['aga'] - e['ba']) ** 2
    meta = {'window': W, 'K': K, 'pool1': pool1, 'poolK': poolK,
            'bt': {'n': cur['n'], 'impAtt': round(cur['impAtt'], 4), 'impDef': round(cur['impDef'], 4), 'enough': enough, 'valN': val['n'],
                   'useAtt': use_att, 'useDef': use_def,
                   'poolOnly': {'impAtt': round(cur_pool['impAtt'], 4), 'impDef': round(cur_pool['impDef'], 4)} if cur_pool['n'] else None,
                   'multi': {'n': len(mm), 'impAtt': round(1 - ma / mb, 4) if mb else 0.0, 'impDef': round(1 - md / mc, 4) if mc else 0.0},
                   'byK': {str(k_): [bt[k_]['n'], round(bt[k_]['impAtt'], 4), round(bt[k_]['impDef'], 4)] for k_ in SQ_K_CHOICES if bt[k_]['n']}}}
    # current profiles
    teams = {}
    for key, ms in per.items():
        if len(ms) < 5:
            continue
        i = len(ms)
        w = window(ms, i)
        n = len(w)
        starts, apps, pcnt, gl, asst = {}, {}, {}, {}, {}
        forms = {}
        for m in w:
            for nm, pos in m['xi'].items():
                starts[nm] = starts.get(nm, 0) + 1
                apps[nm] = apps.get(nm, 0) + 1
                d = pcnt.setdefault(nm, {})
                d[pos] = d.get(pos, 0) + 1
            for nm in m['on']:
                apps[nm] = apps.get(nm, 0) + 1
            for sc, a_ in m['goals']:
                gl[sc] = gl.get(sc, 0) + 1
                if a_:
                    asst[a_] = asst.get(a_, 0) + 1
        for eid in [m['eid'] for m in w]:
            v = cache[eid]
            f = (v.get('H') if v['h'] == key else v.get('A')) or {}
            if f.get('f'):
                forms[f['f']] = forms.get(f['f'], 0) + 1
        rows = []
        for nm, st in starts.items():
            pos = max(pcnt[nm].items(), key=lambda x: x[1])[0]
            r = role_of(pos)
            p = pool1.get(r) or {'att': 1.0, 'def': 1.0}
            wi = [m for m in w if nm in m['xi']]
            oi = [m for m in w if nm not in m['xi']]
            f = own(ms, i, nm, K, p['att'], p['def']) or (1.0, 1.0, 1)
            att, dfn = (f[0] if use_att else 1.0), (f[1] if use_def else 1.0)
            av = lambda lst, k: round(sum(m[k] for m in lst) / len(lst), 2) if lst else None
            rows.append([nm, pos, st, apps.get(nm, st), gl.get(nm, 0), asst.get(nm, 0), len(oi),
                         av(wi, 'gf'), av(oi, 'gf'), av(wi, 'ga'), av(oi, 'ga'),
                         av(wi, 'agf'), av(oi, 'agf'), av(wi, 'aga'), av(oi, 'aga'), round(att, 3), round(dfn, 3), f[2]])
        rows.sort(key=lambda r: (-r[2], -r[3], -r[4]))
        usual = max(forms.items(), key=lambda x: x[1])[0] if forms else ''
        teams[key] = {'n': n, 'gf': sum(m['gf'] for m in w), 'ga': sum(m['ga'] for m in w), 'f': usual, 'p': rows[:26]}
    return teams, meta




# ---------------------------------------------------------------- our own saved season of results (for competitions with no free stats file)
ARCH_FILE = 'archive.dat'      # not .json on purpose: it stays in the repository but is not published on the website
ARCH_BACK_DAYS = 330           # how far back we keep and use games
ARCH_STEP_DAYS = 60            # how far back we read per run until the whole season is saved
ARCH_MAX_REQUESTS = 400


def arch_slugs():
    out = {c: v[0] for c, v in ESPN_LIST.items()}
    out['CL'] = ESPN_CL
    return out


def arch_range(slug, d0, d1):
    """Finished games between two dates, read 7 days at a time so nothing is cut off. Returns (games, requests made)."""
    base = ESPN_BASE % slug
    a0 = datetime.strptime(d0, '%Y%m%d')
    span = (datetime.strptime(d1, '%Y%m%d') - a0).days
    games, k, reqs = [], 0, 0
    while k <= span:
        c0 = a0 + timedelta(days=k)
        c1 = a0 + timedelta(days=min(span, k + 6))
        j = json.loads(fetch(base + '?' + urlencode({'dates': '%s-%s' % (c0.strftime('%Y%m%d'), c1.strftime('%Y%m%d'))}), tries=2, timeout=30))
        reqs += 1
        for ev in j.get('events', []):
            comp = (ev.get('competitions') or [{}])[0]
            cs = comp.get('competitors') or []
            home = next((c for c in cs if c.get('homeAway') == 'home'), None)
            away = next((c for c in cs if c.get('homeAway') == 'away'), None)
            if not home or not away or ((ev.get('status') or {}).get('type') or {}).get('state') != 'post':
                continue
            try:
                hg, ag = int(home.get('score')), int(away.get('score'))
                ko = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
            except (TypeError, ValueError, KeyError):
                continue

            def first_half(c):
                ls = c.get('linescores') or []
                try:
                    return int(float(ls[0].get('value'))) if len(ls) >= 2 else None
                except (TypeError, ValueError, AttributeError):
                    return None
            th, ta = home.get('team') or {}, away.get('team') or {}
            games.append([str(ev['id']), ko.strftime('%Y-%m-%d'), th.get('displayName') or th.get('name') or '?', ta.get('displayName') or ta.get('name') or '?',
                          hg, ag, first_half(home), first_half(away)])
        k += 7
    return games, reqs


def archive_update():
    path = os.path.join(DATA_DIR, ARCH_FILE)
    arch = {'v': 1, 'games': {}, 'from': {}}
    if os.path.exists(path):
        try:
            with open(path) as fh:
                arch = json.load(fh)
        except Exception:
            pass
    today = NOW.strftime('%Y%m%d')
    target = (NOW - timedelta(days=ARCH_BACK_DAYS)).strftime('%Y%m%d')
    reqs, errs, added = 0, 0, 0
    for code, slug in arch_slugs().items():
        try:
            gs, r1 = arch_range(slug, (NOW - timedelta(days=4)).strftime('%Y%m%d'), today)       # newest games again (late corrections)
            reqs += r1
            frm = arch['from'].get(slug, today)
            if frm > target and reqs < ARCH_MAX_REQUESTS:
                start = max(datetime.strptime(target, '%Y%m%d'), datetime.strptime(frm, '%Y%m%d') - timedelta(days=ARCH_STEP_DAYS))
                g2, r2 = arch_range(slug, start.strftime('%Y%m%d'), frm)
                reqs += r2
                gs += g2
                arch['from'][slug] = start.strftime('%Y%m%d')
            elif slug not in arch['from']:
                arch['from'][slug] = today
            for g in gs:
                key = '%s|%s' % (slug, g[0])
                if key not in arch['games']:
                    added += 1
                arch['games'][key] = [code] + g[1:]
        except Exception as e:
            errs += 1
            NOTES.append('saved results %s: %s' % (slug, e))
    cut = (NOW - timedelta(days=ARCH_BACK_DAYS + 70)).strftime('%Y-%m-%d')
    arch['games'] = {k: v for k, v in arch['games'].items() if v[1] >= cut}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(arch, fh, separators=(',', ':'))
    full = sum(1 for s_, f_ in arch['from'].items() if f_ <= target)
    return arch, added, errs, full, len(arch['from'])


def archive_models(arch, fixtures, teams, leagues):
    """Chances for games in competitions with no free stats file, from the results we saved ourselves (goals only)."""
    by = {}
    cut = (NOW - timedelta(days=ARCH_BACK_DAYS)).strftime('%Y-%m-%d')
    for v in arch['games'].values():
        if v[1] >= cut:
            by.setdefault(v[0], []).append({'d': datetime.strptime(v[1], '%Y-%m-%d').replace(tzinfo=timezone.utc), 'h': v[2], 'a': v[3], 'hg': v[4], 'ag': v[5],
                                            'hhg': v[6], 'hag': v[7], 'hc': None, 'ac': None, 'hy': None, 'ay': None, 'hr': None, 'ar': None, 'hst': None, 'ast': None, 'ref': None})
    made, leagues_used = 0, set()
    for code, ms in by.items():
        lg_meta = leagues.get(code) or {}
        if lg_meta.get('comp') == 'europe':
            continue                    # Europe and cup games use the club strength ratings, which compare clubs across leagues
        if len(ms) < 40:
            continue
        lg = league_avgs(ms)
        by_team = {}
        for m in ms:
            by_team.setdefault(m['h'], []).append(m)
            by_team.setdefault(m['a'], []).append(m)
        gv = {t: [(m['hg'] if m['h'] == t else m['ag']) for m in l] for t, l in by_team.items()}
        av = {t: [(m['ag'] if m['h'] == t else m['hg']) for m in l] for t, l in by_team.items()}
        ks = [x for x in (est_k(gv), est_k(av)) if x]
        lg['k'] = {'g': round(sum(ks) / len(ks), 2)} if ks else {}
        prof = {t: profile(sorted(l, key=lambda m: m['d'], reverse=True)[:WINDOW], t) for t, l in by_team.items()}
        for f in fixtures:
            if f['league'] != code or f.get('model'):
                continue
            hn, an = (teams.get(f['home']) or {}).get('raw'), (teams.get(f['away']) or {}).get('raw')
            H, A = prof.get(hn), prof.get(an)
            if not H or not A or H['n'] < 4 or A['n'] < 4:
                continue
            f['model'] = predict(H, A, lg, None)
            f['model']['basis'] = 'archive'
            f['modelNote'] = 'archive'
            for key, P in ((f['home'], H), (f['away'], A)):
                if teams.get(key) is not None and not teams[key].get('stats'):
                    teams[key]['stats'] = P
            made += 1
            leagues_used.add(code)
            if lg_meta.get('avg') == {} or not lg_meta.get('avg'):
                lg_meta['avg'] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in lg.items() if k != 'k' and v is not None}
    return made, len(leagues_used)


# ---------------------------------------------------------------- basketball: real scores from ESPN, our own ratings worked out from them
# slug, name, short name, days per request, extra request options, days of past results to read
BB_LEAGUES = {
    'nba': ('nba', 'NBA', 'NBA', 7, {'limit': 300}, 120),
    'wnba': ('wnba', 'WNBA', 'WNBA', 7, {'limit': 300}, 120),
    'mcb': ('mens-college-basketball', "Men's college basketball", 'College men', 1, {'groups': '50', 'limit': 400}, 45),
    'wcb': ('womens-college-basketball', "Women's college basketball", 'College women', 1, {'groups': '50', 'limit': 400}, 45),
}


def bb_events(slug, d0, d1, step, extra):
    """All ESPN basketball games between two dates, read in small chunks so nothing is cut off."""
    base = 'https://site.api.espn.com/apis/site/v2/sports/basketball/%s/scoreboard' % slug
    a0 = datetime.strptime(d0, '%Y%m%d')
    span = (datetime.strptime(d1, '%Y%m%d') - a0).days
    seen, out, k = set(), [], 0
    while k <= span:
        c0 = a0 + timedelta(days=k)
        c1 = a0 + timedelta(days=min(span, k + step - 1))
        dd = c0.strftime('%Y%m%d') if step == 1 else '%s-%s' % (c0.strftime('%Y%m%d'), c1.strftime('%Y%m%d'))
        q = dict(extra)
        q['dates'] = dd
        j = json.loads(fetch(base + '?' + urlencode(q), tries=2, timeout=30))
        for ev in j.get('events', []):
            if ev.get('id') not in seen:
                seen.add(ev.get('id'))
                out.append(ev)
        k += step
    return out


def bb_parse(ev):
    comp = (ev.get('competitions') or [{}])[0]
    cs = comp.get('competitors') or []
    home = next((c for c in cs if c.get('homeAway') == 'home'), None)
    away = next((c for c in cs if c.get('homeAway') == 'away'), None)
    if not home or not away:
        return None
    st = (ev.get('status') or {}).get('type') or {}

    def tm(c):
        t = c.get('team') or {}
        return {'id': str(t.get('id') or ''), 'name': t.get('displayName') or t.get('name') or '?', 'abbr': t.get('abbreviation') or ''}

    def sc(c):
        try:
            return int(float(c.get('score')))
        except (TypeError, ValueError):
            return None
    try:
        ko = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
    except Exception:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=timezone.utc)
    ven = comp.get('venue') or {}
    h, a = tm(home), tm(away)
    if not h['id'] or not a['id']:
        return None
    return {'id': str(ev.get('id')), 'ko': ko.astimezone(timezone.utc), 'state': st.get('state'), 'h': h, 'a': a,
            'sh': sc(home), 'sa': sc(away), 'neutral': bool(comp.get('neutralSite')), 'when': st.get('shortDetail') or '',
            'venue': {'n': ven.get('fullName'), 'c': (ven.get('address') or {}).get('city')}}


def bb_fit(games, lam, iters=60):
    """Ratings from real scores. Points scored = league average + home edge + the team's attack + the other team's weakness at defence.
    Each rating is pulled toward zero by lam games' worth, so a team with few games does not swing wildly."""
    teams = set()
    for g in games:
        teams.add(g[0]); teams.add(g[1])
    n = float(len(games))
    mu = sum(g[2] + g[3] for g in games) / (2 * n)
    non = [g for g in games if not g[4]]
    hca = sum(g[2] - g[3] for g in non) / len(non) if non else 0.0
    off = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}
    for _ in range(iters):
        num, cnt = {t: 0.0 for t in teams}, {t: 0 for t in teams}
        for H, A, sh, sa, neu in games:
            h = 0.0 if neu else hca
            num[H] += sh - (mu + h / 2 + dfn[A]); cnt[H] += 1
            num[A] += sa - (mu - h / 2 + dfn[H]); cnt[A] += 1
        off = {t: num[t] / (cnt[t] + lam) for t in teams}
        num, cnt = {t: 0.0 for t in teams}, {t: 0 for t in teams}
        for H, A, sh, sa, neu in games:
            h = 0.0 if neu else hca
            num[A] += sh - (mu + h / 2 + off[H]); cnt[A] += 1
            num[H] += sa - (mu - h / 2 + off[A]); cnt[H] += 1
        dfn = {t: num[t] / (cnt[t] + lam) for t in teams}
        if non:
            hca = sum((g[2] - g[3]) - (off[g[0]] + dfn[g[1]] - off[g[1]] - dfn[g[0]]) for g in non) / len(non)
    return {'mu': mu, 'hca': hca, 'off': off, 'dfn': dfn, 'lam': lam}


def bb_pred(fit, H, A, neutral=False):
    off, dfn, mu = fit['off'], fit['dfn'], fit['mu']
    h = 0.0 if neutral else fit['hca']
    ph = mu + h / 2 + off.get(H, 0.0) + dfn.get(A, 0.0)
    pa = mu - h / 2 + off.get(A, 0.0) + dfn.get(H, 0.0)
    return ph, pa


def bb_norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bb_league_model(games):
    """Picks the rating strength by testing on the newest quarter of games, and measures how far real results land from our guesses."""
    games = sorted(games, key=lambda g: g[5])
    G = [g[:5] for g in games]
    N = len(G)
    margins = [g[2] - g[3] for g in G]
    totals = [g[2] + g[3] for g in G]

    def sd(v):
        m = sum(v) / len(v)
        return math.sqrt(sum((x - m) ** 2 for x in v) / max(1, len(v) - 1))
    info = {'n': N, 'lam': 8.0, 'lamChosen': False, 'back': None}
    sm, st = sd(margins), sd(totals)             # raw spread of real results (used when there are too few games to test)
    if N >= 150:
        c1, c2 = int(N * 0.6), int(N * 0.8)
        train, val, test = G[:c1], G[c1:c2], G[c2:]
        best = None
        for lam in (2.0, 4.0, 8.0, 16.0, 32.0):          # the rating strength is chosen on the validation games only
            f = bb_fit(train, lam)
            err = sum(((sh - sa) - (bb_pred(f, H, A, neu)[0] - bb_pred(f, H, A, neu)[1])) ** 2 for H, A, sh, sa, neu in val) / len(val)
            if best is None or err < best[0]:
                best = (err, lam, f)
        _, lam, f = best
        info['lam'], info['lamChosen'] = lam, True
        res_m, res_t = [], []
        for H, A, sh, sa, neu in val:                    # how far real results land from our guesses, measured on validation games
            ph, pa = bb_pred(f, H, A, neu)
            res_m.append((sh - sa) - (ph - pa)); res_t.append((sh + sa) - (ph + pa))
        sm, st = math.sqrt(sum(x * x for x in res_m) / len(res_m)), math.sqrt(sum(x * x for x in res_t) / len(res_t))
        f2 = bb_fit(train + val, lam)                    # the test games were not used for anything above
        right = homew = inm = int_ = 0
        brier = 0.0
        for H, A, sh, sa, neu in test:
            ph, pa = bb_pred(f2, H, A, neu)
            p = bb_norm_cdf((ph - pa) / sm)
            won = 1.0 if sh > sa else 0.0
            right += 1 if (p >= 0.5) == (sh > sa) else 0
            brier += (p - won) ** 2
            if not neu and sh > sa:
                homew += 1
            inm += 1 if abs((sh - sa) - (ph - pa)) <= sm else 0
            int_ += 1 if abs((sh + sa) - (ph + pa)) <= st else 0
        info['back'] = {'n': len(test), 'right': right, 'acc': round(right / len(test), 3), 'brier': round(brier / len(test), 4),
                        'homeAcc': round(homew / max(1, len([g for g in test if not g[4]])), 3),
                        'covM': round(inm / len(test), 3), 'covT': round(int_ / len(test), 3)}
    info['sm'], info['st'] = round(sm, 2), round(st, 2)
    fit = bb_fit(G, info['lam'])
    info['mu'], info['hca'] = round(fit['mu'], 2), round(fit['hca'], 2)
    return fit, info


def bb_injuries(slug, gid):
    j = json.loads(fetch('https://site.api.espn.com/apis/site/v2/sports/basketball/%s/summary?event=%s' % (slug, gid), tries=2, timeout=25))
    comp = ((j.get('header') or {}).get('competitions') or [{}])[0]
    names = {str(((c.get('team') or {}).get('id')) or ''): c.get('homeAway') for c in comp.get('competitors') or []}
    inj = []
    for t in j.get('injuries') or []:
        sd = names.get(str(((t.get('team') or {}).get('id')) or ''))
        if sd not in ('home', 'away'):
            continue
        for i in (t.get('injuries') or [])[:12]:
            a = i.get('athlete') or {}
            stt = i.get('status')
            stt = stt if isinstance(stt, str) else ((stt or {}).get('name') or (i.get('type') or {}).get('description') or '')
            inj.append({'s': sd, 'n': a.get('displayName') or a.get('shortName') or '?', 'p': (a.get('position') or {}).get('abbreviation') or '', 'st': stt})
    return inj


def basketball_update():
    """Basketball games and our own numbers for them. Everything comes from real ESPN scores; nothing is typed in by hand."""
    d1 = (NOW + timedelta(days=7)).strftime('%Y%m%d')
    leagues, teams, games, notes = {}, {}, [], []
    inj_budget = 40
    for key, (slug, name, short, step, extra, back_days) in BB_LEAGUES.items():
        try:
            d0 = (NOW - timedelta(days=back_days)).strftime('%Y%m%d')
            evs = [x for x in (bb_parse(e) for e in bb_events(slug, d0, d1, step, extra)) if x]
        except Exception as e:
            notes.append('%s: could not read ESPN (%s)' % (short, e))
            continue
        done = [x for x in evs if x['state'] == 'post' and x['sh'] is not None and x['sa'] is not None and x['sh'] + x['sa'] > 0]
        soon = [x for x in evs if x['state'] in ('pre', 'in') and x['ko'] <= NOW + timedelta(days=7)]
        recent_done = [x for x in done if x['ko'] >= NOW - timedelta(days=1)]
        if len(done) < 20:
            notes.append('%s: only %d finished games found, so no numbers yet' % (short, len(done)))
            if not soon and not recent_done:
                continue
            fit, info = None, {'n': len(done)}
        else:
            fit, info = bb_league_model([(x['h']['id'], x['a']['id'], x['sh'], x['sa'], x['neutral'], x['ko']) for x in done])
        rec = {}
        for x in sorted(done, key=lambda x: x['ko']):
            for side, tmx, own, opp in (('h', x['h'], x['sh'], x['sa']), ('a', x['a'], x['sa'], x['sh'])):
                r = rec.setdefault(tmx['id'], {'name': tmx['name'], 'abbr': tmx['abbr'], 'n': 0, 'w': 0, 'l': 0, 'pf': 0, 'pa': 0, 'form': []})
                r['n'] += 1; r['pf'] += own; r['pa'] += opp
                if own > opp:
                    r['w'] += 1
                else:
                    r['l'] += 1
                r['form'].append('W' if own > opp else 'L')
        for x in soon + recent_done:
            for tmx in (x['h'], x['a']):
                rec.setdefault(tmx['id'], {'name': tmx['name'], 'abbr': tmx['abbr'], 'n': 0, 'w': 0, 'l': 0, 'pf': 0, 'pa': 0, 'form': []})
        for tid, r in rec.items():
            t = {'name': r['name'], 'abbr': r['abbr'], 'n': r['n'], 'w': r['w'], 'l': r['l'], 'form': r['form'][-5:]}
            if r['n']:
                t['ppg'], t['oppg'] = round(r['pf'] / r['n'], 1), round(r['pa'] / r['n'], 1)
            if fit and r['n']:
                t['att'], t['def'] = round(fit['off'].get(tid, 0.0), 2), round(fit['dfn'].get(tid, 0.0), 2)
            teams['%s:%s' % (key, tid)] = t
        leagues[key] = dict(info, name=name, short=short, slug=slug)
        for x in sorted(soon + recent_done, key=lambda x: x['ko']):
            g = {'id': '%s-%s' % (key, x['id']), 'lg': key, 'ko': x['ko'].strftime('%Y-%m-%dT%H:%M:00Z'), 'home': '%s:%s' % (key, x['h']['id']),
                 'away': '%s:%s' % (key, x['a']['id']), 'state': x['state'], 'neutral': x['neutral'], 'venue': x['venue'], 'espn': {'slug': slug, 'id': x['id']}}
            if x['state'] in ('in', 'post'):
                g['score'] = [x['sh'], x['sa']]
                g['when'] = x['when']
            if fit and x['state'] == 'pre':
                nh, na = rec.get(x['h']['id'], {}).get('n', 0), rec.get(x['a']['id'], {}).get('n', 0)
                if nh >= 1 and na >= 1:
                    ph, pa = bb_pred(fit, x['h']['id'], x['a']['id'], x['neutral'])
                    g['pred'] = {'h': round(ph, 1), 'a': round(pa, 1), 'margin': round(ph - pa, 1), 'total': round(ph + pa, 1),
                                 'pw': round(bb_norm_cdf((ph - pa) / info['sm']), 4), 'n': [nh, na]}
            if key in ('nba', 'wnba') and x['state'] == 'pre' and x['ko'] <= NOW + timedelta(hours=48) and inj_budget > 0:
                inj_budget -= 1
                try:
                    g['inj'] = bb_injuries(slug, x['id'])
                    g['injAt'] = NOW.strftime('%Y-%m-%dT%H:%M:00Z')
                except Exception as e:
                    notes.append('injuries %s: %s' % (g['id'], e))
            games.append(g)
    if not games and not leagues:
        raise RuntimeError('no basketball data could be read' + ('; ' + notes[0] if notes else ''))
    return {'version': 1, 'asOf': NOW.strftime('%Y-%m-%dT%H:%M:00Z'), 'leagues': leagues, 'teams': teams, 'games': games, 'notes': notes[:12]}


def write_basketball(prev_path):
    try:
        out = basketball_update()
        with open(prev_path, 'w') as fh:
            json.dump(out, fh, separators=(',', ':'))
        STATUS['basketball (ESPN scores)'] = 'ok: %d games in %d leagues%s' % (len(out['games']), len(out['leagues']), (' (' + '; '.join(out['notes'][:3]) + ')') if out['notes'] else '')
    except Exception as e:
        STATUS['basketball (ESPN scores)'] = 'FAILED: %s (the football part still works)' % e


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
                                 'ref': None, 'mkt': None, 'ou': None, 'ah': None, 'model': None,
                                 'venue': c.get('venue')})
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

    for k_, v_ in LISTONLY_TEAMS.items():
        teams.setdefault(k_, v_)
    try:
        n_o, ok_o, bad_o = espn_other(built, teams, leagues, fixtures)
        STATUS['ESPN (other competitions)'] = 'ok: %d more fixtures from %d competitions (%d had no feed right now)' % (n_o, ok_o, bad_o)
    except Exception as e:
        STATUS['ESPN (other competitions)'] = 'FAILED: %s' % e

    try:
        arch_, add_, err_, full_, nsl_ = archive_update()
        mk_, nl_ = archive_models(arch_, fixtures, teams, leagues)
        STATUS['saved season results'] = 'ok: %d games saved (%d new this run). Whole season saved for %d of %d competitions. %d upcoming games got chances from them in %d competitions.%s' % (
            len(arch_['games']), add_, full_, len(arch_slugs()), mk_, nl_, (' %d competitions could not be read this time.' % err_) if err_ else '')
    except Exception as e:
        STATUS['saved season results'] = 'FAILED: %s' % e
    try:
        d_, t_, miss_, asof_, old_ = add_elo_models(fixtures, teams, leagues, built)
        STATUS['club strength ratings (ClubElo)'] = 'ok: %d of %d Europe and cup games now have chances (ratings from %s%s)' % (d_, t_, asof_, ', saved copy' if old_ else '')
        if miss_:
            STATUS['club strength ratings (ClubElo)'] += '. No rating found for: ' + ', '.join(miss_[:12]) + (' and %d more' % (len(miss_) - 12) if len(miss_) > 12 else '')
    except Exception as e:
        STATUS['club strength ratings (ClubElo)'] = 'FAILED: %s (Europe and cup games stay without chances)' % e
    if not fixtures and not teams:
        log('Nothing could be fetched. The old data file is left untouched.')
        for k_, v in STATUS.items():
            log(' ', k_, '->', v)
        sys.exit(1)
    fixtures.sort(key=lambda f: (f['ko'][:10], 0 if f['koKnown'] else 1, f['ko']))
    try:
        sq, meta_, new_, fail_ = build_squads(built)
        with open(os.path.join(DATA_DIR, 'squads.json'), 'w') as fh:
            json.dump({'asOf': NOW.strftime('%Y-%m-%dT%H:%M:00Z'), 'window': SQ_WINDOW, 'meta': meta_, 'teams': sq}, fh, separators=(',', ':'))
        STATUS['usual teams (ESPN lineups)'] = 'ok: %d teams profiled, %d new match lineups read, %d failed' % (len(sq), new_, fail_)
    except Exception as e:
        STATUS['usual teams (ESPN lineups)'] = 'FAILED: %s (the app still works without it)' % e
    for label, fn in (('weather (Open-Meteo)', add_weather), ('team news (ESPN injuries)', add_team_news)):
        try:
            a_, b_ = fn(fixtures)
            STATUS[label] = 'ok: %d matches filled, %d without' % (a_, b_) if fn is add_weather else 'ok: %d matches, %d failed' % (a_, b_)
        except Exception as e:
            STATUS[label] = 'FAILED: %s (the app still works without it)' % e
    write_basketball(os.path.join(DATA_DIR, 'basketball.json'))
    out = {'version': 3, 'asOf': NOW.strftime('%Y-%m-%dT%H:%M:00Z'), 'season': CUR, 'status': STATUS, 'notes': NOTES[:20],
           'leagues': leagues, 'teams': teams, 'fixtures': fixtures}
    with open(out_path, 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    # results for the track record page (never allowed to break the main update)
    try:
        rpath = os.path.join(DATA_DIR, 'results.json')
        prev_res = []
        if os.path.exists(rpath):
            try:
                with open(rpath) as f:
                    prev_res = json.load(f).get('results', [])
            except Exception:
                prev_res = []
        results = build_results(built, prev_res)
        with open(rpath, 'w') as f:
            json.dump({'asOf': NOW.strftime('%Y-%m-%dT%H:%M:00Z'), 'results': results}, f, separators=(',', ':'))
        STATUS['results (track record)'] = 'ok: %d finished matches' % len(results)
        out['status'] = STATUS
        with open(out_path, 'w') as f:
            json.dump(out, f, separators=(',', ':'))
    except Exception as e:
        STATUS['results (track record)'] = 'FAILED: %s' % e
        out['status'] = STATUS
        with open(out_path, 'w') as f:
            json.dump(out, f, separators=(',', ':'))
    log('Wrote %s: %d fixtures, %d teams' % (out_path, len(fixtures), len(teams)))
    for k_, v in STATUS.items():
        log(' ', k_, '->', v)
    for n in NOTES[:20]:
        log(' note:', n)


if __name__ == '__main__':
    main()

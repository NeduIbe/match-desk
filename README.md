# Naija Match Desk

A phone-first match-analysis page for football (NPFL plus top leagues, cups, Champions League) and basketball (NBA, WNBA, college).
It shows chances, goals, corners and cards, and a checklist of things that can matter. It does not place bets and it is not advice.
Only bet money you can afford to lose. 18+.

## How it works (one minute)
1. `update.py` runs 4 times a day on GitHub (see `.github/workflows/daily.yml`). It reads free public data, builds the numbers and writes files into `data/`.
2. `index.html` is the whole app. It reads the files in `data/` and runs in your phone's browser. GitHub Pages hosts it.
3. Your own record (picks, odds you took, notes) is stored on your phone only. Use **Record > Save backup file** now and then.

## What is measured and what is a guess
- **Measured from real results:** league goals, corners, cards, home advantage, how much to trust a team's record, how many recent games to use, whether home/away records should be split, club strength (ClubElo), basketball team strength.
- **Guesses from the guide (labelled in the app):** weather, pitch, travel, NPFL rules, referee moods. The Record screen checks, on your own finished picks, whether the checklist adds anything.
- If there is not enough real data for a match, the app says so and shows no chances. It does not fill the gap.

## Files
| File | What it is |
|---|---|
| `index.html` | The app. Replace this file to update the page. |
| `update.py` | The data and model builder. |
| `.github/workflows/daily.yml` | Runs the update and publishes the site. |
| `.github/workflows/check.yml` | Runs the self-checks in `tests/`. Does not touch the site. |
| `tests/test_model.py` | Self-checks using made-up numbers. |
| `data/` | Files written by `update.py`. `archive.dat` (saved finished games) is kept in the repository but not published. |
| `SETUP.md`, `START-HERE.md` | Step-by-step setup in plain words. |

## Self-checks
On GitHub: **Actions > Self-check > Run workflow**. A green tick means the maths still behaves. A red cross means do not trust the latest change.
On a computer: `python3 -m unittest discover -s tests -v`

## Known limits
- Tested here only on made-up data. The first live run is the real test; look at **Actions** for errors.
- The opponent adjustment in squad numbers still uses whole-season totals (a small leak in the squad back-test).
- Corners and cards use the same recent-games number as goals but it was not tested on its own.
- Picks are saved on the phone only when the app is opened before kick-off.

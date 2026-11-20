# JbaltzSkills

A collection of [Agent Skills](https://docs.claude.com/en/docs/claude-code/skills) for Claude Code. Each skill is a folder with a `SKILL.md` (frontmatter plus instructions) and, optionally, scripts and reference docs that Claude loads when the skill fires.

## Skills

| Skill | What it does |
|-------|--------------|
| [stock-ticker-analysis](skills/stock-ticker-analysis/SKILL.md) | Analyzes a stock ticker and writes a Markdown or JSON report in four sections: technical indicators, fundamentals from SEC EDGAR, benchmarking against the market, a sector ETF and peers, and anomaly detection. Informational only, not investment advice. |
| [example-skill](skills/example-skill/SKILL.md) | A template to copy when creating a new skill. Not meant to be used directly. |

## Installation

Skills are discovered from `~/.claude/skills/` (available in every project) or `.claude/skills/` inside a project (that project only). Copy or symlink a skill folder into one of those locations:

```bash
git clone <this-repo-url> ~/workplace/JbaltzSkills

# All projects
mkdir -p ~/.claude/skills
ln -s ~/workplace/JbaltzSkills/skills/stock-ticker-analysis ~/.claude/skills/stock-ticker-analysis

# Or a single project
mkdir -p .claude/skills
cp -R ~/workplace/JbaltzSkills/skills/stock-ticker-analysis .claude/skills/
```

Symlinking keeps the installed skill in sync with `git pull`. Start a new Claude Code session afterwards so the skill is picked up.

## Using a skill

You don't call skills by name. Claude reads each skill's `description` and loads it when a request matches. For example:

> Analyze NVDA against AMD, INTC and AVGO.
> Has TSLA had any unusual moves this quarter?

### stock-ticker-analysis

**Requirements:**
- Python 3.9+. The script uses only the standard library, so there is nothing to install.
- Outbound HTTPS to Yahoo Finance (`query1`/`query2.finance.yahoo.com`) and SEC EDGAR (`www.sec.gov`, `data.sec.gov`).
- Recommended: set a contact for SEC's User-Agent policy. SEC may return HTTP 403 without one.

  ```bash
  export SEC_USER_AGENT="Your Name you@example.com"
  ```

You can also run the script yourself:

```bash
python3 -I skills/stock-ticker-analysis/scripts/analyze_ticker.py MSFT \
  --peers AAPL,GOOGL,ORCL --out /tmp/msft.md
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--peers` | none | Comma-separated peer tickers |
| `--market` | `SPY` | Broad-market benchmark (use a local index for non-US listings, e.g. `^N225`) |
| `--sector-etf` | mapped from SIC code | Override the sector benchmark (e.g. `XLC` for GOOGL/META) |
| `--range` | `2y` | Price history: `1y`, `2y`, `5y`, `10y` |
| `--anomaly-days` | `63` | Sessions scanned for price/volume anomalies |
| `--no-fundamentals` | off | Skip SEC calls (price-only analysis) |
| `--format` | `md` | `md` or `json` |
| `--out` | stdout | Output file |

Exit codes: `0` report written, `1` no price data for the ticker, `2` bad arguments.

Formulas, thresholds, data sources and known limits are documented in [references/README.md](skills/stock-ticker-analysis/references/README.md).

## Repository layout

```
skills/
  <skill-name>/
    SKILL.md        # Frontmatter (name, description, version, tags) + instructions
    scripts/        # Executables the skill runs (optional)
    references/     # Docs Claude reads on demand (optional)
```

## Adding a skill

1. Copy `skills/example-skill/` to `skills/<your-skill-name>/` (lowercase, hyphenated).
2. In `SKILL.md`, set `name` to match the folder and write a `description` that says what the skill does **and when it should activate**. Claude uses this text to decide whether to load the skill.
3. Replace the template sections with real instructions. Keep `SKILL.md` focused and move long reference material into `references/`.
4. Put helper scripts in `scripts/` and link them from `SKILL.md` with relative paths.
5. Add the skill to the table above.

## Disclaimer

The stock-ticker-analysis skill describes public market and filing data. It does not give investment advice, and its data sources (in particular Yahoo Finance's unofficial endpoint) can change or be rate-limited without notice.

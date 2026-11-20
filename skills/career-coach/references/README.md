# Career Coach — References

Supplementary reference material for the **career-coach** skill.

## Files

| Path | Committed | Purpose |
|------|-----------|---------|
| `references/profile.example.md` | yes | Template showing the expected structure of a career profile |
| `private/` | **no** (gitignored) | All of your personal material. See the layout below |

## The private folder

```
skills/career-coach/private/
  profile.md     # your work history; the skill reads this first on every use
  resume.*       # your original resume / LinkedIn export (optional)
  jobs/          # job postings you're targeting
  drafts/        # tailored resumes, cover letters, LinkedIn text
  interviews/    # mock-interview notes, STAR stories
```

The repo's `.gitignore` excludes the whole `skills/career-coach/private/` folder, so anything you put in it stays on your machine.

## Creating your profile

Either:

1. Copy `references/profile.example.md` to `private/profile.md` and fill it in, or
2. Drop your resume or LinkedIn "Save to PDF" export into `private/` and ask Claude: *"Build my career profile from private/resume.pdf"*. Claude writes `private/profile.md` and lists anything it was unsure about.

Keep it factual and include numbers you actually know. Update `Last updated` when you change it; the skill asks to refresh the profile once it's more than 6 months old.

## Privacy

- Before making this repo public, run `git status --ignored` and confirm `private/` shows as ignored (`!!`).
- Never use `git add -f` on anything in `private/`.
- If you install the skill by copying it instead of symlinking, copy `private/` with it.

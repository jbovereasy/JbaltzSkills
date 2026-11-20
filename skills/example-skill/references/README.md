# Example Skill — References

This folder contains supplementary reference material for the **example-skill** template.

## Overview

The example-skill is a template that demonstrates the structure and components of a well-documented skill. It is not intended for direct use — copy and modify it to create new skills.

## Files

| File | Purpose |
|------|---------|
| `additional-info.md` | Template reference file showing how to structure extended documentation, background info, advanced examples, reference tables, and appendices |

## Usage

When creating a new skill from this template:

1. Copy the entire `example-skill/` directory
2. Rename the folder to your skill name
3. Update `SKILL.md` with your skill's logic and instructions
4. Replace `additional-info.md` with reference material specific to your skill
5. Add or remove files in `references/` as needed for your use case
6. Update or remove `scripts/example-script.sh` as appropriate

## Conventions

- Reference files use Markdown format
- File names should be lowercase with hyphens (e.g., `my-reference.md`)
- Each reference file should be self-contained and focused on a single topic
- Reference files are linked from `SKILL.md` using relative paths

# Commit Staged Changes

Commit the current staged changes for the hype-trading-agent demo with a proper commit message.

## Instructions

1. **Check Staged Changes**
   - Run `git status` to see staged files
   - Run `git diff --cached` to review the changes

2. **Determine Commit Type**
   - `feat:` New feature or functionality
   - `fix:` Bug fix
   - `docs:` Documentation changes
   - `refactor:` Code refactoring (no functional change)
   - `style:` Code style changes (formatting, etc.)
   - `test:` Adding or updating tests
   - `chore:` Maintenance tasks, build process, dependencies

3. **Write Commit Message**
   - Use conventional commit format: `<type>: <description>`
   - Keep description concise (50 chars or less)
   - Use imperative mood ("add" not "added" or "adds")
   - Include more details in body if needed

4. **Create the Commit**
   - Stage any additional files if needed: `git add <files>`
   - Create commit with proper message
   - Include co-author attribution if applicable

## Commit Message Template

```
<type>: <brief description (50 chars or less)>

<detailed explanation if needed (wrap at 72 chars)>

<references to issues, bugs, or related work>
```

## Examples

```
feat: add PnL overview chart to dashboard

Add new chart showing cumulative profit/loss over time with
per-trade returns breakdown.

Fixes #S8
```

```
fix: resolve whale indicator feature calculation bug

The whale indicator was using incorrect aggregation window
causing skewed feature values.

Related to BUGS_AND_FIXES.md entry BUG-003
```

```
docs: update README with new directory structure

Reflect the reorganization of code into chaining/ and
verifying/ directories.
```

```
refactor: extract data loading to separate module

Improve code organization by separating data loading logic
from model training pipeline.
```

## What This Command Does

1. Show current git status
2. Show staged changes (git diff --cached)
3. Ask user for commit type and description
4. Create commit with proper format including:
   - Conventional commit prefix
   - Clear description
   - Attribution if applicable
5. Confirm commit was created successfully

## After Commit

The commit will be created locally. To push to remote:
```bash
git push origin dev
```

To create a pull request:
```bash
gh pr create --title "<commit title>" --body "<description>"
```

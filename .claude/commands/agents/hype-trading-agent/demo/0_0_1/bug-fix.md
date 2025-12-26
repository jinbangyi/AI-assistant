# Bug Fix - Hype Trading Agent Demo

Fix identified bugs in the hype-trading-agent demo code.

## Instructions

### Prerequisite
Run `/bug-review` first to identify bugs, or provide a specific bug ID/bug description.

### Fix Process

1. **Understand the Bug**
   - Read the affected file(s)
   - Understand the expected behavior
   - Identify the root cause

2. **Create a Fix Plan**
   - Determine the minimal change needed
   - Consider side effects
   - Plan tests to verify the fix

3. **Implement the Fix**
   - Make the code changes
   - Add comments if the fix is non-obvious
   - Ensure no new bugs are introduced

4. **Document the Fix**
   - Update `fix_scripts_and_history/BUGS_AND_FIXES.md` with:
     - Bug ID and description
     - What was changed
     - Why the fix works
     - Date of fix

5. **Verify the Fix**
   - Run affected scripts to ensure they work
   - Check for regressions in related code
   - Test edge cases if applicable

## Template for BUGS_AND_FIXES.md Entry

```markdown
### BUG-XXX: [Bug Title]

**Date**: YYYY-MM-DD
**Severity**: Critical/High/Medium/Low
**Status**: Fixed

**Description**
[Brief description of the bug]

**Location**
- File: `path/to/file.py`
- Lines: X-Y

**Root Cause**
[What caused the bug]

**Fix Applied**
```python
# Before:
[old code]

# After:
[new code]
```

**Why This Fix Works**
[Explanation of why the fix resolves the issue]

**Testing**
- [ ] Tested locally
- [ ] Verified no regressions
- [ ] Edge cases tested (if applicable)
```

## Common Fix Patterns

### Missing Null Check
```python
# Before
value = data["key"]

# After
value = data.get("key")
if value is None:
    logger.warning("Missing key in data")
    return default_value
```

### Type Error
```python
# Before
result = int(timestamp)  # Fails if timestamp is None

# After
result = int(timestamp) if timestamp is not None else 0
```

### Off-by-One Error
```python
# Before
for i in range(len(items)):  # May include index out of bounds
    process(items[i])

# After
for i in range(len(items) - 1):
    process(items[i])
```

### Missing Error Handling
```python
# Before
data = fetch_from_api(url)
process(data)

# After
try:
    data = fetch_from_api(url)
    if not data:
        logger.error(f"No data from {url}")
        return
    process(data)
except Exception as e:
    logger.error(f"Failed to fetch data: {e}")
    raise
```

### Incorrect API Response
```python
# Before
return result  # May not be JSON-serializable

# After
return {
    "status": "success",
    "data": result,
    "timestamp": int(time.time())
}
```

## After Fixing

1. Update README.md if the fix changed user-facing behavior
2. Run `/update-demo-readme` if needed
3. Commit with clear message: `fix: resolve BUG-XXX [brief description]`

## Safety Checks

Before applying any fix:
- [ ] Does this break existing functionality?
- [ ] Are there related files that need updating?
- [ ] Does the configuration need changes?
- [ ] Should this fix be documented in README.md?

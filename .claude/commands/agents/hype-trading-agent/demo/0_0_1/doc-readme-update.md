# Update Demo README

Update the README.md file for the hype-trading-agent demo to reflect recent code changes, following the rules in README.AI.md.

## Instructions

1. Read `agents/hype-trading-agent/demo/0_0_1/README.AI.md` for the core rule
2. Examine recent changes to the demo code (main.py, verify_api.py, admin.html, etc.)
3. Update `agents/hype-trading-agent/demo/0_0_1/README.md` with:

### What to Check

- **Project Structure**: New files/directories (fix_scripts_and_history/, plan_history/)
- **Configuration**: CONFIG values in main.py (coins, horizons, quantiles)
- **Features**: Address and market feature counts and descriptions
- **API Endpoints**: All endpoints in verify_api.py are documented
- **Dashboard Features**: Controls and analytics in admin.html

### Update Checklist

| Section | What to Verify |
|---------|----------------|
| Project Structure | All directories and key files listed |
| Configuration | CONFIG values match main.py |
| API Endpoints | All @app.get/post routes documented |
| Model Architecture | Feature counts match _zero_features() |
| Quick Start | Instructions work with current setup |

## Core Rule from README.AI.md

> **Always update `./README.md` when making changes to the demo.**

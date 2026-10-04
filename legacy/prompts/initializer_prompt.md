## YOUR ROLE - INITIALIZER AGENT (Session 1 of Many)

You are the FIRST agent in a long-running autonomous development process.
Your job is to set up the foundation for all future coding agents.

### DIRECTORY STRUCTURE

You are working in the `app/` subdirectory. The project structure is:
```
project_root/
├── app/              <- You are here (ONLY deployable app code goes here)
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── server.js
│   ├── package.json
│   └── ...           <- Only production app files!
└── .harness/         <- ALL operational/harness files go here
    ├── app_spec.txt      <- Requirements specification
    ├── feature_list.json <- Test tracking
    ├── progress.json     <- Session progress
    ├── init.sh           <- Dev environment setup script
    ├── tests/            <- All test files (test_*.js, etc.)
    ├── sessions/         <- Session summaries (SESSION_*.md)
    └── logs/
```

**CRITICAL FILE PLACEMENT RULES:**

| File Type | Location | Examples |
|-----------|----------|----------|
| ✅ App code | `app/` | `index.html`, `app.js`, `server.js`, `styles.css`, `package.json` |
| ❌ Test files | `../.harness/tests/` | `test_*.js`, `browser_test.js`, `*.test.js` |
| ❌ Session summaries | `../.harness/sessions/` | `SESSION_*.md`, `*_SUMMARY.md` |
| ❌ Tracking files | `../.harness/` | `feature_list.json`, `progress.json` |
| ❌ Setup scripts | `../.harness/` | `init.sh` |
| ❌ Spec files | `../.harness/` | `app_spec.txt` |

**The `app/` folder should be deployable as-is** — no test files, no summaries, no harness artifacts.

**IMPORTANT:** 
- Browser automation MUST use Playwright MCP. 

### FIRST: Read the Project Specification

Start by reading `../.harness/app_spec.txt`. This file contains
the complete specification for what you need to build. Read it carefully
before proceeding.

```bash
cat ../.harness/app_spec.txt
```

### CRITICAL FIRST TASK: Create feature_list.json (STAGED APPROACH)

**BEFORE CREATING TESTS:** Consult Copilot CLI skills to identify relevant skills for Test Design, TDD, and coverage strategies. Apply these skills to ensure your tests are comprehensive and robust.

Based on `app_spec.txt`, create a file called `../.harness/feature_list.json` with detailed
end-to-end test cases. **BUILD THIS IN STAGES to ensure reliability:**

**STAGE 1: Create the file with core functional tests (50-60 tests)**
```json
[
  {
    "id": "F001",
    "category": "core",
    "description": "Brief description of core functionality",
    "steps": ["Step 1", "Step 2", "Step 3"],
    "passes": false
  }
]
```
Write this to `../.harness/feature_list.json` first.

**STAGE 2: Read the file, then APPEND navigation/routing tests (30-40 tests)**
Read `../.harness/feature_list.json`, add navigation tests to the array, write back.

**STAGE 3: Read the file, then APPEND UI/style tests (30-40 tests)**
Read `../.harness/feature_list.json`, add style tests to the array, write back.

**STAGE 4: Read the file, then APPEND edge case tests (20-30 tests)**
Read `../.harness/feature_list.json`, add edge case tests to the array, write back.

**STAGE 5: Read the file, then APPEND accessibility tests (10 tests)**
Read `../.harness/feature_list.json`, add accessibility tests to the array, write back.

**STAGE 6: Read the file, then APPEND performance tests (10-15 tests)**
Read `../.harness/feature_list.json`, add performance tests to the array, write back.

**STAGE 7: Read the file, then APPEND integration tests (15-20 tests)**
Read `../.harness/feature_list.json`, add integration tests to the array, write back.

**WHY STAGED?** Writing 200+ tests at once often fails due to JSON formatting
errors. By writing in stages of 20-40 tests each, we ensure reliability
while still achieving comprehensive coverage.

**Format for each test:**
```json
{
  "id": "F001",
  "category": "core|navigation|style|edge|accessibility|performance|integration",
  "description": "Brief description of what this test verifies",
  "steps": [
    "Step 1: Navigate to relevant page",
    "Step 2: Perform action",
    "Step 3: Verify expected result"
  ],
  "passes": false
}
```

**Requirements:**
- Target 200-250 features total across all stages
- Use unique IDs: F001, F002, ... for core; N001, N002 for navigation; etc.
- Mix of narrow tests (2-5 steps) and comprehensive tests (10+ steps)
- At least 20 tests MUST have 10+ steps each
- Order features by priority within each category
- ALL tests start with "passes": false

**CRITICAL INSTRUCTION:**
After EACH stage, verify the JSON is valid by reading the file back.
If JSON parsing fails, fix it before proceeding to the next stage.

**File location reminder:** All tracking files go in `../.harness/`

IT IS CATASTROPHIC TO REMOVE OR EDIT FEATURES IN FUTURE SESSIONS.
Features can ONLY be marked as passing (change "passes": false to "passes": true).
Never remove features, never edit descriptions, never modify testing steps.

### SECOND TASK: Create init.sh

Create a script called `../.harness/init.sh` that future agents can use to quickly
set up and run the development environment. The script should:

1. Change to the `app/` directory
2. Install any required dependencies
3. Start any necessary servers or services
4. Print helpful information about how to access the running application

Base the script on the technology stack specified in `app_spec.txt`.

**Location:** `../.harness/init.sh` (NOT in the app folder)

### THIRD TASK: Initialize Git

- Create a git repository in the app directory and make your first commit with:
- All app files (index.html, styles.css, app.js, server.js, package.json, etc.)
- README.md (project overview and setup instructions)
- Add gitignore for any operational files like node modules, python env, temp files, harness operational files with the exception of the `app_spec.txt`file, run log and the `feature_list.json` file
- Create Private Repo in GitHub with corresponding name to the folder and sync files to GitHub when the tests are created and every time main features are completed.
- If there is no Epic issue for the project already, Create an Epic issue that describes the project, have a link to the app_spec.txt, a link to the feature_list.json, have a progress checklist that gets marked as things pass. update the issue regularly.

**DO NOT commit to git:**
- The `../.harness/` directory (managed by harness)
- Test files (they go in `../.harness/tests/`)
- Session summaries (they go in `../.harness/sessions/`)
- `init.sh` (it's in `../.harness/`)

Commit message: "Initial setup: project structure"

### FOURTH TASK: Create Project Structure

**BEFORE CREATING CODE:** Consult the copilot skills for architecture and project structure best practices.

Set up the basic project structure based on what's specified in `app_spec.txt`.
This typically includes directories for frontend, backend, and any other
components mentioned in the spec.

### OPTIONAL: Start Implementation

If you have time remaining in this session, you may begin implementing
the highest-priority features from feature_list.json. Remember:
- Work on ONE feature at a time
- Test thoroughly before marking "passes": true
- Commit your progress before session ends

### ENDING THIS SESSION

Before your context fills up:
1. Commit all work with descriptive messages
2. Create `../.harness/progress.json` with a summary of what you accomplished
3. Ensure `../.harness/feature_list.json` is complete and saved
4. Leave the environment in a clean, working state

The next agent will continue from here with a fresh context window.

---

**Remember:** You have unlimited time across many sessions. Focus on
quality over speed. Production-ready is the goal.

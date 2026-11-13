# Contributing to StockRescue

Thank you for your interest in contributing to **StockRescue**! We welcome contributions from developers, researchers, and industrial supply chain engineers.

This document outlines the development workflow, code standards, testing requirements, and contribution guidelines for the project.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards & Style Guide](#coding-standards--style-guide)
- [Testing Guidelines](#testing-guidelines)
- [Commit Message Conventions](#commit-message-conventions)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Issues & Bugs](#reporting-issues--bugs)

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free experience for everyone. Contributors are expected to treat all participants with respect and professional courtesy.

---

## Getting Started

### 1. Prerequisites
- **Python:** 3.11 or 3.12 (Python 3.11 recommended)
- **Git:** 2.40+
- **Database:** SQLite (built-in) or PostgreSQL / Supabase

### 2. Fork and Clone
```bash
git clone https://github.com/vivinarya/stckresc.git
cd stckresc
```

### 3. Create a Virtual Environment
```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies
```bash
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install flake8 pytest
```

### 5. Setup Local Configuration
```bash
cp backend/.env.example backend/.env
```

---

## Development Workflow

1. **Create a topic branch:**
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. **Make your changes:**
   - Keep changes scoped to the targeted feature or bug fix.
   - Strictly adhere to the Human-in-the-Loop purchasing safeguard (`REAL_PURCHASING_ENABLED=false`).
   - Ground all inventory arithmetic and quote calculations in deterministic Python modules under `backend/tools/`.

3. **Verify locally:**
   - Run linter checks.
   - Run the full test suite.

---

## Coding Standards & Style Guide

All code in StockRescue must adhere to the following quality standards:

### 1. Python Style & PEP 8
- We enforce **Flake8** linting with a maximum line length of **120 characters** (configured to match GitHub Actions CI).
- Run linting before committing:
  ```bash
  flake8 backend/ --count --max-complexity=15 --max-line-length=120 --statistics
  ```

### 2. Typing & Data Validation
- Use explicit Python type hints throughout functions, method signatures, and class attributes.
- Use **Pydantic v2** models (`BaseModel` / `StockRescueModel`) for data validation and serialization.
- Ensure all domain models use strict timezone-aware UTC datetimes (`datetime.now(timezone.utc)`).

### 3. Architecture & Safety Rules
- **Deterministic Business Logic:** Never allow LLM hallucination for numerical balances, prices, or safety checks. All business arithmetic must reside in `backend/tools/`.
- **Zero-Idle Cloud Deployment:** Autonomous agents on Agentverse must not introduce infinite polling loops (`@agent.on_interval`). Use event-driven message handlers.
- **Human-in-the-Loop:** Automated live financial execution is permanently disallowed. All purchasing operations must generate reviewable drafts.

---

## Testing Guidelines

StockRescue maintains a rigorous automated test suite covering unit, integration, and property-based tests.

### Running Tests
From the `backend/` directory:

```bash
# Standard test run
pytest -v

# Run with CI test environment flags
APP_ENV=testing STOCKRESCUE_ENV=testing DATABASE_BACKEND=sqlite DATABASE_PATH=":memory:" REAL_TELEPHONY_ENABLED=false VOICE_PROVIDER=simulated SPECIALIST_TRANSPORT_MODE=local pytest -v
```

On Windows (PowerShell):
```powershell
$env:APP_ENV="testing"; $env:STOCKRESCUE_ENV="testing"; $env:DATABASE_BACKEND="sqlite"; $env:DATABASE_PATH=":memory:"; $env:REAL_TELEPHONY_ENABLED="false"; $env:VOICE_PROVIDER="simulated"; $env:SPECIALIST_TRANSPORT_MODE="local"; pytest -v
```

### Test Requirements for PRs
- Every new feature must be accompanied by comprehensive tests in `backend/tests/`.
- Bug fixes must include a regression test reproducing and resolving the issue.
- Ensure test execution passes with 0 failures before opening a pull request.

---

## Commit Message Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` Introduces a new feature or capability.
- `fix:` Fixes a bug or unexpected behavior.
- `docs:` Documentation improvements, typos, or guides.
- `style:` Formatting, whitespace, or linting adjustments (no production logic change).
- `refactor:` Code restructuring without behavioral changes.
- `test:` Adding, refactoring, or updating tests.
- `ci:` Changes to CI/CD workflows and deployment scripts.

**Example:**
```bash
git commit -m "docs: improve and update project documentation"
```

---

## Submitting a Pull Request

1. Push your branch to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```
2. Navigate to [https://github.com/vivinarya/stckresc](https://github.com/vivinarya/stckresc) and open a Pull Request against the `main` branch.
3. Provide a clear summary of changes, rationale, and any related issue numbers.
4. Verify that all automated CI checks pass.

---

## Reporting Issues & Bugs

If you encounter unexpected behavior or security vulnerabilities:
1. Check existing issues on GitHub to avoid duplicates.
2. Open a new issue providing:
   - Concise summary of the problem.
   - Operating system and Python version.
   - Reproduction steps and minimal test case.
   - Expected behavior vs. actual behavior.
   - Terminal logs or error tracebacks.

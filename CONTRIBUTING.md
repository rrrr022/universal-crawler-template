# Contributing to Universal Crawler Template

Thank you for your interest in contributing! 🎉

## Development Setup

1. Fork and clone repository
2. Create virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Testing tools
```

4. Run tests:
```bash
pytest tests/ -v
```

## Pull Request Process

1. Create feature branch: `git checkout -b feature/your-feature-name`
2. Make changes and add tests
3. Ensure all tests pass: `pytest tests/`
4. Update documentation if needed
5. Submit PR with clear description

## Coding Standards

- **Python**: Follow PEP 8, use type hints
- **PowerShell**: Use approved verbs, proper cmdlet naming
- **Comments**: Document complex logic, explain "why" not "what"
- **Tests**: Aim for 80%+ coverage

## Reporting Issues

Use GitHub Issues with:
- Clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version)

## Code Review

All PRs require:
- Passing CI/CD tests
- At least one approving review
- No merge conflicts
- Updated documentation

Thank you for contributing! 🚀

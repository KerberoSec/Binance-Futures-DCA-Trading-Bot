# Contributing Guidelines

Thank you for your interest in contributing to the Binance Futures DCA Trading Bot. We welcome contributions from developers and quantitative researchers.

---

## Code of Conduct

All contributors and participants are expected to adhere to our Code of Conduct. Please maintain respectful and constructive collaboration.

---

## How to Contribute

### 1. Reporting Bugs
* For general software bugs, open an issue using the Bug Report template.
* Provide complete execution logs, reproduction steps, and Python environment details.
* Never include your actual Binance API keys or secrets in logs or issues.
* For security vulnerabilities, follow the instructions in SECURITY.md rather than opening a public issue.

### 2. Suggesting Enhancements
* Open an issue using the Feature Request template to outline the proposed quantitative strategy, indicator, or risk management control.
* Detail the rationale, expected benefit, and implementation approach.

### 3. Pull Requests
* Fork the repository and create your branch from main.
* Ensure your code adheres to Python PEP 8 conventions.
* Test all calculations thoroughly to guarantee that financial order rounding and leverage constraints remain exact.
* Submit your pull request with a descriptive title and detailed summary of changes.

---

## Coding Standards

* Keep algorithmic calculations explicit and numerically verified.
* Avoid blocking operations in WebSocket callbacks.
* Enforce thread safety across shared data structures using reentrant locks.

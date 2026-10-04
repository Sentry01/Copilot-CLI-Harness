"""Test-driven autonomous development harness for GitHub Copilot.

The harness turns a PRD into a frozen, executable acceptance suite and then drives
Copilot agents to make it pass, one verified batch at a time. Agents never grade their
own work: the harness runs the tests, owns the regression baseline, and commits only
green states.
"""

__version__ = "2.0.0"

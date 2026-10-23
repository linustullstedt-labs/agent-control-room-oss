"""Agent plug-ins. Each one turns an agent's own files or commands into session rows (see base.Agent).

To support a new agent, add a module with an Agent subclass and list an instance here.
"""
from .claude_code import ClaudeCode
from .codex import Codex

AGENTS = [ClaudeCode(), Codex()]
BY_VENDOR = {a.vendor: a for a in AGENTS}

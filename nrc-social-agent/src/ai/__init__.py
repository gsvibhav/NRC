"""Claude media analysis: adapter, prompts, parsing, and persistence.

Application code must never import the `anthropic` SDK directly — only
src/ai/client.py does. Everything else in this package (or outside it)
goes through ClaudeClient / AnalysisService.
"""

"""Local-first MCP servers for files, Python scripts and LLM delegation.

Each server ships as a console script:

``mcp-filesystem``
    Encoding-aware file reads, directory listing and content search.
``mcp-python-runner``
    Local Python execution and output analysis.
``mcp-delegate``
    Task delegation to an OpenAI-compatible chat endpoint.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

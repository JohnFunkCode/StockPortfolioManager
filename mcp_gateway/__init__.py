"""MCP gateway support package (architectural standard v2 §5.5, Phase 3).

Holds the shared HTTP seam (`rest_client`) every MCP wrapper uses to reach the
FastAPI REST tier. The wrapper servers themselves remain under ``fastMCPTest/``.
"""

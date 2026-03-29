import math
from fastmcp import FastMCP

mcp = FastMCP("sqrt-server")


@mcp.tool()
def sqrt(number: float) -> float:
    """Calculate the square root of a number."""
    if number < 0:
        raise ValueError(f"Cannot compute square root of negative number: {number}")
    return math.sqrt(number)


if __name__ == "__main__":
    mcp.run()

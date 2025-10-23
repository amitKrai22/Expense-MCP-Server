from fastmcp import FastMCP
from demo_api import app # Import your fastapi app

# Convert FastAPI app to mcp
mcp = FastMCP.from_fastapi(
    app=app,
    name="Expense Tracker app"
)

if __name__ == "__main__":
    mcp.run()
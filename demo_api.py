from fastapi import FastAPI

app = FastAPI()

@app.get('/connect-mcp')
def connect_fastapi_app_mcpserver():
    return "Fastapi app connected to Fastmcp server to can app feature in clade desktop."
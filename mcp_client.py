import asyncio
import json
from typing import Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import google.generativeai as genai
import os
from dotenv import load_dotenv
load_dotenv()

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Please set GEMINI_API_KEY environment variable")

genai.configure(api_key=GEMINI_API_KEY)


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.stdio_context = None
        self.available_tools = []
        self.available_resources = []
        # Initialize Gemini model
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        
    async def connect_to_server(self, server_script_path: str):
        """Connect to the MCP server"""
        server_params = StdioServerParameters(
            command="python",
            args=[server_script_path],
            env=None
        )
        
        # Use async context manager properly
        self.stdio_context = stdio_client(server_params)
        stdio_transport = await self.stdio_context.__aenter__()
        read_stream, write_stream = stdio_transport
        
        self.session = ClientSession(read_stream, write_stream)
        await self.session.__aenter__()
        
        # Initialize and list available tools
        await self.session.initialize()
        
        # List tools
        tools_response = await self.session.list_tools()
        self.available_tools = tools_response.tools
        
        # List resources
        resources_response = await self.session.list_resources()
        self.available_resources = resources_response.resources
        
        print(f"Connected to server with {len(self.available_tools)} tools and {len(self.available_resources)} resources")
        
    async def process_query(self, query: str) -> str:
        """Process a user query using Gemini with tool calling"""
        
        # Prepare tools for Gemini
        gemini_tools = self._convert_tools_for_gemini()
        
        # Get available resources content
        resources_content = await self._get_resources_content()
        
        # Create system instruction
        system_instruction = f"""You are an expense tracker assistant. You have access to tools to manage expenses.

            Available resources:
            {resources_content}

            When users ask about expenses, use the appropriate tools to help them:
            - add_expense: Add new expenses
            - list_expenses: View expenses in a date range
            - summarize: Get expense summaries by category

            IMPORTANT: Always use dates in YYYY-MM-DD format (e.g., 2025-10-23, not 10/23/2025).
            Always provide helpful, natural responses."""
        
        # Configure model with tools
        model_with_tools = genai.GenerativeModel(
            'gemini-2.0-flash',
            tools=gemini_tools,
            system_instruction=system_instruction
        )
        
        chat = model_with_tools.start_chat(enable_automatic_function_calling=False)
        response = chat.send_message(query)
        
        # Handle tool calls
        while response.candidates[0].content.parts:
            part = response.candidates[0].content.parts[0]
            
            # Check if this is a function call
            if hasattr(part, 'function_call') and part.function_call:
                function_call = part.function_call
                function_name = function_call.name
                function_args = dict(function_call.args)
                
                print(f"\n🔧 Calling tool: {function_name}")
                print(f"   Arguments: {json.dumps(function_args, indent=2)}")
                
                # Execute the tool via MCP
                tool_result = await self.session.call_tool(
                    function_name,
                    arguments=function_args
                )
                
                print(f"   Result: {tool_result.content[0].text}")
                
                # Send the result back to Gemini
                response = chat.send_message(
                    genai.protos.Content(
                        parts=[genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=function_name,
                                response={'result': tool_result.content[0].text}
                            )
                        )]
                    )
                )
            else:
                # Regular text response
                break
        
        # Extract final text response
        final_response = response.text
        return final_response
    
    def _convert_tools_for_gemini(self):
        """Convert MCP tools to Gemini function declarations"""
        gemini_tools = []
        
        for tool in self.available_tools:
            # Build parameter schema
            properties = {}
            required = []
            
            if tool.inputSchema and 'properties' in tool.inputSchema:
                for param_name, param_info in tool.inputSchema['properties'].items():
                    properties[param_name] = {
                        'type': param_info.get('type', 'string').upper(),
                        'description': param_info.get('description', '')
                    }
                    
                if 'required' in tool.inputSchema:
                    required = tool.inputSchema['required']
            
            # Create function declaration
            function_declaration = genai.protos.FunctionDeclaration(
                name=tool.name,
                description=tool.description or "",
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties={
                        name: genai.protos.Schema(
                            type=getattr(genai.protos.Type, props['type']),
                            description=props['description']
                        )
                        for name, props in properties.items()
                    },
                    required=required
                )
            )
            
            gemini_tools.append(function_declaration)
        
        return gemini_tools
    
    async def _get_resources_content(self) -> str:
        """Fetch content from available resources"""
        resources_text = ""
        for resource in self.available_resources:
            try:
                result = await self.session.read_resource(resource.uri)
                resources_text += f"\n{resource.name}:\n{result.contents[0].text}\n"
            except Exception as e:
                print(f"Warning: Could not read resource {resource.uri}: {e}")
        return resources_text
    
    async def chat_loop(self):
        """Interactive chat loop"""
        print("\n💬 Expense Tracker Chat (type 'quit' to exit)")
        print("=" * 50)
        
        while True:
            try:
                user_input = (await asyncio.to_thread(input, "\nYou: ")).strip()
                
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                
                if not user_input:
                    continue
                
                response = await self.process_query(user_input)
                print(f"\nAssistant: {response}")
                
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}")
                import traceback
                traceback.print_exc()
    
    async def cleanup(self):
        """Cleanup resources"""
        if self.session:
            await self.session.__aexit__(None, None, None)
        if self.stdio_context:
            await self.stdio_context.__aexit__(None, None, None)


async def main():
    # Path to your MCP server script
    SERVER_SCRIPT = "expense_tracker_local_mcp_server.py"  # Update this path
    
    client = MCPClient()
    
    try:
        print("🔌 Connecting to MCP server...")
        await client.connect_to_server(SERVER_SCRIPT)
        
        print("\n✅ Connected successfully!")
        print(f"Available tools: {[t.name for t in client.available_tools]}")
        print(f"Available resources: {[r.name for r in client.available_resources]}")
        
        await client.chat_loop()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
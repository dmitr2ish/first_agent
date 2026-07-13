import json
import os
import requests

from ddgs import DDGS
from bs4 import BeautifulSoup
from openai import OpenAI

model_url = 'http://localhost:11434/v1'
api_key = 'ollama'

# TOOLS

def web_search(query: str) -> str:
    """Search in DuckDuckGo and retrieve top-3 links with description"""
    print(f"\n[TOOL] Searching in internet: '{query}'...")
    try:
        with DDGS() as duckduckgo:
            results = list(duckduckgo.text(query, max_results=2))
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"Search error: {e}"


def extract_url_text(url: str) -> str:
    """Download text from web page and truncate it to 4000 symbols"""
    print(f"\n[TOOL] Reading page: {url}...")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(separator=' ', strip=True)

        return text
    except Exception as e:
        return f"Exception reading page: {e}"


# TOOLS SPEC
tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search information in the internet. Return list of URLs and short description (snippet)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search request"}

                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_url_text",
            "description": "Download main text from the web page",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct link (URL) to the web page"
                    }
                },
                "required": ["url"]
            }
        }
    }
]

# CLIENT
client = OpenAI(base_url=model_url, api_key=api_key)

def choose_model() -> str:
    """Retrieve list of available ollama models and asking to choose one"""
    print("\n[^-^] Retrieving list of available ollama models...")
    try:
        models_response = client.models.list()
        available_models = [model.id for model in models_response.data]
        if not available_models:
            print("No models available. Run 'ollama pull <model_name>' in terminal")
            exit()
        print("\n====Available models====")
        for i, model in enumerate(available_models):
            print(f"{i+1}.{model}")
        print("="*10)

        while True:
            choice = input("\nChoose one of the available models: ")
            try:
                choice_index = int(choice) - 1
                if 0 <= choice_index < len(available_models):
                    selected_model = available_models[choice_index]
                    print(f"Chosen model: {selected_model}")
                    return selected_model
                else:
                    print("Invalid choice. Try again.")
            except ValueError:
                print("Invalid choice. Try again.")
    except Exception as e:
        print(f"Exception choosing model: {e}")
        exit()

# AGENTIC LOOP

system_prompt = {"role": "system",
                 "content":
                     """You are an expert Research Assistant. Your main goal is to provide accurate, factual, and concise answers based on real-time internet searches.

Follow this strict pipeline to answer:
1. SEARCH: ALWAYS use the `web_search` tool first to find current information. Do not rely on your internal training data.
2. READ: Use the `extract_url_text` tool on the 1-2 most promising URLs from the search results to get detailed context.
3. SYNTHESIZE: Write your final answer based ONLY on the gathered data.

CRITICAL RULES:
- NO HALLUCINATIONS: If the search tools return empty or irrelevant results, honestly state: "I couldn't find the exact information." Do not guess.
- LANGUAGE: Always answer in the exact same language the user used to ask the question (e.g., if the user asks in Russian, answer in Russian).
- CITATIONS: You MUST include inline links to your sources in the text, and a "Sources" list at the end. Format: [Website Name](URL).
- CONCISENESS: Keep the final answer short, well-structured, and strictly formatted in Markdown.
"""}


def run_agent(user_query: str, model: str):
    # AGENT MEMORY
    messages = [system_prompt, {"role": "user", "content": user_query}]

    # LOOP (Orchestrator)
    while True:
        # 1. Sending current history and tool schemas
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools_schema,
            temperature=0.1  # Low temperature makes model more logical and less creative
        )

        reply = response.choices[0].message

        # 2. Saving answer to history
        assistant_message = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tool.id,
                    "type": "function",
                    "function": {
                        "name": tool.function.name,
                        "arguments": tool.function.arguments
                    }
                } for tool in reply.tool_calls
            ]
        messages.append(assistant_message)

        # log messages
        # for message in messages: print(f"ROLE =>:{message.get('role')}\nCONTENT => {message.get('content').strip()}")

        # 3. Check condition: Does model want to call tool?
        if reply.tool_calls:
            for tool_call in reply.tool_calls:
                function_name = tool_call.function.name
                print(f"\n[TOOL] {function_name}")
                arguments = json.loads(tool_call.function.arguments)
                print(f"\args: {arguments}")

                # Router
                if function_name == "web_search":
                    result = web_search(arguments["query"])
                elif function_name == "extract_url_text":
                    result = extract_url_text(arguments["url"])
                else:
                    result = "Error: Unknown tool call"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": result
                })

            # All tools executed, go to next iteration of loop
            continue

        else:
            # Model returned final text
            print("\n" + "=" * 40)
            print("Final answer")
            print("\n" + "=" * 40)
            print(reply.content)

            # saving report
            # with open("report.md", "w", encoding="utf-8") as f:
            #     f.write(reply.content)
            # print(f"Report saved at {os.getcwd()}/report.md")

            # Exit condition
            break


# RUNNING HERE (psvm)

if __name__ == "__main__":
    selected_model = choose_model()
    print("Hello, i m a Research agent\nWrite \'exit\' to exit.")
    while True:
        try:
            print("\n" + "=" * 40)
            print("Enter your query:")
            user_query = input("> ")

            if user_query.strip().lower() == "/exit":
                print("\n" + "=" * 40)
                print("Thank you for using Research agent")
                break
            if user_query.strip().lower() == "/change_model":
                print("\n" + "=" * 40)
                print("Requested new model")
                selected_model = choose_model()
                continue
            if user_query.strip().lower() == "/current_model":
                print("\n" + "=" * 40)
                print(f"Current is model {selected_model}")
                continue


            if not user_query.strip():  # empty enter
                continue

            run_agent(user_query, selected_model)
        except Exception as e:
            print(e)

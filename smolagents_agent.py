from openai import OpenAI
from smolagents import ToolCallingAgent, WebSearchTool, VisitWebpageTool, OpenAIServerModel, FinalAnswerTool, \
    UserInputTool, tool

model_url = 'http://localhost:11434/v1'
api_key = 'ollama'

# ==========================================
# CLIENT
# ==========================================
client = OpenAI(base_url=model_url, api_key=api_key)

# ==========================================
# GLOBAL AGENTS
# ==========================================
RESEARCH_AGENT = None
MANAGER_AGENT = None


# ==========================================
# 1. MODEL CHOOSING (CLI)
# ==========================================
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
            print(f"{i + 1}.{model}")
        print("=" * 10)

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


# ==========================================
# 2. DELEGATING TOOLS
# ==========================================
@tool
def delegate_to_research_agent(query: str) -> str:
    """Useful for searching the internet, reading websites, and finding factual information.
    ALWAYS use this tool if the user asks a question about facts, news, coding documentation, or current events.

     Args:
         query (str): The detailed search or instruction for the researcher.
    """
    print(f"\n[Manager] Delegating this task to Researcher: {query}")
    result = RESEARCH_AGENT.run(query)
    print(f"\n[Manager] Retrieve result from Researcher: {result}")
    return result


# ==========================================
# INIT AGENT SYSTEM
# ==========================================

def init_multi_agent_system(model_name: str):
    global RESEARCH_AGENT, MANAGER_AGENT
    model = OpenAIServerModel(
        model_id=model_name,
        api_base=model_url,
        api_key=api_key,
    )
    # AGENT 1: Researcher
    researcher_prompt = """You are an expert Research Assistant. 
    Your ONLY goal is to search the internet, read websites, and provide a detailed factual report.
    - Always use `web_search`.
    - Always use `visit_webpage` on the best link.
    - Keep your answer factual and include markdown links to sources.
    """
    RESEARCH_AGENT = ToolCallingAgent(
        tools=[WebSearchTool(), VisitWebpageTool()],
        model=model,
        instructions=researcher_prompt,
        max_steps=4
    )
    # AGENT 2: Manager (Orchestrator)
    manager_prompt = """You are an Executive AI Manager. You interact with the user and coordinate tasks.

    Your rules:
    1. If the user asks for information, facts, or news: ALWAYS use the `delegate_to_researcher` tool.
    2. If the user's request is unclear or you need more details: ALWAYS use the `user_input` tool to ask them a clarifying question.
    3. Synthesize the final answer beautifully based on the researcher's report or your conversation.
    4. Answer in the same language as the user.
    """

    MANAGER_AGENT = ToolCallingAgent(
        tools=[delegate_to_research_agent, UserInputTool()],
        model=model,
        instructions=manager_prompt,
        max_steps=5
    )


# ==========================================
# RUNNING HERE
# ==========================================
if __name__ == "__main__":
    selected_model = choose_model()
    init_multi_agent_system(selected_model)

    print("\n" + "=" * 50)
    print("Hello, i'm your AI manager."
          ", I can answer on your questions and search the internet"
          "\nWrite \'exit\' to exit.")
    print("=" * 50)

    while True:
        try:
            print("\n" + "=" * 40)
            print("Enter your query:")
            user_query = input("> ")

            if user_query.strip().lower() == "/exit":
                print("\n" + "=" * 40)
                print("Thank you for using AI manager")
                break

            if user_query.strip().lower() == "/change_model":
                print("\n" + "=" * 40)
                print("Requested new model")
                selected_model = choose_model()
                init_multi_agent_system(selected_model)
                continue

            if user_query.strip().lower() == "/current_model":
                print("\n" + "=" * 40)
                print(f"Current is model {selected_model}")
                continue

            if not user_query.strip():  # empty enter
                continue

            print("[^-^] Agent thinking...")
            result = MANAGER_AGENT.run(user_query)
            print("\n" + "=" * 40)
            print("Final result:")
            print("=" * 40)
            print(result)

        except Exception as e:
            print(e)

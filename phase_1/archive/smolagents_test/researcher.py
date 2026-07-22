import json

from ddgs import DDGS
from openai import OpenAI
from smolagents import tool, ToolCallingAgent, OpenAIServerModel, VisitWebpageTool

model_url = 'http://localhost:11434/v1'
api_key = 'ollama'
client = OpenAI(base_url=model_url, api_key=api_key)

# GLOBAL AGENTS
RESEARCH_AGENT = None
MANAGER_AGENT = None


# CLI -> choosing model
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


# TOOL
@tool
def trusted_web_search(query: str) -> str:
    """Searches the internet, but ONLY on official US government and imigration websites (.gov).
    ALWAYS use this tool to find current US visa rules, wait times, USCIS forms, or embassy policies.

    Args:
        query (str): The search query in English.
    """

    # site:gov covers travel.state.gov, uscis.gov и cbp.gov
    strict_query = f"{query} site:gov"
    print(f"\n[Searching] Trying to find on official .gov sites: {strict_query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(strict_query, max_results=4))
        if not results:
            return "No results found on official .gov sites. Try a different or simpler query."

        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        print(f"Search error: {e}")


# INIT AGENTS
def init_visa_agent_system(model_name: str) -> ToolCallingAgent:
    """Initialize the system from Manager and Researcher agents"""
    model = OpenAIServerModel(
        model_id=model_name,
        api_base=model_url,
        api_key=api_key
    )

    # Agent 1: Researcher
    researcher_prompt = """You are an expert US Immigration Research Assistant.
    Your ONLY goal is to search official US government websites and provide a detailed factual report.
    - Always use `trusted_web_search`.
    - Always use `visit_webpage` on the best link from the search results to read the full policy.
    - Keep your answer factual and include markdown links to sources.

    CRITICAL RULE:
    1. Visa and immigration laws are strict.
    If the search results do not explicitly mention the answer on an official `.gov` site,
    state: "I dont have information from official sources." Do NOT invent legal advice.
    2. NEVER output plain text.
    You MUST always output a valid JSON tool call for `trusted_web_search`, `visit_webpage`, or `final_answer`.
    """

    researcher_agent_descr = \
        ("Useful for searching official US immigration rules, visa wait times, and USCIS policies. "
         "Call this agent with a detailed search query in English.")

    researcher_agent = ToolCallingAgent(
        tools=[trusted_web_search, VisitWebpageTool()],
        model=model,
        instructions=researcher_prompt,
        name="us_visa_researcher",
        description=researcher_agent_descr,
        max_steps=4
    )

    # Agent 2: Manager
    manager_prompt = """You are an Executive AI Manager for US Visa rules. You talk to the user and coordinate tasks.

    STRICT WORKFLOW:
    Step 1. Analyze the user's input. Is it ambiguous, too short, or missing context? (e.g., "visa" - which one? B1/B2 or F1?).
    - IF AMBIGUOUS: You MUST NOT call the researcher. You must immediately use the `final_answer` tool to ask the user a clarifying question in their language.
    - IF CLEAR: Go to Step 2.

    Step 2. If the request is clear, formulate a detailed English query and call the `us_visa_researcher` managed agent.

    Step 3. After receiving the researcher's report, use the `final_answer` tool to synthesize a detailed response for the user. Include warnings that this is not official legal counsel.

    CRITICAL RULES:
    1. ALWAYS communicate with the user in the language they used (e.g., if Russian is used, reply in Russian).
    2. NEVER guess the user's intent. If unclear, ask them!
    3. NEVER output plain text. You MUST always output a valid JSON tool call for `us_visa_researcher` or `final_answer`.
    """
    manager_agent = ToolCallingAgent(
        tools=[],
        model=model,
        instructions=manager_prompt,
        managed_agents=[researcher_agent],
        max_steps=3
    )
    return manager_agent


# RUNNING HERE
if __name__ == "__main__":
    selected_model = choose_model()
    manager = init_visa_agent_system(selected_model)

    print("\n" + "=" * 50)
    print("Hi! I'm your AI advisor for US visas")
    print("I search for information ONLY on official government websites (.gov).")
    print("Type \'/exit\' for exit the program.")
    print("=" * 50)

    while True:
        try:
            print("Enter your query:")
            user_query = input("> ")

            if user_query.strip().lower() == "/exit":
                print("\n" + "=" * 50)
                print("Thank you for using AI advisor for US visas")
                break

            if user_query.strip().lower() == "/change_model":
                print("\n" + "=" * 50)
                print("Requested new model")
                selected_model = choose_model()
                manager = init_visa_agent_system(selected_model)
                continue

            if user_query.strip().lower() == "/current_model":
                print("\n" + "=" * 50)
                print(f"Current is model {selected_model}")
                continue

            if not user_query.strip():  # empty enter
                continue

            print("[Manager] Analyzing the query...")
            result = manager.run(user_query)
            print("\n" + "=" * 50)
            print("Answer:")
            print("=" * 50)
            print(result)
        except Exception as e:
            print(f"\n [!] Error: {e}")
            print("\n" + "=" * 50)

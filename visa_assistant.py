import json
import requests
from datetime import datetime

from agno.db.sqlite import SqliteDb
from agno.tools.websearch import WebSearchTools
from agno.tools.website import WebsiteTools
from ddgs import DDGS

from agno.agent import Agent
from agno.team import Team, TeamMode
from agno.models.ollama import Ollama

from official_sources import collect_official_us_sources

DB_FILE = "visa_assistant_data.db"
SESSION_ID = "visa-cli-main"

db = SqliteDb(db_file=DB_FILE)

# ==========================================
# 1. ВЫБОР МОДЕЛИ (CLI)
# ==========================================
def choose_model() -> str:
    """Получает список доступных моделей напрямую из локального API Ollama"""
    print("\n[^-^] Получаю список доступных моделей Ollama...")
    try:
        response = requests.get('http://localhost:11434/api/tags')
        response.raise_for_status()

        models_data = response.json().get('models', [])
        available_models = [model['name'] for model in models_data]

        if not available_models:
            print("Нет доступных моделей. Выполните 'ollama pull <model_name>' в терминале.")
            exit()

        print("\n==== Доступные модели ====")
        for i, model in enumerate(available_models):
            print(f"{i + 1}. {model}")
        print("=" * 26)

        while True:
            choice = input("\nВыберите номер модели: ")
            try:
                choice_index = int(choice) - 1
                if 0 <= choice_index < len(available_models):
                    selected_model = available_models[choice_index]
                    print(f"Выбрана модель: {selected_model}")
                    return selected_model
                else:
                    print("Неверный выбор. Попробуйте еще раз.")
            except ValueError:
                print("Введите число. Попробуйте еще раз.")
    except Exception as e:
        print(f"Ошибка при подключении к Ollama: {e}")
        print("Убедитесь, что локальное приложение Ollama запущено!")
        exit()


# ==========================================
# 2. ИНСТРУМЕНТЫ (Tools)
# ==========================================
def trusted_web_search(query: str) -> str:
    """Searches the internet, but ONLY on official US government and immigration websites (.gov).
    ALWAYS use this tool to find current US visa rules, wait times, USCIS forms, or embassy policies.

    Args:
        query (str): The search query in English.
    """
    # Ограничиваем область поиска только официальными правительственными ресурсами США
    strict_query = f"{query} site:gov"
    print(f"\n[🔍 Умный Поиск] Ищу на официальных сайтах США: {strict_query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(strict_query, max_results=10))
        if not results:
            return "No results found on official .gov sites. Try a different or simpler query."

        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"Search error: {e}"


# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ ИИ-КОМАНДЫ (Agno Team)
# ==========================================
def init_visa_agent_system(model_name: str) -> Team:
    """Инициализация команды: Менеджер (Team) и Исследователь (Agent)"""

    # 1. Подключаем локальную модель через Ollama коннектор
    llm_model = Ollama(id=model_name, options={"temperature": 0.0, "num_ctx": 16384})

    # Получаем текущую дату для динамического внедрения в системный контекст
    current_time_str = datetime.now().strftime("%B %d, %Y")
    current_year = datetime.now().strftime("%Y")

    # 2. Агент-Исследователь (Выполняет поиск и готовит факты)
    researcher = Agent(
        id="us-visa-researcher",
        name="us_visa_researcher",
        role=(
            "Find and read official US government sources concerning "
            "US visas and immigration."
        ),
        model=llm_model,

        tools=[collect_official_us_sources],
        tool_choice={"type":"function", "name":"collect_official_us_sources"},
        tool_call_limit=1,

        instructions=[
            f"Today's date is {current_time_str}.",
            "First search for relevant official sources.",
            "Then read the most relevant source pages.",
            "Do not rely only on search-result snippets.",
            "Use only official US government domains.",
            "Return a structured report containing:",
            "1. Direct answer.",
            "2. Verified facts.",
            "3. Source URL for every important claim.",
            "4. Publication or update date when visible.",
            "5. Uncertainties or missing information.",
            "Never invent rules or infer a prohibition without an explicit source.",
        ],

        markdown=True,
        debug_mode=True,
        debug_level=2
    )

    # 3. Агент-Менеджер (Оркестратор и валидатор запросов)
    # Использует класс Team и координирует работу researcher
    manager = Team(
        id="visa-manager",
        name="Visa Manager",
        model=llm_model,

        members=[researcher],
        # broadcast делегирует запрос всем участникам без решения менеджера, а после этого лидер синтезирует результаты.
        mode=TeamMode.broadcast,

        db=db,
        session_id=SESSION_ID,

        # Последние сообщения получает менеджер
        add_history_to_context=True,
        num_history_runs=5,

        # Историю команды получает researcher
        add_team_history_to_members=True,
        num_team_history_runs=5,

        instructions=[
            f"Today's date is {current_time_str}.",
            "Every request received by this team is a US immigration research request.",
            "Always wait for the researcher's response before answering.",
            "Use only facts contained in the researcher's report.",
            "Never add visa rules, restrictions, dates, fees, or eligibility requirements from your own knowledge.",
            "If the researcher found no official evidence, explicitly say so.",
            "Answer in the same language as the user.",
            "Include the official source URLs used by the researcher.",
            "Add a short disclaimer that this is not legal advice.",
        ],
        markdown=True,
        debug_mode=True,
        debug_level=2
    )

    return manager


# ==========================================
# 4. ГЛАВНЫЙ ЦИКЛ ПРИЛОЖЕНИЯ
# ==========================================
if __name__ == "__main__":
    selected_model = choose_model()
    manager = init_visa_agent_system(selected_model)

    print("\n" + "=" * 50)
    print("👔 Привет! Я твой ИИ-менеджер по визам США (Powered by Agno).")
    print("Я ищу информацию ТОЛЬКО на правительственных сайтах (.gov).")
    print("Введи '/exit' для выхода.")
    print("=" * 50)

    while True:
        try:
            print("\n👤 Ваш запрос:")
            user_query = input("> ")

            if user_query.strip().lower() == "/exit":
                print("\n" + "=" * 50)
                print("До свидания!")
                break

            if user_query.strip().lower() == "/change_model":
                print("\n" + "=" * 40)
                print("Запрос на смену модели...")
                selected_model = choose_model()
                manager = init_visa_agent_system(selected_model)
                continue

            if not user_query.strip():
                continue

            print("\n[👔 Менеджер] Анализирую задачу...")

            manager.print_response(user_query,
                                   session_id=SESSION_ID,
                                   stream=True,
                                   show_member_responses=True)

        except Exception as e:
            print(f"\n[!] Ошибка в работе системы: {e}")

from pathlib import Path
from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.workspace import Workspace

folder = Path(__file__).parent

llm_model = Ollama(id="hermes3:latest")

sorting_hat = Agent(
    name="Sorting Hat",
    model=llm_model,
    tools=[Workspace(root=str(folder), allowed=["read", "list", "search", "shell"])],
    instructions=(
        "Walk the folder, figure out what's there, and propose a clean organization. "
        "Decide the categories yourself. Use shell commands when they help (e.g. `file`, "
        "`pdftotext`). Return a tidy summary, a category breakdown, and a folder tree."
    ),
    markdown=True,
)

sorting_hat.print_response(f"Inventory and organize {folder}", stream=True)

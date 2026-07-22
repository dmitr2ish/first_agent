from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.ollama import Ollama
from agno.os import AgentOS
from agno.tools.workspace import Workspace

llm_model = Ollama(id="hermes3:latest")

workbench = Agent(
    name="Workbench",
    model=llm_model,
    db=SqliteDb(db_file="workbench.db"),  # session storage
    tools=[Workspace("")],               # operate in this directory
    enable_agentic_memory=True,           # remembers across sessions
    add_history_to_context=True,          # add past runs to context
    num_history_runs=3,                   # last 3 runs
)

# Serve via AgentOS, get streaming, auth, session isolation, API endpoints
agent_os = AgentOS(agents=[workbench], tracing=True)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="workbench:app", reload=True)
## 🤖 Local Research Agent (MVP)

A lightweight, fully local AI research assistant built with Python and Ollama. This agent autonomously searches the web and reads articles to answer your questions accurately, completely offline (except for fetching web pages).

### ✨ Features

- 100% Local LLM: Powered by Ollama (using models like Hermes 2 Pro or Qwen 2.5). No API keys or cloud subscriptions required.

- Autonomous Web Search: Uses DuckDuckGo (ddgs) to find relevant, up-to-date information.

- Smart Web Scraping: Downloads and extracts clean text from URLs using BeautifulSoup.

- Context Protection: Automatically truncates long web pages to prevent LLM context overflow.

- Agentic Loop: A custom-built orchestrator that routes LLM tool calls to Python functions and manages the conversation history.

### 🛠️ Prerequisites

Before you begin, ensure you have the following installed:

- Python 3.10+

- Ollama (Download from ollama.com)

- A local model downloaded via Ollama (e.g., ollama pull hermes3:8b-llama3.1-q4_K_M or ollama pull qwen2.5:14b).

### 🚀 Getting Started

1. Clone the repository and set up a virtual environment:

```
mkdir local_research_agent
cd local_research_agent
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
```


2. Install dependencies:

```
pip install openai ddgs requests beautifulsoup4
```


3. Run the Agent:

```
python first_agent.py
```

### 💻 Usage

Once the script is running, simply type your query into the console:

Enter your query:
> What is the latest version of Java Spring Boot and what are its features?


The agent will output its thought process (calling tools) and finally generate a detailed Markdown report.

Type `exit` to quit the program.

### 📁 Project Structure

`first_agent.py:` The main application script containing the tools, schemas, and the agentic loop.
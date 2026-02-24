from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from src.agents import get_analyst_agents, get_integrator_agent, get_retriever

# Define the state for the graph
class AgentState(TypedDict):
    ticker: str
    assumptions: List[str]
    bull_argument: str
    bear_argument: str
    final_report: str

# Define the nodes for the graph
def run_bull_agent(state: AgentState):
    print("--- Running Bull Agent ---")
    ticker = state['ticker']
    assumptions = state['assumptions']
    analyst_agents = get_analyst_agents(ticker)
    argument = analyst_agents['bull_agent'](assumptions)
    return {"bull_argument": argument}

def run_bear_agent(state: AgentState):
    print("--- Running Bear Agent ---")
    ticker = state['ticker']
    assumptions = state['assumptions']
    analyst_agents = get_analyst_agents(ticker)
    argument = analyst_agents['bear_agent'](assumptions)
    return {"bear_argument": argument}

def run_integrator_agent(state: AgentState):
    print("--- Running Integrator Agent ---")
    integrator = get_integrator_agent()
    report = integrator.invoke({
        "assumptions": state['assumptions'],
        "bull_argument": state['bull_argument'],
        "bear_argument": state['bear_argument']
    })
    return {"final_report": report}

# Build the graph
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("bull_agent", run_bull_agent)
workflow.add_node("bear_agent", run_bear_agent)
workflow.add_node("integrator", run_integrator_agent)

# Define edges
workflow.add_edge("bull_agent", "integrator")
workflow.add_edge("bear_agent", "integrator")

workflow.add_conditional_edges(
    "integrator",
    lambda x: "end",
    {"end": END}
)

# Set entry point
# The entry point is a parallel execution of the bull and bear agents.
# We achieve this by setting both as the entry point. LangGraph handles the parallel execution.
workflow.set_entry_point("bull_agent")
workflow.add_edge('__start__', 'bear_agent')


# Compile the graph
app = workflow.compile()

def run_graph(ticker: str, assumptions: List[str]):
    """
    Runs the multi-agent graph for a given ticker and set of assumptions.
    """
    inputs = {
        "ticker": ticker,
        "assumptions": assumptions,
    }
    return app.invoke(inputs)

if __name__ == '__main__':
    # Example Usage:
    # Ensure you have run ingestion first (e.g., `python src/ingestion.py`)
    
    TICKER = "AAPL"
    USER_ASSUMPTIONS = [
        "iPhone sales are the primary driver of revenue.",
        "The company is facing significant competitive pressure in the smartphone market.",
        "Growth in the services division is accelerating."
    ]

    # Make sure the knowledge base exists
    try:
        get_retriever(TICKER)
        print(f"Knowledge base for {TICKER} found.")
        
        # Run the graph
        results = run_graph(TICKER, USER_ASSUMPTIONS)
        
        print("\n--- Bull Argument ---")
        print(results['bull_argument'])
        
        print("\n--- Bear Argument ---")
        print(results['bear_argument'])
        
        print("\n--- Final Report ---")
        print(results['final_report'])

    except FileNotFoundError as e:
        print(e)
        print("Please run the ingestion script first: `python src/ingestion.py`")


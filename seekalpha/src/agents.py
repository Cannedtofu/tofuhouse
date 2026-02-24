import os
from langchain.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.schema.output_parser import StrOutputParser
from langchain.schema.runnable import RunnablePassthrough

# Initialize the LLM and Embeddings
llm = ChatOpenAI(temperature=0)
embeddings = OpenAIEmbeddings()

def get_retriever(ticker: str, kb_dir: str = "data/kb"):
    """
    Get a retriever for a specific ticker.
    """
    persist_directory = os.path.join(kb_dir, ticker)
    if not os.path.exists(persist_directory):
        raise FileNotFoundError(f"Knowledge base for ticker {ticker} not found. Please run ingestion first.")
    
    vectorstore = Chroma(persist_directory=persist_directory, embedding_function=embeddings)
    return vectorstore.as_retriever()

# --- Bull Analyst Agent ---
BULL_PROMPT_TEMPLATE = """
As a 'Bull' Analyst, your task is to find evidence from the provided context that supports a positive outlook for the company.
Focus on strengths, opportunities, and positive performance indicators.
Extract and cite key data points. For each piece of evidence, you MUST cite the source document.

Context:
{context}

Key Assumptions to Validate:
{assumptions}

Based on the context, provide a concise bullish argument with citations.
Argument:
"""
BULL_PROMPT = PromptTemplate.from_template(BULL_PROMPT_TEMPLATE)

# --- Bear Analyst Agent ---
BEAR_PROMPT_TEMPLATE = """
As a 'Bear' Analyst, your task is to find evidence from the provided context that supports a negative or cautious outlook for the company.
Focus on weaknesses, threats, competitive pressures, and negative performance indicators.
Extract and cite key data points. For each piece of evidence, you MUST cite the source document.

Context:
{context}

Key Assumptions to Validate:
{assumptions}

Based on the context, provide a concise bearish argument with citations.
Argument:
"""
BEAR_PROMPT = PromptTemplate.from_template(BEAR_PROMPT_TEMPLATE)

# --- Integrator Agent ---
INTEGRATOR_PROMPT_TEMPLATE = """
You are a senior investment analyst. Your role is to synthesize the Bull and Bear arguments, compare them against the initial 'Key Assumptions', and identify key risks.

Key Assumptions:
{assumptions}

Bull Argument:
{bull_argument}

Bear Argument:
{bear_argument}

Based on the provided arguments and assumptions, perform the following:
1.  **Fact Check:** Compare the Bull and Bear findings against the Key Assumptions.
2.  **Identify Risks:** Clearly list the primary risks and discrepancies that emerge from the debate.
3.  **Suggest Mitigation/Next Steps:** For each risk, propose a mitigation strategy or a next step for further investigation.

Output:
"""
INTEGRATOR_PROMPT = PromptTemplate.from_template(INTEGRATOR_PROMPT_TEMPLATE)

def create_analyst_chain(prompt_template, llm):
    """
    Creates a chain for a financial analyst (Bull or Bear).
    """
    return (
        {
            "context": lambda x: x["retriever"].get_relevant_documents(x["assumptions"]),
            "assumptions": lambda x: x["assumptions"],
        }
        | prompt_template
        | llm
        | StrOutputParser()
    )

def get_analyst_agents(ticker: str):
    """
    Initializes and returns the Bull and Bear analyst agents for a given ticker.
    """
    retriever = get_retriever(ticker)
    
    bull_runnable = create_analyst_chain(BULL_PROMPT, llm)
    bear_runnable = create_analyst_chain(BEAR_PROMPT, llm)
    
    return {
        "bull_agent": lambda assumptions: bull_runnable.invoke({"retriever": retriever, "assumptions": assumptions}),
        "bear_agent": lambda assumptions: bear_runnable.invoke({"retriever": retriever, "assumptions": assumptions}),
    }

def get_integrator_agent():
    """
    Initializes and returns the Integrator agent.
    """
    integrator_chain = INTEGRATOR_PROMPT | llm | StrOutputParser()
    return integrator_chain

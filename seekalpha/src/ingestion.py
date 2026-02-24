import os
from langchain.vectorstores import Chroma
from langchain_community.document_loaders import DirectoryLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

def ingest_documents(ticker: str, staging_dir: str = "data/staging", kb_dir: str = "data/kb"):
    """
    Ingests documents from the staging directory into a ChromaDB vector store,
    associating them with a specific stock ticker.
    """
    loader = DirectoryLoader(staging_dir, glob="**/*.txt", show_progress=True)
    documents = loader.load()

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    splits = text_splitter.split_documents(documents)

    # Add ticker metadata to each document
    for split in splits:
        split.metadata["ticker"] = ticker

    # Define the ChromaDB persistence directory for the given ticker
    persist_directory = os.path.join(kb_dir, ticker)
    
    # Create or update the vector store
    vectorstore = Chroma.from_documents(
        documents=splits,
        embedding=OpenAIEmbeddings(),
        persist_directory=persist_directory
    )
    vectorstore.persist()
    print(f"Ingestion complete for ticker {ticker}. Knowledge base updated at {persist_directory}")

if __name__ == '__main__':
    # Example Usage:
    # 1. Place a text file (e.g., 'aapl_earnings_report.txt') in the 'data/staging' directory.
    # 2. Run this script.
    
    # Create dummy file for demonstration
    if not os.path.exists("data/staging"):
        os.makedirs("data/staging")
    with open("data/staging/dummy_report.txt", "w") as f:
        f.write("Apple Inc. reported record revenue in the last quarter, driven by strong iPhone sales.")

    ingest_documents(ticker="AAPL")

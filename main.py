import os
import chromadb
import telebot
import threading
from flask import Flask
from PyPDF2 import PdfReader
from google import genai
from google.genai import types
import chromadb.utils.embedding_functions as embedding_functions

# ---------------------------------------------------------
# 1. API KEYS & SETUP
# ---------------------------------------------------------
# Pull the real keys securely from Render's Environment Variables
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")

client = genai.Client(api_key=GEMINI_KEY)

# Tell Chroma to use Google's servers so Render doesn't run out of memory!
gemini_ef = embedding_functions.GoogleGenerativeAiEmbeddingFunction(
    api_key=GEMINI_KEY
)

# Initialize local database with the Gemini embedding function
chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(
    name="game_rules",
    embedding_function=gemini_ef
)

# ---------------------------------------------------------
# 2. DATA INGESTION: Read PDF and Chunk the Text
# ---------------------------------------------------------
def load_pdf_to_db(pdf_path: str):
    print(f"Loading and chunking {pdf_path}...")
    reader = PdfReader(pdf_path)
    
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    chunk_size = 800
    overlap = 100
    chunks = []
    
    start = 0
    while start < len(full_text):
        end = start + chunk_size
        chunks.append(full_text[start:end])
        start += chunk_size - overlap 
        
    if chunks:
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        collection.add(
            documents=chunks,
            ids=ids
        )
    print(f"Successfully loaded {len(chunks)} chunks into the database!\n")

existing_ids = collection.get()['ids']
if existing_ids:
    collection.delete(ids=existing_ids)
load_pdf_to_db("OneNightUltimateWerewolf-rules.pdf")

# ---------------------------------------------------------
# 3. THE TOOL: How the AI searches the database
# ---------------------------------------------------------
def search_game_rules(query: str) -> str:
    """Searches the official game rules database for answers."""
    print(f"\n[Agent is searching database for: '{query}']")
    
    results = collection.query(
        query_texts=[query], 
        n_results=3 
    )
    
    if results['documents'] and results['documents'][0]:
        combined_results = "\n\n---NEXT EXCERPT---\n\n".join(results['documents'][0])
        return combined_results
        
    return "No relevant rules found."

# ---------------------------------------------------------
# 4. THE AGENT: Connecting the LLM to the Tool
# ---------------------------------------------------------
system_prompt = (
    "You are Paul a helpful game referee. Always use the search_game_rules tool to answer questions. "
    "CRITICAL: If the user asks a vague follow-up question, "
    "use the context of the conversation history to write a highly specific, complete search query. "
    "Please format your answers cleanly using Markdown. Use **bold text** for important roles or rules, and bullet points for lists."
)

chat = client.chats.create(
    model="gemini-2.5-flash",
    config=types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[search_game_rules],
        temperature=0.2, 
    )
)

# ---------------------------------------------------------
# 5. THE PROTOTYPE LOOP
# ---------------------------------------------------------
bot = telebot.TeleBot(TG_TOKEN)

print("Connecting to Telegram...")

@bot.message_handler(func=lambda message: True)
def handle_telegram_message(message):
    print(f"\n[!] RECEIVED MESSAGE: {message.text}") # <--- Tracer 1
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        print("[!] Asking Gemini...") # <--- Tracer 2
        response = chat.send_message(message.text)
        
        print("[!] Gemini replied! Sending to Telegram...") # <--- Tracer 3
        bot.reply_to(message, response.text)
        print("[!] Success!")
        
    except Exception as e:
        bot.reply_to(message, "Sorry, the referee encountered an error reading the rules.")
        print(f"Error Caught: {e}")

# ---------------------------------------------------------
# 6. CLOUD DEPLOYMENT: Keep-Alive Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Referee Bot is awake and monitoring the game!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

server_thread = threading.Thread(target=run_server)
server_thread.start()

bot.infinity_polling(skip_pending=True)

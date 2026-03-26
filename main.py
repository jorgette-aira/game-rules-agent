import os
import chromadb
import telebot
import threading
from flask import Flask
from PyPDF2 import PdfReader
from google import genai
from google.genai import types

# Setting up API Key
os.environ["GEMINI_API_KEY"] = "YOUR_GEMINI_KEY"
client = genai.Client()

# Initialize local database
chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(name="game_rules")

# ---------------------------------------------------------
# 2. DATA INGESTION: Read PDF and Chunk the Text
# ---------------------------------------------------------
def load_pdf_to_db(pdf_path: str):
    print(f"Loading and chunking {pdf_path}...")
    reader = PdfReader(pdf_path)
    
    # 1. Extract ALL text from the PDF into one giant string
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    # 2. Slice the text into chunks
    chunk_size = 800
    overlap = 100
    chunks = []
    
    start = 0
    while start < len(full_text):
        end = start + chunk_size
        chunks.append(full_text[start:end])
        start += chunk_size - overlap # Move forward, but leave an overlap
        
    # 3. Add chunks to the database
    if chunks:
        # Generate a unique ID for every chunk (chunk_0, chunk_1, etc.)
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        collection.add(
            documents=chunks,
            ids=ids
        )
    print(f"Successfully loaded {len(chunks)} chunks into the database!\n")

# Important: We need to clear the old "page" data before loading the new chunks.
# Add this line right above load_pdf_to_db("rules.pdf")
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
        n_results=3 # Grab the top 3 chunks instead of 1
    )
    
    if results['documents'] and results['documents'][0]:
        # Stitch the 3 chunks together with a divider so the AI can read them all
        combined_results = "\n\n---NEXT EXCERPT---\n\n".join(results['documents'][0])
        return combined_results
        
    return "No relevant rules found."

# ---------------------------------------------------------
# ---------------------------------------------------------
# 4. THE AGENT: Connecting the LLM to the Tool
# ---------------------------------------------------------
# We update the system instruction to explicitly tell the agent to use its memory
system_prompt = (
    "You are Paul a helpful game referee. Always use the search_game_rules tool to answer questions. "
    "CRITICAL: If the user asks a vague follow-up question (e.g., 'What about round 2?'), "
    "use the context of the conversation history to write a highly specific, complete search query "
    "for the tool (e.g., 'rolling a 7 in round 2 rules'). "
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
# 5. THE PROTOTYPE LOOP: Talk to your Agent (Now with Streaming!)
# ---------------------------------------------------------
TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
bot = telebot.TeleBot(TELEGRAM_TOKEN)

print("Connecting to Telegram...")

# This decorator catches every text message sent to your bot
@bot.message_handler(func=lambda message: True)
def handle_telegram_message(message):
    
    # Show the "typing..." status indicator in the Telegram app
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # Pass the Telegram message into our existing RAG Agent
        response = chat.send_message(message.text)
        
        # Send the agent's answer back to the Telegram chat using Markdown
        bot.reply_to(message, response.text, parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, "Sorry, the referee encountered an error reading the rules.")
        print(f"Error: {e}")

# ---------------------------------------------------------
# 6. CLOUD DEPLOYMENT: Keep-Alive Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Referee Bot is awake and monitoring the game!"

def run_server():
    # Bind to the port Render gives us
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Start the web server in a background thread
server_thread = threading.Thread(target=run_server)
server_thread.start()

# Keep the Telegram polling loop running on the main thread
bot.infinity_polling()
    